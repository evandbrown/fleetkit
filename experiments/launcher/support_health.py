#!/usr/bin/env python3
"""The support host's own account of its fixture, for the driver on the worker host.

    python3.12 support_health.py [--port 8082] [--metrics /var/lib/fleetkit/support-metrics.csv]

Runs on the support host (the launcher starts it over SSM as the transient unit
fleetkit-support-health). Once a second it fetches the fixture's home page from the support
host itself, and it reads the support host's 1 Hz metrics (images/support/support-metrics.py).
After every trial the driver asks about the trial's window:

    GET /window?from=<unix s>&to=<unix s>  ->  {"problems": [...], "fixture_cpu_max_pct", "probe", ...}

A problem means the fixture was unhealthy or overloaded during the window, measured here and not
from the worker host, whose own load is what the experiment measures. The driver then treats the
trial as not a result and runs it again (harness/driver/driver/clean.py). The two rules:

* the fixture container used at least FIXTURE_CPU_BUSY_PCT of its one CPU in some second;
* the fixture didn't answer its own host, or took longer than PROBE_SLOW_MS to.

GET /health answers {"ok": true}. Standard library only.
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIXTURE_CPU_BUSY_PCT = 90.0   # the fixture is pinned to one CPU; at this it is queueing requests
PROBE_SLOW_MS = 1000.0        # a static page from its own host should take a few milliseconds
PROBE_TIMEOUT_S = 2.0
KEEP_S = 4 * 3600             # longest window kept in memory
FIXTURE = "fleetkit-fixture"


def evaluate(rows, probes, t0: float, t1: float) -> dict:
    """The window [t0, t1]'s figures and problems.

    ``rows``: (ts, subject, metric, value) from support-metrics.csv, in time order.
    ``probes``: (ts, ok, ms) from the fixture probe, in time order."""
    usage = [(ts, v) for ts, subj, m, v in rows if subj == FIXTURE and m == "cpu_usage_usec"]
    busy = []
    for (ta, ua), (tb, ub) in zip(usage, usage[1:]):
        if tb > t0 and ta < t1 and tb > ta and ub >= ua:
            busy.append(100.0 * (ub - ua) / ((tb - ta) * 1e6))
    host_cpu = [v for ts, subj, m, v in rows if subj == "support" and m == "cpu_util" and t0 <= ts <= t1 + 1]
    window = [(ts, ok, ms) for ts, ok, ms in probes if t0 <= ts <= t1 + 1]
    failed = sum(1 for _, ok, _ in window if not ok)
    slowest = max((ms for _, ok, ms in window if ok), default=None)
    problems = []
    if busy and max(busy) >= FIXTURE_CPU_BUSY_PCT:
        problems.append(f"the fixture used {max(busy):.0f}% of its CPU")
    if failed:
        problems.append(f"the fixture didn't answer its own host {failed} of {len(window)} times")
    if slowest is not None and slowest > PROBE_SLOW_MS:
        problems.append(f"the fixture took {slowest:.0f} ms to answer its own host")
    return {"from": t0, "to": t1, "problems": problems,
            "fixture_cpu_max_pct": round(max(busy), 1) if busy else None,
            "support_cpu_util_max": max(host_cpu) if host_cpu else None,
            "probe": {"count": len(window), "failed": failed,
                      "max_ms": round(slowest, 1) if slowest is not None else None}}


class Monitor:
    def __init__(self, metrics_path: str, fixture_url: str):
        self.metrics_path = metrics_path
        self.fixture_url = fixture_url
        self.rows: collections.deque = collections.deque()
        self.probes: collections.deque = collections.deque()
        self.offset = 0
        self.header = None
        self.lock = threading.Lock()

    def probe_forever(self, stop: threading.Event) -> None:
        nxt = time.monotonic()
        while not stop.is_set():
            ts, t = time.time(), time.monotonic()
            try:
                with urllib.request.urlopen(self.fixture_url, timeout=PROBE_TIMEOUT_S) as r:
                    r.read()
                    ok = 200 <= r.status < 300
            except Exception:
                ok = False
            ms = (time.monotonic() - t) * 1000.0
            with self.lock:
                self.probes.append((ts, ok, ms))
                while self.probes and self.probes[0][0] < ts - KEEP_S:
                    self.probes.popleft()
            nxt += 1.0
            stop.wait(max(0.0, nxt - time.monotonic()))

    def _read_new_rows(self) -> None:
        """Rows appended to support-metrics.csv since the last read (whole lines only)."""
        try:
            with open(self.metrics_path, "rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
        except OSError:
            return
        end = data.rfind(b"\n") + 1
        if end <= 0:
            return
        self.offset += end
        for rec in csv.reader(io.StringIO(data[:end].decode("utf-8", "replace"))):
            if self.header is None:
                self.header = rec
                continue
            try:
                ts, subj, metric, value = float(rec[0]), rec[1], rec[2], float(rec[3])
            except (IndexError, ValueError):
                continue
            self.rows.append((ts, subj, metric, value))
        cutoff = (self.rows[-1][0] if self.rows else 0) - KEEP_S
        while self.rows and self.rows[0][0] < cutoff:
            self.rows.popleft()

    def window(self, t0: float, t1: float) -> dict:
        with self.lock:
            self._read_new_rows()
            rows, probes = list(self.rows), list(self.probes)
        return evaluate(rows, probes, t0, t1)


def make_handler(mon: Monitor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/health":
                return self._send(200, {"ok": True})
            if url.path == "/window":
                q = urllib.parse.parse_qs(url.query)
                try:
                    t0, t1 = float(q["from"][0]), float(q["to"][0])
                except (KeyError, ValueError):
                    return self._send(400, {"error": "from and to are Unix seconds"})
                return self._send(200, mon.window(t0, t1))
            self._send(404, {"error": "not found"})

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--metrics", default="/var/lib/fleetkit/support-metrics.csv")
    ap.add_argument("--fixture-url", default="http://127.0.0.1:8081/index.html")
    a = ap.parse_args(argv)
    mon = Monitor(a.metrics, a.fixture_url)
    stop = threading.Event()
    threading.Thread(target=mon.probe_forever, args=(stop,), daemon=True).start()
    httpd = ThreadingHTTPServer((a.bind, a.port), make_handler(mon))
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
