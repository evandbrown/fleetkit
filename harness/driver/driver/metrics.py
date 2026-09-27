"""Host metrics sampling: GET /host/metrics at 1 Hz into host_metrics.csv (long format).

Nulls (PSI and steal where the platform has none) are skipped rather than written as empty cells.
"""
from __future__ import annotations

import threading
import time

from .hostclient import HostClient
from .outputs import CsvAppender

HOST_SCALARS = ("mem_total", "mem_available", "cpu_util", "steal")
SESSION_SCALARS = ("rss_bytes", "cgroup_memory_current", "cgroup_memory_peak", "cpu_usage_usec")


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


class HostMetricsSampler:
    def __init__(self, client: HostClient, writer: CsvAppender, interval_s: float = 1.0, log=None):
        self.client = client
        self.writer = writer
        self.interval_s = interval_s
        self.log = log
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="host-metrics", daemon=True)
        self.samples = 0
        self.errors = 0

    def start(self) -> "HostMetricsSampler":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(self.interval_s * 3 + 2)

    def _run(self) -> None:
        next_at = time.monotonic()
        while not self._stop.is_set():
            try:
                sample = self.client.host_metrics(timeout_s=max(0.5, self.interval_s - 0.1))
                ts = sample.get("ts") if isinstance(sample, dict) else None
                if not _num(ts):
                    ts = time.time()
                for sid, metric, value in flatten(sample):
                    self.writer.append({"ts": ts, "session_id": sid, "metric": metric, "value": value})
                self.samples += 1
            except Exception as exc:
                self.errors += 1
                if self.log and self.errors == 1:
                    self.log.warn(f"host metrics sampling failed: {exc}")
            next_at += self.interval_s
            delay = next_at - time.monotonic()
            if delay < 0:
                next_at = time.monotonic()
                delay = 0
            if self._stop.wait(delay):
                break
