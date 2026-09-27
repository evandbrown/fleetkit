"""Host metrics sampling: GET /host/metrics at ``--metrics-hz`` (default 1 Hz) into host_metrics.csv
(long format).

Nulls (PSI and steal where the platform has none, the Firecracker-only per-session CPU fields on
docker) are skipped rather than written as empty cells. The host daemon samples on its own period
(``--metrics-period``) and serves its latest sample, so a response whose ``ts`` equals the previous
one is a repeat and is not written again.

Each tick also writes the driver's own ``driver_cpu_usec`` / ``driver_rss_bytes`` under session_id
``driver``, and, unless disabled, a separate thread times one HTTP GET of the fixture at 1 Hz and
writes ``fixture_rtt_ms`` under session_id ``fixture`` (a failed probe writes no row and is counted).
"""
from __future__ import annotations

import collections
import os
import sys
import threading
import time
import urllib.request

from .config import FIXTURE_PROBE_INTERVAL_S, FIXTURE_PROBE_TIMEOUT_S
from .hostclient import HostClient
from .outputs import CsvAppender

HOST_SCALARS = ("mem_total", "mem_available", "cpu_util", "steal", "cpu_count", "hostd_cpu_usec", "hostd_rss_bytes")
SESSION_SCALARS = ("rss_bytes", "cgroup_memory_current", "cgroup_memory_peak", "cpu_usage_usec",
                   "cpu_vcpu_usec", "cpu_vmm_usec", "cpu_throttled_usec", "cpu_nr_throttled",
                   "cpu_pressure_some_total_us", "cpu_pressure_full_total_us", "memory_pressure_some_total_us")


def flatten(sample: dict) -> list[tuple[str, str, float]]:
    """-> [(session_id or 'host', metric, value)] for one /host/metrics response."""
    rows = []
    if not isinstance(sample, dict):
        return rows
    for k in HOST_SCALARS:
        v = sample.get(k)
        if _num(v):
            rows.append(("host", k, v))
    psi = sample.get("psi") or {}
    if isinstance(psi, dict):
        for res, vals in psi.items():
            if not isinstance(vals, dict):
                continue
            for key, v in vals.items():
                if _num(v):
                    rows.append(("host", f"psi_{res}_{key}", v))
    for s in sample.get("sessions") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        for k in SESSION_SCALARS:
            v = s.get(k)
            if _num(v):
                rows.append((str(s["id"]), k, v))
    return rows


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def driver_self_metrics(proc_root: str = "/proc") -> dict:
    """The driver's cumulative CPU (utime+stime, all threads) in µs and its resident set in bytes.
    RSS is current RSS from <proc_root>/self/statm on Linux; elsewhere the peak RSS from getrusage."""
    out = {"driver_cpu_usec": None, "driver_rss_bytes": None}
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        out["driver_cpu_usec"] = int((ru.ru_utime + ru.ru_stime) * 1e6)
        peak = int(ru.ru_maxrss) * (1 if sys.platform == "darwin" else 1024)
    except Exception:
        peak = None
    try:
        with open(os.path.join(proc_root, "self", "statm"), encoding="ascii") as fh:
            out["driver_rss_bytes"] = int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        out["driver_rss_bytes"] = peak
    return out


class HostMetricsSampler:
    def __init__(self, client: HostClient, writer: CsvAppender, interval_s: float = 1.0, log=None,
                 fixture_url: str | None = None, fixture_interval_s: float = FIXTURE_PROBE_INTERVAL_S,
                 self_metrics: bool = True):
        self.client = client
        self.writer = writer
        self.interval_s = interval_s
        self.log = log
        self.fixture_url = fixture_url.rstrip("/") + "/" if fixture_url else None
        self.fixture_interval_s = fixture_interval_s
        self.self_metrics = self_metrics
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="host-metrics", daemon=True)
        self._probe = (threading.Thread(target=self._run_probe, name="fixture-probe", daemon=True)
                       if self.fixture_url else None)
        self._last_ts = None
        self._recent_lock = threading.Lock()
        # (driver receive time, cpu_util) for --settle-s; a few minutes at 5 Hz
        self._recent: collections.deque = collections.deque(maxlen=4096)
        self.samples = 0
        self.errors = 0
        self.repeats = 0
        self.probes = 0
        self.probe_errors = 0

    def start(self) -> "HostMetricsSampler":
        self._thread.start()
        if self._probe:
            self._probe.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(self.interval_s * 3 + 2)
        if self._probe:
            self._probe.join(FIXTURE_PROBE_TIMEOUT_S + 2)

    def cpu_util_between(self, t0: float, t1: float) -> list[float]:
        """cpu_util values of the samples received between t0 and t1 (driver clock)."""
        with self._recent_lock:
            return [v for ts, v in self._recent if t0 <= ts <= t1]

    def tick(self) -> None:
        """One sampling tick: the host daemon's latest sample (unless it is a repeat) and the driver's own figures."""
        try:
            sample = self.client.host_metrics(timeout_s=max(0.5, self.interval_s - 0.1))
            ts = sample.get("ts") if isinstance(sample, dict) else None
            if _num(ts) and ts == self._last_ts:
                self.repeats += 1  # the daemon has not taken a new sample since the last tick
            else:
                self._last_ts = ts if _num(ts) else None
                if not _num(ts):
                    ts = time.time()
                self.writer.append_many([{"ts": ts, "session_id": sid, "metric": metric, "value": value}
                                         for sid, metric, value in flatten(sample)])
                if isinstance(sample, dict) and _num(sample.get("cpu_util")):
                    with self._recent_lock:
                        self._recent.append((time.time(), sample["cpu_util"]))
                self.samples += 1
        except Exception as exc:
            self.errors += 1
            if self.log and self.errors == 1:
                self.log.warn(f"host metrics sampling failed: {exc}")
        if self.self_metrics:
            now = time.time()
            self.writer.append_many([{"ts": now, "session_id": "driver", "metric": k, "value": v}
                                     for k, v in driver_self_metrics().items() if _num(v)])

    def _run(self) -> None:
        next_at = time.monotonic()
        while not self._stop.is_set():
            self.tick()
            next_at += self.interval_s
            delay = next_at - time.monotonic()
            if delay < 0:
                next_at = time.monotonic()
                delay = 0
            if self._stop.wait(delay):
                break

    def _run_probe(self) -> None:
        next_at = time.monotonic()
        while not self._stop.is_set():
            ts = time.time()
            t0 = time.monotonic()
            try:
                req = urllib.request.Request(self.fixture_url, headers={"Accept": "*/*"})
                with urllib.request.urlopen(req, timeout=FIXTURE_PROBE_TIMEOUT_S) as resp:
                    resp.read()
                    ok = 200 <= resp.status < 300
                if not ok:
                    raise RuntimeError(f"HTTP {resp.status}")
                self.writer.append({"ts": ts, "session_id": "fixture", "metric": "fixture_rtt_ms",
                                    "value": round((time.monotonic() - t0) * 1000.0, 3)})
                self.probes += 1
            except Exception as exc:
                self.probe_errors += 1
                if self.log and self.probe_errors == 1:
                    self.log.warn(f"fixture probe {self.fixture_url} failed: {exc}")
            next_at += self.fixture_interval_s
            delay = next_at - time.monotonic()
            if delay < 0:
                next_at = time.monotonic()
                delay = 0
            if self._stop.wait(delay):
                break
