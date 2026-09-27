"""Host metrics sampler: GET /host/metrics shape, sampled every `period` seconds into gauges.

Linux reads /proc directly (meminfo, stat, pressure/*). macOS has no PSI and no
steal; memory and CPU come from psutil when it is installed, else null. Per-
session figures come from the backend (Engine API stats or the VM's cgroup).
The sample also carries the daemon's own cost (hostd_cpu_usec, hostd_rss_bytes)
so a reader can subtract it; the period is --metrics-period (default 1 s).
"""
from __future__ import annotations

import os
import platform
import threading
import time
from typing import Any, Dict, List, Optional

from .backends.base import SESSION_FIGURES
from .model import Defaults, State
from .procfs import PROC_ROOT, parse_pressure, read_text, status_rss_bytes

try:  # optional, only needed off-Linux
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


def read_meminfo(proc_root: str = PROC_ROOT) -> Dict[str, Optional[int]]:
    text = read_text(os.path.join(proc_root, "meminfo"))
    if text is None:
        if psutil is not None:
            vm = psutil.virtual_memory()
            return {"mem_total": int(vm.total), "mem_available": int(vm.available)}
        return {"mem_total": None, "mem_available": None}
    vals: Dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            vals[key] = int(parts[0]) * 1024
    return {"mem_total": vals.get("MemTotal"), "mem_available": vals.get("MemAvailable")}


def read_cpu_counters(proc_root: str = PROC_ROOT) -> Optional[Dict[str, int]]:
    text = read_text(os.path.join(proc_root, "stat"))
    if not text:
        return None
    first = text.splitlines()[0].split()
    if first[0] != "cpu":
        return None
    nums = [int(x) for x in first[1:]]
    while len(nums) < 8:
        nums.append(0)
    user, nice, system, idle, iowait, irq, softirq, steal = nums[:8]
    return {"idle": idle + iowait, "steal": steal, "total": sum(nums[:8])}


def read_psi(proc_root: str = PROC_ROOT) -> Optional[Dict[str, Dict[str, Optional[float]]]]:
    out: Dict[str, Dict[str, Optional[float]]] = {}
    any_found = False
    for res in ("cpu", "memory", "io"):
        text = read_text(os.path.join(proc_root, "pressure", res))
        if text:
            any_found = True
        out[res] = parse_pressure(text)
    return out if any_found else None


def hostd_cpu_usec() -> int:
    """This process's cumulative user+system CPU time (all threads), microseconds."""
    t = os.times()
    return int((t.user + t.system) * 1_000_000)


def hostd_rss_bytes(proc_root: str = PROC_ROOT) -> Optional[int]:
    """This process's resident set: VmRSS on Linux, psutil elsewhere, else None."""
    rss = status_rss_bytes(proc_root, "self")
    if rss is not None:
        return rss
    if psutil is not None:
        try:
            return int(psutil.Process().memory_info().rss)
        except Exception:
            pass
    return None


def _drop_none(attrs: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in attrs.items() if v is not None and v != ""}


class HostSampler:
    """Samples host and per-session metrics every `period` seconds on a daemon thread."""

    def __init__(self, manager: Any, telemetry: Any, period: float = Defaults.METRICS_PERIOD_S,
                 host_id: str = "", proc_root: str = PROC_ROOT):
        self.manager = manager
        self.telemetry = telemetry
        self.period = period
        self.host_id = host_id
        self.proc_root = proc_root
        self.latest: Optional[Dict[str, Any]] = None
        self._prev_cpu: Optional[Dict[str, int]] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="host-metrics", daemon=True)
        self.samples = 0
        if psutil is not None and platform.system() != "Linux":
            try:
                psutil.cpu_percent(interval=None)  # prime the delta
            except Exception:
                pass

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _cpu(self) -> Dict[str, Optional[float]]:
        counters = read_cpu_counters(self.proc_root)
        if counters is None:
            if psutil is not None:
                try:
                    return {"cpu_util": float(psutil.cpu_percent(interval=None)), "steal": None}
                except Exception:
                    pass
            return {"cpu_util": None, "steal": None}
        prev, self._prev_cpu = self._prev_cpu, counters
        if prev is None:
            return {"cpu_util": None, "steal": None}
        total = counters["total"] - prev["total"]
        if total <= 0:
            return {"cpu_util": None, "steal": None}
        idle = counters["idle"] - prev["idle"]
        steal = counters["steal"] - prev["steal"]
        return {"cpu_util": round(100.0 * (1 - idle / total), 2), "steal": round(100.0 * steal / total, 2)}

    def sample_once(self) -> Dict[str, Any]:
        ts = time.time()
        sample: Dict[str, Any] = {"ts": ts}
        sample.update(read_meminfo(self.proc_root))
        sample.update(self._cpu())
        sample["psi"] = read_psi(self.proc_root)
        sample["cpu_count"] = os.cpu_count()
        sample["hostd_cpu_usec"] = hostd_cpu_usec()
        sample["hostd_rss_bytes"] = hostd_rss_bytes(self.proc_root)
        sessions: List[Dict[str, Any]] = []
        # Correlation keys per session (section 8) for the exported points; kept out of the
        # GET /host/metrics shape, which section 4 fixes to {id, rss_bytes, ...}.
        keys: Dict[str, Dict[str, Any]] = {}
        for s in self.manager.live_sessions():
            try:
                figures = self.manager.backend.sample(s)
            except Exception:
                figures = {}
            # Every key of SESSION_FIGURES on every session, whatever the backend returned.
            sessions.append({"id": s.id, **{k: figures.get(k) for k in SESSION_FIGURES}})
            keys[s.id] = {"fleetkit.run_id": s.run_id, "fleetkit.trial_id": s.trial_id,
                          "fleetkit.backend": s.backend}
        sample["sessions"] = sessions
        self.latest = sample
        self.samples += 1
        self._export(sample, keys)
        return sample

    def _export(self, sample: Dict[str, Any], session_keys: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        """Gauges with the section 8 correlation keys: fleetkit.host_id and fleetkit.backend on host
        points; those plus fleetkit.session_id, run_id and trial_id on per-session points."""
        if self.telemetry is None:
            return
        backend = getattr(getattr(self.manager, "backend", None), "name", None)
        host_attrs = _drop_none({"fleetkit.host_id": self.host_id, "fleetkit.backend": backend})
        points = [{"name": "fleetkit.host.%s" % k, "value": sample.get(k), "attributes": host_attrs}
                  for k in ("mem_total", "mem_available", "cpu_util", "steal", "cpu_count",
                            "hostd_cpu_usec", "hostd_rss_bytes")]
        psi = sample.get("psi") or {}
        for res, entry in psi.items():
            for k, v in entry.items():
                points.append({"name": "fleetkit.host.psi.%s.%s" % (res, k), "value": v, "attributes": host_attrs})
        for s in sample["sessions"]:
            attrs = _drop_none({**host_attrs, **(session_keys or {}).get(s["id"], {}),
                                "fleetkit.session_id": s["id"]})
            for k in SESSION_FIGURES:
                points.append({"name": "fleetkit.session.%s" % k, "value": s.get(k), "attributes": attrs})
        self.telemetry.gauges([p for p in points if p["value"] is not None], ts_ns=int(sample["ts"] * 1e9))

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self.sample_once()
            except Exception as e:  # never let the sampler die
                if self.telemetry is not None:
                    self.telemetry.log("host metrics sample failed: %s" % e, severity="WARN")
            elapsed = time.monotonic() - t0
            self._stop.wait(max(0.0, self.period - elapsed))
