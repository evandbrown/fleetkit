"""A stub host daemon for driver tests: implements the hostd API of design section 4 with a fake
session model, the five faults, the idle and lifetime reapers, verify-clean, host metrics, and its
own spans.jsonl/logs.jsonl (services ``hostd`` and ``guest-daemon``, log records in the shape of
hostd/telemetry.py) plus sessions/<id>/console.log, so the trace assertion and the bundle inventory
can be exercised offline. Like the real guest, a failed task's reply carries only the steps
completed so far. No Docker, no Chromium.

Capacity-experiment fields (contract section 1 and 2): ``GET /host/info``; boot phases and
``guest_info`` on the session record once ready; ``cpu_count``/``hostd_*`` and, for sessions created
with backend ``firecracker``, the per-VM CPU/throttle/pressure counters on ``/host/metrics``; per-step
``bytes_received``/``request_count``, ``proc_samples``, ``timing_valid``, ``guestd_cpu_ms`` and
``step_screenshots`` on the task result. ``step_ms_per_n`` makes each step take longer with the
number of live sessions, so a ladder finds a limit. Also runnable by hand::

    python3 -m tests.stub_hostd --port 8090 --telemetry-dir results/hostd

Timings scale with the request's timeouts, so tests pass small values.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STEP_NAMES = ("home", "search", "open_product", "add_to_cart", "verify_cart")
# a 1x1 JPEG
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8Q"
    "EBEQCgwSExIQEw8QEBD/yQALCAABAAEBAREA/8wABgAQEAX/2gAIAQEAAD8A0s8g/9k=")


class StubOptions:
    def __init__(self, **kw):
        self.startup_ms = kw.get("startup_ms", 120)  # container-ish startup
        self.step_ms = kw.get("step_ms", 20)  # per-step duration for ok tasks
        self.proxy_margin_ms = kw.get("proxy_margin_ms", 5000)  # design: task_timeout_ms + 5000
        self.leak_after_trial = kw.get("leak_after_trial", False)  # verify-clean reports leftovers
        self.telemetry_dir = kw.get("telemetry_dir")  # writes spans.jsonl / logs.jsonl / hostd.log
        self.reaper_interval_s = kw.get("reaper_interval_s", 0.1)
        self.host_id = kw.get("host_id", "stub-host")
        self.jitter_ms = kw.get("jitter_ms", 10)
        self.crash_slots = set(kw.get("crash_slots", ()))  # slots that crash on start regardless of fault
        self.step_ms_per_n = kw.get("step_ms_per_n", 0)  # extra ms per step per live session beyond the first
        self.host_info = kw.get("host_info", True)  # False: GET /host/info answers 404 (an older daemon)
        self.ec2 = kw.get("ec2")  # the /host/info ec2 object (None off EC2)
        self.cpu_count = kw.get("cpu_count", 16)


class Session:
    def __init__(self, sid: str, slot: int, spec: dict, opts: StubOptions):
        self.id = sid
        self.slot = slot
        self.spec = spec
        self.backend = spec.get("backend", "docker")
        self.address = f"127.0.0.1:{18080 + slot}"
        self.state = "creating"
        self.created_ts = time.time()
        self.process_started_ts = None
        self.ready_ts = None
        self.destroyed_ts = None
        self.last_activity_ts = self.created_ts
        self.startup_ms = None
        self.cleanup_ms = None
        self.outcome = None
        self.error = None
        self.fault = spec.get("fault") or None
        self.lock = threading.Lock()
        self.task_lock = threading.Lock()
        self.tasks_run = 0
        self.tasks_failed = 0
        self.container_present = False
        self.boot: dict = {"kernel_start_ts": None, "guestd_start_ts": None, "chromium_launch_ts": None,
                           "chromium_ready_ts": None, "guest_info": None}

    def view(self) -> dict:
        return {"id": self.id, "state": self.state, "backend": self.backend, "slot": self.slot,
                "address": self.address, "created_ts": self.created_ts,
                "process_started_ts": self.process_started_ts, "ready_ts": self.ready_ts,
                "destroyed_ts": self.destroyed_ts, "last_activity_ts": self.last_activity_ts,
                "startup_ms": self.startup_ms, "cleanup_ms": self.cleanup_ms, "outcome": self.outcome,
                "error": self.error, **self.boot}


class StubHost:
    def __init__(self, opts: StubOptions):
        self.o = opts
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.slots_in_use: set[int] = set()
        self.transitions: list[dict] = []
        self.leaked: list[str] = []
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap, daemon=True)
        self._reaper.start()
        self.tel = _Telemetry(opts.telemetry_dir) if opts.telemetry_dir else None
        self.started = time.time()
        self.task_payloads: list[dict] = []

    # ---- lifecycle -----------------------------------------------------------------------
    def create(self, spec: dict, traceparent: str | None, run_id: str | None) -> list[dict]:
        out = []
        with self.lock:
            for _ in range(int(spec.get("count", 1))):
                slot = next(s for s in range(0, 120) if s not in self.slots_in_use)
                self.slots_in_use.add(slot)
                sid = f"sess-{uuid.uuid4().hex[:8]}"
                s = Session(sid, slot, spec, self.o)
                self.sessions[sid] = s
                out.append({"id": sid, "slot": slot, "address": s.address})
                threading.Thread(target=self._boot, args=(s, traceparent, run_id), daemon=True).start()
        return out

    def _transition(self, s: Session, to: str, outcome=None, traceparent=None, run_id=None):
        frm = s.state
        s.state = to
        if outcome and not s.outcome:
            s.outcome = outcome
        rec = {"session_id": s.id, "from": frm, "to": to, "ts": time.time(), "outcome": s.outcome}
        self.transitions.append(rec)
        if self.tel:
            self.tel.log("hostd", "session.state", rec, traceparent, run_id)

    def _boot(self, s: Session, traceparent, run_id):
        t0 = time.time()
        delay = int(s.spec.get("launch_interval_ms", 0) or 0) * s.slot / 1000.0
        if delay:
            time.sleep(delay)
        span_start = time.time_ns()
        time.sleep(0.02)  # "host setup": tap / port allocation
        s.process_started_ts = time.time()
        s.container_present = True
        if self.tel:
            self.tel.console(s.id, f"[stub] container for slot {s.slot} started (fault={s.fault or 'none'})")
        self._transition(s, "booting", traceparent=traceparent, run_id=run_id)
        if s.fault == "crash_on_start" or s.slot in self.o.crash_slots:
            time.sleep(0.05)
            s.container_present = False
            s.error = "process exited during boot (stub crash_on_start)"
            self._transition(s, "failed", "startup_error", traceparent, run_id)
            self._teardown(s, traceparent, run_id)
            return
        if s.fault == "never_ready":
            deadline = t0 + float(s.spec.get("ready_timeout_s", 60))
            while time.time() < deadline and s.state == "booting":
                time.sleep(0.05)
            if s.state == "booting":
                s.error = f"not ready within ready_timeout_s={s.spec.get('ready_timeout_s')}"
                self._transition(s, "failed", "startup_timeout", traceparent, run_id)
                self._teardown(s, traceparent, run_id)
            return
        time.sleep((self.o.startup_ms + random.uniform(0, self.o.jitter_ms)) / 1000.0)
        with s.lock:
            if s.state != "booting":
                return
            s.ready_ts = time.time()
            s.startup_ms = (s.ready_ts - s.created_ts) * 1000.0
            s.last_activity_ts = s.ready_ts
            p0 = s.process_started_ts
            s.boot = {"kernel_start_ts": p0 + 0.002, "guestd_start_ts": p0 + 0.004,
                      "chromium_launch_ts": p0 + 0.006, "chromium_ready_ts": s.ready_ts - 0.001,
                      "guest_info": {"guestd_version": "stub", "chromium_version": "154.0.0.0-stub",
                                     "chromium_flags": ["--headless=new", "--remote-debugging-port=9222"],
                                     "kernel_cmdline": "console=ttyS0 reboot=k panic=1 fleetkit.stub=1",
                                     "vcpus": int(s.spec.get("vcpus", 2)),
                                     "mem_total": int(s.spec.get("mem_mib", 2048)) * 1024 * 1024}}
            self._transition(s, "ready", traceparent=traceparent, run_id=run_id)
        if self.tel:
            self.tel.span("hostd", "session.create", traceparent, span_start, time.time_ns(),
                          {"fleetkit.session_id": s.id, "fleetkit.run_id": run_id or ""})

    def get(self, sid: str):
        return self.sessions.get(sid)

    def destroy(self, sid: str, outcome=None, traceparent=None, run_id=None) -> bool:
        s = self.sessions.get(sid)
        if not s:
            return False
        with s.lock:
            if s.state in ("destroying", "destroyed"):
                return True
            if s.state == "failed" and s.destroyed_ts is not None:
                return True
            self._transition(s, "destroying", outcome or "completed", traceparent, run_id)
        self._teardown(s, traceparent, run_id)
        return True

    def _teardown(self, s: Session, traceparent=None, run_id=None):
        t0 = time.time()
        time.sleep(0.03 + random.uniform(0, self.o.jitter_ms) / 1000.0)  # docker rm -f
        if self.tel:
            self.tel.console(s.id, f"[stub] container for slot {s.slot} removed (outcome={s.outcome})")
        s.container_present = False
        with self.lock:
            self.slots_in_use.discard(s.slot)
            if self.o.leak_after_trial:
                self.leaked.append(f"container fleetkit-session-{s.slot} (fleetkit.role=session)")
        s.destroyed_ts = time.time()
        s.cleanup_ms = (s.destroyed_ts - t0) * 1000.0
        if s.state != "failed":
            self._transition(s, "destroyed", traceparent=traceparent, run_id=run_id)

    def _reap(self):
        while not self._stop.wait(self.o.reaper_interval_s):
            now = time.time()
            for s in list(self.sessions.values()):
                if s.state not in ("ready", "busy"):
                    continue
                if now - s.created_ts > float(s.spec.get("max_lifetime_s", 600)):
                    self.destroy(s.id, "lifetime_expired")
                elif s.state == "ready" and now - s.last_activity_ts > float(s.spec.get("idle_timeout_s", 120)):
                    self.destroy(s.id, "idle_expired")

    # ---- task proxy ------------------------------------------------------------------------
    def task(self, sid: str, payload: dict, traceparent, run_id) -> tuple[int, dict]:
        s = self.sessions.get(sid)
        if not s:
            return 404, {"ok": False, "failure_category": "session_not_ready", "error": "no such session"}
        with s.lock:
            if s.state != "ready":
                return 409, {"ok": False, "failure_category": "session_not_ready",
                             "error": f"session is {s.state}"}
            s.state = "busy"
            s.last_activity_ts = time.time()
        send_ts = time.time()
        rtt_ns = 1_000_000  # a real host daemon measures this with a small probe, not the task itself
        status, body = self._guest(s, payload, traceparent, run_id)
        with s.lock:
            s.last_activity_ts = time.time()
            s.tasks_run += 1
            if not body.get("ok"):
                s.tasks_failed += 1
            if body.get("failure_category") == "guest_unreachable":
                # proxy deadline fired: destroy with task_failure_destroyed
                if s.state == "busy":
                    s.state = "ready"
                destroy = True
            else:
                destroy = False
                if s.state == "busy":
                    s.state = "ready"
        if destroy:
            self.destroy(sid, "task_failure_destroyed", traceparent, run_id)
        if "guest_clock_ns" in body:
            body["clock_offset_ns"] = int(send_ts * 1e9) + rtt_ns // 2 - body["guest_clock_ns"]
        body["guest_mem_available"] = 1_500_000_000 - s.slot * 1000
        body["chromium_rss"] = 250_000_000 + s.slot * 1000
        return status, body

    def _guest(self, s: Session, payload: dict, traceparent, run_id) -> tuple[int, dict]:
        self.task_payloads.append(dict(payload))
        step_timeout = int(payload.get("step_timeout_ms", 10000))
        task_timeout = int(payload.get("task_timeout_ms", 45000))
        interval_ms = int(payload.get("sample_interval_ms", 200))
        shots = bool(payload.get("screenshot_each_step"))
        live = sum(1 for x in list(self.sessions.values()) if x.state in ("ready", "busy"))
        step_ms = self.o.step_ms + self.o.step_ms_per_n * max(0, live - 1)
        guest_clock_ns = time.time_ns()
        t0 = time.monotonic_ns()
        fault = s.fault or ""
        if fault == "hang_task":
            time.sleep((task_timeout + self.o.proxy_margin_ms) / 1000.0)  # proxy deadline
            return 504, {"ok": False, "failure_category": "guest_unreachable",
                         "error": "proxy deadline exceeded (stub hang_task)", "task_id": payload.get("task_id")}
        steps = []
        step_shots: list[dict] = []
        failed_step = None
        error = ""
        category = "ok"
        for i, name in enumerate(STEP_NAMES):
            d0 = time.monotonic_ns() - t0
            if i == 1 and (fault == "hang_step" or fault.startswith("slow_step")):
                time.sleep(step_timeout / 1000.0)
                # like guestd: the step that timed out never settled, so it gets no step record
                failed_step, category, error = name, "step_timeout", f"step {name} exceeded {step_timeout} ms ({fault})"
                break
            time.sleep((step_ms + random.uniform(0, self.o.jitter_ms)) / 1000.0)
            d1 = time.monotonic_ns() - t0
            steps.append({"name": name, "dispatch_ns": d0, "settle_ns": d1, "duration_ms": (d1 - d0) / 1e6,
                          "bytes_received": STEP_BYTES[i] + (s.slot * 10 if i == 0 else 0),
                          "request_count": STEP_REQUESTS[i]})
            if shots:
                step_shots.append({"step": name, "b64": base64.b64encode(TINY_JPEG).decode()})
        task_ns = time.monotonic_ns() - t0
        task_ms = task_ns / 1e6
        if self.tel:
            self.tel.span("guest-daemon", "task", traceparent, guest_clock_ns, time.time_ns(),
                          {"fleetkit.task_id": payload.get("task_id", ""), "fleetkit.run_id": run_id or ""})
            for st in steps:
                self.tel.span("guest-daemon", f"step.{st['name']}", traceparent, guest_clock_ns + st["dispatch_ns"],
                              guest_clock_ns + st["settle_ns"], {"fleetkit.task_id": payload.get("task_id", "")})
            self.tel.span("hostd", "task.proxy", traceparent, guest_clock_ns - 1000, time.time_ns() + 1000,
                          {"fleetkit.task_id": payload.get("task_id", ""), "fleetkit.session_id": s.id,
                           "fleetkit.run_id": run_id or ""})
            self.tel.log("hostd", "task proxied", {"task_id": payload.get("task_id"), "session_id": s.id,
                                                    "failure_category": category}, traceparent, run_id)
        body = {"ok": category == "ok", "failure_category": category, "steps": steps, "task_ms": task_ms,
                "bytes_received": sum(st["bytes_received"] for st in steps),
                "request_count": sum(st["request_count"] for st in steps), "guest_clock_ns": guest_clock_ns,
                "traceparent": traceparent or "", "task_id": payload.get("task_id"),
                "log_tail": [{"ts": time.time(), "level": "INFO", "msg": f"task {payload.get('task_id')} {category}"}],
                "screenshot_b64": base64.b64encode(TINY_JPEG).decode(),
                "sample_interval_ms": interval_ms, "proc_samples": _proc_samples(task_ns, interval_ms),
                "guestd_cpu_ms": round(task_ms * 0.02, 3), "timing_valid": not shots}
        if shots:
            body["step_screenshots"] = step_shots
        if category != "ok":
            body.update({"failed_step": failed_step, "error": error})
        return 200, body

    # ---- host ------------------------------------------------------------------------------
    def metrics(self) -> dict:
        now = time.time()
        up_us = (now - self.started) * 1e6
        live = [s for s in list(self.sessions.values()) if s.state in ("ready", "busy", "booting")]
        busy = sum(1 for s in live if s.state == "busy")
        return {"ts": now, "mem_total": 8_000_000_000, "mem_available": 5_000_000_000,
                "cpu_util": round(5.0 + 10.0 * busy + random.uniform(0, 1), 3), "steal": None,
                "cpu_count": self.o.cpu_count, "hostd_cpu_usec": int(up_us * 0.02), "hostd_rss_bytes": 60_000_000,
                "psi": {"cpu": {"some_avg10": 0.1, "some_total": int(up_us * 0.01), "full_avg10": None, "full_total": None},
                        "memory": {"some_avg10": 0.0, "some_total": 0, "full_avg10": 0.0, "full_total": 0},
                        "io": {"some_avg10": 0.0, "some_total": 0, "full_avg10": 0.0, "full_total": 0}},
                "sessions": [{"id": s.id, "rss_bytes": 240_000_000, "cgroup_memory_current": 300_000_000,
                              "cgroup_memory_peak": 310_000_000, "cpu_usage_usec": 123456, **_vm_counters(s, now)}
                             for s in live]}

    def info(self) -> dict:
        return {"host_id": self.o.host_id, "backend": "docker", "hostd_version": "stub", "kernel_release": "stub-kernel",
                "cpu_model": "Stub CPU @ 3.00GHz", "cpu_count": self.o.cpu_count, "threads_per_core": 1,
                "cores_per_socket": self.o.cpu_count, "sockets": 1, "mem_total": 8_000_000_000,
                "virtualized": True, "kvm": False, "ec2": self.o.ec2, "metrics_period_s": 1.0, "firecracker": None}

    def verify_clean(self) -> dict:
        leftovers = [f"container for session {s.id}" for s in self.sessions.values() if s.container_present]
        leftovers += self.leaked
        return {"clean": not leftovers, "leftovers": leftovers}

    def stop(self):
        self._stop.set()
        if self.tel:
            self.tel.close()


STEP_BYTES = (20_000, 9_000, 10_000, 2_000, 7_000)  # sums to 48,000 (plus slot * 10 on home)
STEP_REQUESTS = (4, 2, 3, 1, 2)  # sums to 12
GROUP_SHARES = {"renderer": 0.5, "browser": 0.2, "gpu": 0.1, "network": 0.05, "guestd": 0.05, "other": 0.1}


def _proc_samples(task_ns: int, interval_ms: int) -> list[dict]:
    """Samples every interval_ms over the task, plus one at its end: 2 vCPUs, 75% busy."""
    if interval_ms <= 0:
        return []
    step = interval_ms * 1_000_000
    points = list(range(step, task_ns, step)) + [task_ns]
    out, prev = [], 0
    for i, t in enumerate(points):
        wall_ms = (t - prev) / 1e6
        busy = wall_ms * 2 * 0.75
        out.append({"t_ns": t, "cpu_total_ms": busy, "cpu_idle_ms": wall_ms * 2 - busy,
                    "mem_available": 1_500_000_000 - i * 1_000_000, "psi_cpu_some_total_us": 1000 * (i + 1),
                    "groups": {g: {"cpu_ms": busy * sh, "rss_bytes": int(100_000_000 * sh), "procs": 1}
                               for g, sh in GROUP_SHARES.items()}})
        prev = t
    return out


def _vm_counters(s: "Session", now: float) -> dict:
    """Per-VM counters hostd reports on the firecracker backend (null elsewhere)."""
    keys = ("cpu_vcpu_usec", "cpu_vmm_usec", "cpu_throttled_usec", "cpu_nr_throttled", "cpu_pressure_some_total_us",
            "cpu_pressure_full_total_us", "memory_pressure_some_total_us")
    if s.backend != "firecracker" or not s.process_started_ts:
        return dict.fromkeys(keys)
    up = (now - s.process_started_ts) * 1e6
    return {"cpu_vcpu_usec": int(up * 0.8), "cpu_vmm_usec": int(up * 0.05), "cpu_throttled_usec": int(up * 0.01),
            "cpu_nr_throttled": int(up / 100_000), "cpu_pressure_some_total_us": int(up * 0.02),
            "cpu_pressure_full_total_us": int(up * 0.01), "memory_pressure_some_total_us": 0}


class _Telemetry:
    def __init__(self, d: str):
        self.dir = Path(d)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.spans = open(self.dir / "spans.jsonl", "a", encoding="utf-8")
        self.logs = open(self.dir / "logs.jsonl", "a", encoding="utf-8")
        self.text = open(self.dir / "hostd.log", "a", encoding="utf-8")

    @staticmethod
    def _ids(traceparent):
        try:
            _, tid, pid, _ = traceparent.split("-")
            return tid, pid
        except Exception:
            return os.urandom(16).hex(), ""

    def span(self, service, name, traceparent, start_ns, end_ns, attrs):
        tid, pid = self._ids(traceparent)
        rec = {"service": service, "trace_id": tid, "span_id": os.urandom(8).hex(), "parent_span_id": pid,
               "name": name, "start_ns": start_ns, "end_ns": end_ns, "status": "ok",
               "attributes": {"service.name": service, **attrs}}
        with self.lock:
            self.spans.write(json.dumps(rec) + "\n")
            self.spans.flush()

    def log(self, service, message, attrs, traceparent, run_id):
        # the shape hostd/telemetry.py log() writes: body + severity, a structured event's name
        # repeated in attributes.event (session.state carries session_id/from/to/ts/outcome there)
        tid, pid = self._ids(traceparent) if traceparent else (None, None)
        attributes = {"fleetkit.run_id": run_id or "", "service.version": "stub", **attrs}
        if "." in message:
            attributes["event"] = message
        rec = {"ts": time.time(), "severity": "INFO", "service": service, "body": message, "trace_id": tid,
               "span_id": pid, "attributes": attributes}
        with self.lock:
            self.logs.write(json.dumps(rec, default=str) + "\n")
            self.logs.flush()
            self.text.write(f"{rec['ts']:.3f} {service} {message} {json.dumps(attrs, default=str)}\n")
            self.text.flush()

    def console(self, session_id: str, line: str) -> None:
        """sessions/<id>/console.log, as hostd's docker and firecracker backends write it."""
        d = self.dir / "sessions" / session_id
        d.mkdir(parents=True, exist_ok=True)
        with self.lock, open(d / "console.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def close(self):
        for fh in (self.spans, self.logs, self.text):
            fh.close()


class Handler(BaseHTTPRequestHandler):
    host: StubHost = None  # set by serve()
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, status: int, body):
        data = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw) if raw else {}

    def _ctx(self):
        return self.headers.get("traceparent"), self.headers.get("X-Fleetkit-Run-Id")

    def do_GET(self):
        h = self.host
        p = self.path.split("?", 1)[0]
        if p == "/health":
            return self._send(200, {"ok": True, "backends": ["docker"], "stub": True, "host_id": h.o.host_id})
        if p == "/host/metrics":
            return self._send(200, h.metrics())
        if p == "/host/info":
            return self._send(200, h.info()) if h.o.host_info else self._send(404, {"error": "not found"})
        if p == "/host/verify-clean":
            return self._send(200, h.verify_clean())
        if p == "/sessions":
            return self._send(200, [s.view() for s in h.sessions.values()])
        if p.startswith("/sessions/"):
            sid = p.split("/")[2]
            s = h.get(sid)
            return self._send(200, s.view()) if s else self._send(404, {"error": "no such session"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        h = self.host
        p = self.path.split("?", 1)[0]
        tp, run_id = self._ctx()
        if p == "/sessions":
            spec = self._body()
            return self._send(201, h.create(spec, tp, run_id))
        parts = p.split("/")
        if len(parts) == 4 and parts[1] == "sessions" and parts[3] == "task":
            status, body = h.task(parts[2], self._body(), tp, run_id)
            return self._send(status, body)
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        h = self.host
        parts = self.path.split("?", 1)[0].split("/")
        tp, run_id = self._ctx()
        if len(parts) == 3 and parts[1] == "sessions":
            found = h.destroy(parts[2], traceparent=tp, run_id=run_id)
            return self._send(200 if found else 404, {"ok": found})
        self._send(404, {"error": "not found"})


class StubServer:
    """Serves the stub in a background thread; ``url`` is the base URL."""

    def __init__(self, port: int = 0, **opts):
        self.host = StubHost(StubOptions(**opts))
        handler = type("BoundHandler", (Handler,), {"host": self.host})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "StubServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.host.stop()
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="stub host daemon for driver development")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--telemetry-dir", default=None, help="write spans.jsonl/logs.jsonl/hostd.log here")
    ap.add_argument("--startup-ms", type=int, default=120)
    ap.add_argument("--step-ms", type=int, default=20)
    ap.add_argument("--leak", action="store_true", help="make verify-clean report a leftover after each destroy")
    a = ap.parse_args(argv)
    srv = StubServer(a.port, telemetry_dir=a.telemetry_dir, startup_ms=a.startup_ms, step_ms=a.step_ms,
                     leak_after_trial=a.leak).start()
    print(f"stub hostd listening on {srv.url} (Ctrl-C to stop)", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
