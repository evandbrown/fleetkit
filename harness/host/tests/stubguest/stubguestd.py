"""A stub guest daemon: answers /health, /task, /metrics and /logs with the section 4 shapes.

It runs no Chromium. It exists so the host daemon's docker backend and proxy
logic can be proven before the real guest image exists, and as an in-process
server for the host daemon's tests. Faults from FLEETKIT_FAULT behave like the
real guest is specified to:
  crash_on_start   exit 1 immediately
  never_ready      /health stays 503
  hang_task        /task never answers
  hang_step        a step never settles; the guest's own step deadline fires -> step_timeout
  slow_step:<ms>   one step takes <ms>; step_timeout if that is over step_timeout_ms
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

STEPS = ["home", "search", "open_product", "add_to_cart", "verify_cart"]
STUB_VERSION = "stub-0"
# What the real guest reports as chromium_flags and kernel_cmdline; the values only need the right types.
STUB_FLAGS = ["--headless=new", "--no-sandbox", "--remote-debugging-port=9222", "--user-data-dir=/tmp/chromium"]
STUB_CMDLINE = "console=ttyS0 reboot=k panic=1 pci=off ip=10.200.0.10::10.200.0.1:255.255.255.0:vm0:eth0:off"
# 1x1 white JPEG so screenshot_b64 is a decodable image.
TINY_JPEG_B64 = ("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////"
                 "////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")


class StubState:
    def __init__(self, fault: Optional[str] = None, ready_after_s: float = 0.0, step_ms: float = 20.0,
                 kernel_boot_s: float = 0.4, chromium_launch_s: float = 0.0):
        self.fault = fault or None
        self.started = time.monotonic()
        self.ready_at = self.started + ready_after_s
        # Boot phases as the real guest reports them: the kernel ran kernel_boot_s before the
        # daemon started; Chromium launched chromium_launch_s and was ready ready_after_s into
        # the daemon's uptime (guestd-uptime seconds).
        self.kernel_boot_s = kernel_boot_s
        self.chromium_launch_s = min(chromium_launch_s, ready_after_s)
        self.chromium_ready_s = ready_after_s
        self.step_ms = step_ms
        self.lock = threading.Lock()
        self.busy = False
        self.tasks_run = 0
        self.tasks_failed = 0
        self.logs: List[Dict[str, Any]] = []
        self.seq = 0
        self.log("stub guest started fault=%s" % self.fault)

    def log(self, msg: str, severity: str = "INFO") -> None:
        with self.lock:
            self.seq += 1
            self.logs.append({"seq": self.seq, "ts": time.time(), "severity": severity, "msg": msg})
            del self.logs[:-200]

    def ready(self) -> bool:
        if self.fault == "never_ready":
            return False
        return time.monotonic() >= self.ready_at

    def health(self) -> Dict[str, Any]:
        """The ready /health body: section 4's fields plus the guest's self-description."""
        uptime = time.monotonic() - self.started
        return {"ready": True, "chromium_version": "stub-0", "uptime_s": uptime,
                "guestd_version": STUB_VERSION, "guestd_uptime_s": uptime,
                "kernel_uptime_s": uptime + self.kernel_boot_s,
                "chromium_launch_s": self.chromium_launch_s, "chromium_ready_s": self.chromium_ready_s,
                "chromium_flags": list(STUB_FLAGS), "kernel_cmdline": STUB_CMDLINE,
                "vcpus": os.cpu_count(), "mem_total": 2 ** 31}

    def run_task(self, req: Dict[str, Any], traceparent: Optional[str]) -> Dict[str, Any]:
        t_recv = time.monotonic_ns()
        guest_clock_ns = time.time_ns()
        step_timeout_ms = float(req.get("step_timeout_ms", 10000))
        task_timeout_ms = float(req.get("task_timeout_ms", 45000))
        steps: List[Dict[str, Any]] = []
        ok, category, failed_step, error = True, "ok", None, None
        slow_ms = None
        fault_name, _, arg = (self.fault or "").partition(":")
        if fault_name == "slow_step":
            slow_ms = float(arg or 0)
        for i, name in enumerate(STEPS):
            dispatch = time.monotonic_ns() - t_recv
            dur = self.step_ms
            if i == 1 and fault_name == "hang_step":
                dur = None                      # never settles
            elif i == 1 and slow_ms is not None:
                dur = slow_ms
            if dur is None or dur > step_timeout_ms:
                time.sleep(step_timeout_ms / 1000.0)
                ok, category, failed_step = False, "step_timeout", name
                error = "step %s did not settle within %d ms" % (name, step_timeout_ms)
                steps.append({"name": name, "dispatch_ns": dispatch, "settle_ns": None,
                              "duration_ms": step_timeout_ms})
                break
            time.sleep(dur / 1000.0)
            settle = time.monotonic_ns() - t_recv
            steps.append({"name": name, "dispatch_ns": dispatch, "settle_ns": settle,
                          "duration_ms": (settle - dispatch) / 1e6})
            if (settle / 1e6) > task_timeout_ms:
                ok, category, failed_step, error = False, "task_timeout", name, "task exceeded %d ms" % task_timeout_ms
                break
        task_ms = (time.monotonic_ns() - t_recv) / 1e6
        with self.lock:
            self.tasks_run += 1
            if not ok:
                self.tasks_failed += 1
        self.log("task %s %s" % (req.get("task_id"), category), "INFO" if ok else "WARN")
        return {"ok": ok, "failure_category": category, "failed_step": failed_step, "error": error,
                "steps": steps, "task_ms": task_ms, "bytes_received": 4096 * len(steps),
                "request_count": 3 * len(steps), "screenshot_b64": TINY_JPEG_B64 if ok else "",
                "guest_clock_ns": guest_clock_ns, "traceparent": traceparent,
                "log_tail": [dict(l) for l in self.logs[-5:]], "task_id": req.get("task_id"),
                "product_id": req.get("product_id"), "stub": True}


def make_handler(state: StubState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a: Any) -> None:
            pass

        def _send(self, status: int, body: Any) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/health":
                if state.ready():
                    self._send(200, state.health())
                else:
                    self._send(503, {"ready": False, "error": "chromium not up"})
            elif url.path == "/metrics":
                self._send(200, {"mem_total": 2 ** 31, "mem_available": 2 ** 30, "cached": 2 ** 20,
                                 "chromium_rss": 150 * 2 ** 20, "tasks_run": state.tasks_run,
                                 "tasks_failed": state.tasks_failed})
            elif url.path == "/logs":
                since = int(parse_qs(url.query).get("since", ["0"])[0])
                self._send(200, {"lines": [l for l in state.logs if l["seq"] > since]})
            else:
                self._send(404, {"error": "no route"})

        def do_POST(self) -> None:
            if self.path != "/task":
                self._send(404, {"error": "no route"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length) or b"{}")
            if state.fault == "hang_task":
                state.log("hang_task: never answering", "WARN")
                threading.Event().wait()        # never answers; the host proxy deadline fires
            with state.lock:
                if state.busy:
                    self._send(409, {"ok": False, "error": "a task is already running"})
                    return
                state.busy = True
            try:
                self._send(200, state.run_task(req, self.headers.get("traceparent")))
            finally:
                with state.lock:
                    state.busy = False

    return Handler


class StubGuest:
    """In-process server for tests: StubGuest(fault=...).start() -> address."""

    def __init__(self, fault: Optional[str] = None, port: int = 0, bind: str = "127.0.0.1", **kw: Any):
        self.state = StubState(fault=fault, **kw)
        self.httpd = ThreadingHTTPServer((bind, port), make_handler(self.state))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.address = "%s:%d" % (bind, self.port)

    def start(self) -> "StubGuest":
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def main() -> int:
    fault = os.environ.get("FLEETKIT_FAULT") or None
    if fault == "crash_on_start":
        sys.stderr.write("stub guest: crash_on_start\n")
        return 1
    port = int(os.environ.get("PORT", "8080"))
    g = StubGuest(fault=fault, port=port, bind="0.0.0.0",
                  ready_after_s=float(os.environ.get("STUB_READY_AFTER_S", "0.5")))
    sys.stderr.write("stub guest listening on %s fault=%s fixture=%s\n" % (
        g.address, fault, os.environ.get("FLEETKIT_FIXTURE_URL")))
    sys.stderr.flush()
    g.httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
