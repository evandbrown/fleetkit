"""Host metrics sampler: GET /host/metrics shape, sampled every second into gauges.

Linux reads /proc directly (meminfo, stat, pressure/*). macOS has no PSI and no
steal; memory and CPU come from psutil when it is installed, else null. Per-
session figures come from the backend (Engine API stats or the VM's cgroup).
"""
from __future__ import annotations

import os
import platform
import threading
import time
from typing import Any, Dict, List, Optional

from .model import Defaults, State

try:  # optional, only needed off-Linux
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def read_meminfo() -> Dict[str, Optional[int]]:
    text = _read("/proc/meminfo")
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


def read_cpu_counters() -> Optional[Dict[str, int]]:
    text = _read("/proc/stat")
    if text is None:
        return None
    first = text.splitlines()[0].split()
    if first[0] != "cpu":
        return None
    nums = [int(x) for x in first[1:]]
    while len(nums) < 8:
        nums.append(0)
    user, nice, system, idle, iowait, irq, softirq, steal = nums[:8]
    return {"idle": idle + iowait, "steal": steal, "total": sum(nums[:8])}


def read_psi() -> Optional[Dict[str, Dict[str, Optional[float]]]]:
    out: Dict[str, Dict[str, Optional[float]]] = {}
    any_found = False
    for res in ("cpu", "memory", "io"):
        text = _read("/proc/pressure/%s" % res)
        entry: Dict[str, Optional[float]] = {"some_avg10": None, "some_total": None, "full_avg10": None, "full_total": None}
        if text:
            any_found = True
            for line in text.splitlines():
                parts = line.split()
                if not parts:
                    continue
                kind = parts[0]
                kv = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
                if kind in ("some", "full"):
                    entry[kind + "_avg10"] = float(kv.get("avg10", "nan")) if "avg10" in kv else None
                    entry[kind + "_total"] = float(kv["total"]) if "total" in kv else None
        out[res] = entry
    return out if any_found else None


def _drop_none(attrs: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in attrs.items() if v is not None and v != ""}


class HostSampler:
    """Samples host and per-session metrics every `period` seconds on a daemon thread."""

    def __init__(self, manager: Any, telemetry: Any, period: float = Defaults.METRICS_PERIOD_S,
                 host_id: str = ""):
        self.manager = manager
        self.telemetry = telemetry
        self.period = period
        self.host_id = host_id
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
        counters = read_cpu_counters()
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
        sample.update(read_meminfo())
        sample.update(self._cpu())
        sample["psi"] = read_psi()
        sessions: List[Dict[str, Any]] = []
        # Correlation keys per session (section 8) for the exported points; kept out of the
        # GET /host/metrics shape, which section 4 fixes to {id, rss_bytes, ...}.
        keys: Dict[str, Dict[str, Any]] = {}
        for s in self.manager.live_sessions():
            try:
                figures = self.manager.backend.sample(s)
            except Exception:
                figures = {"rss_bytes": None, "cgroup_memory_current": None,
                           "cgroup_memory_peak": None, "cpu_usage_usec": None}
            sessions.append({"id": s.id, **figures})
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
                  for k in ("mem_total", "mem_available", "cpu_util", "steal")]
        psi = sample.get("psi") or {}
        for res, entry in psi.items():
            for k, v in entry.items():
                points.append({"name": "fleetkit.host.psi.%s.%s" % (res, k), "value": v, "attributes": host_attrs})
        for s in sample["sessions"]:
            attrs = _drop_none({**host_attrs, **(session_keys or {}).get(s["id"], {}),
                                "fleetkit.session_id": s["id"]})
            for k in ("rss_bytes", "cgroup_memory_current", "cgroup_memory_peak", "cpu_usage_usec"):
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
