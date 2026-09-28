"""MicroVM lifecycle: create, readiness, task proxy, lifetime/idle reaper, destroy.

This is the state machine of section 4, independent of any backend and of the
HTTP layer so it can be unit-tested with a fake backend, a fake guest and a
fake clock.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .backends.base import Backend, BackendError
from .guest import GuestClient, GuestError, boot_phases, guest_info
from .model import (DEFAULT_CONSOLE, DEFAULT_MEMORY_PAGES, Defaults, FailureCategory, MicroVM, Outcome, State,
                    TraceContext, hypervisor_problems, microvm_option_problems, validate_chromium_flags,
                    validate_fault)
from .telemetry import Telemetry


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


@dataclass
class RequestContext:
    """What a request carries for correlation: W3C trace context and baggage."""
    trace: Optional[TraceContext] = None
    baggage: Dict[str, str] = field(default_factory=dict)

    @property
    def run_id(self) -> Optional[str]:
        return self.baggage.get("fleetkit.run_id")

    @property
    def trial_id(self) -> Optional[str]:
        return self.baggage.get("fleetkit.trial_id")


def _num(body: Dict[str, Any], key: str, default: Any, minimum: float = 0) -> float:
    v = body.get(key, default)
    if v is None:
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ApiError(400, "%s must be a number" % key)
    if v < minimum:
        raise ApiError(400, "%s must be >= %s" % (key, minimum))
    return v


class Manager:
    """Every microVM on this host: slot allocation, lifecycle, the task proxy and the reaper."""

    def __init__(self, backend: Backend, telemetry: Telemetry, guest: Optional[GuestClient] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 poll_interval: float = Defaults.READY_POLL_S, dry_run: bool = False, host_id: str = "",
                 spawn: Optional[Callable[[Callable[[], None], str], None]] = None):
        self.backend = backend
        self.tel = telemetry
        self.guest = guest or GuestClient()
        self.clock = clock
        self.sleep = sleep
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self.host_id = host_id
        self.microvms: Dict[str, MicroVM] = {}
        self._used_slots: set = set()
        self._lock = threading.Lock()
        self._spawn = spawn or self._thread_spawn
        self._reaper_stop = threading.Event()
        self._reaper_thread: Optional[threading.Thread] = None

    # ----- helpers -----------------------------------------------------------
    @staticmethod
    def _thread_spawn(fn: Callable[[], None], name: str) -> None:
        threading.Thread(target=fn, name=name, daemon=True).start()

    def _attrs(self, s: MicroVM, **more: Any) -> Dict[str, Any]:
        a = {"fleetkit.run_id": s.run_id, "fleetkit.trial_id": s.trial_id, "fleetkit.microvm_id": s.id,
             "fleetkit.backend": s.backend, "fleetkit.host_id": self.host_id, "fleetkit.slot": s.slot}
        a.update(more)
        return a

    def _transition(self, s: MicroVM, to: str, outcome: Optional[str] = None) -> None:
        """Move the microVM and emit the `microvm.state` record. Caller holds s.lock."""
        rec = s.transition(to, self.clock(), outcome)
        self.tel.event("microvm.state", ctx=s.trace, **{**self._attrs(s), **rec})

    def get(self, microvm_id: str) -> MicroVM:
        s = self.microvms.get(microvm_id)
        if s is None:
            raise ApiError(404, "no microVM %s" % microvm_id)
        return s

    def list_records(self) -> List[Dict[str, Any]]:
        return [s.record() for s in list(self.microvms.values())]

    def live_microvms(self) -> List[MicroVM]:
        return [s for s in list(self.microvms.values()) if s.state in (State.BOOTING, State.READY, State.BUSY)]

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for s in list(self.microvms.values()):
            out[s.state] = out.get(s.state, 0) + 1
        return out

    # ----- create ------------------------------------------------------------
    def create_microvms(self, request: Dict[str, Any], ctx: RequestContext) -> List[Dict[str, Any]]:
        backend = request.get("backend") or self.backend.name
        if backend != self.backend.name:
            raise ApiError(400, "this daemon runs the %s backend, not %s" % (self.backend.name, backend))
        count = int(_num(request, "count", 1, 1))
        vcpus = int(_num(request, "vcpus", Defaults.VCPUS, 1))
        mem_mib = int(_num(request, "mem_mib", Defaults.MEM_MIB, 64))
        ready_timeout_s = _num(request, "ready_timeout_s", Defaults.READY_TIMEOUT_S, 1)
        max_lifetime_s = _num(request, "max_lifetime_s", Defaults.MAX_LIFETIME_S, 1)
        idle_timeout_s = _num(request, "idle_timeout_s", Defaults.IDLE_TIMEOUT_S, 1)
        launch_interval_ms = _num(request, "launch_interval_ms", Defaults.LAUNCH_INTERVAL_MS, 0)
        try:
            fault = validate_fault(request.get("fault"))
            chromium_extra_flags = validate_chromium_flags(request.get("chromium_extra_flags"))
        except ValueError as e:
            raise ApiError(400, str(e))
        # The spec's hypervisor section: refused unless this backend can carry it out, so what a
        # run records is what ran. Absent, the microVM runs the backend as it always has.
        problems = hypervisor_problems(self.backend, request.get("hypervisor"))
        if problems:
            raise ApiError(400, "; ".join(problems))
        hypervisor = {"name": self.backend.name, **(request.get("hypervisor") or {})}
        # The spec's guest console and memory pages: refused unless this backend can carry them out. Absent,
        # the microVM boots as every microVM did before they existed.
        problems = microvm_option_problems(self.backend, request)
        if problems:
            raise ApiError(400, "; ".join(problems))
        console = request.get("console") or DEFAULT_CONSOLE
        memory_pages = request.get("memory_pages") or DEFAULT_MEMORY_PAGES
        for key, value in (("console", console), ("memory_pages", memory_pages)):
            note = (getattr(self.backend, "option_notes", None) or {}).get((key, value))
            if note:
                self.tel.log("%s %s: %s" % (key, value, note), severity="WARN")
        run_id = request.get("run_id") or ctx.run_id
        trial_id = request.get("trial_id") or ctx.trial_id

        created: List[MicroVM] = []
        with self._lock:
            free: List[int] = []
            skipped: List[int] = []
            for slot in range(self.backend.max_slots):
                if slot in self._used_slots:
                    continue
                if len(free) >= count:
                    break
                if self.backend.slot_usable(slot):
                    free.append(slot)
                else:
                    skipped.append(slot)
            if skipped:
                self.tel.log("slots %s skipped: their resources are held by something outside hostd" % skipped,
                             severity="WARN")
            if len(free) < count:
                raise ApiError(409, "only %d of %d requested slots are free" % (len(free), count))
            for slot in free[:count]:
                self._used_slots.add(slot)
                s = MicroVM(id=MicroVM.new_id(slot), slot=slot, backend=self.backend.name,
                            address=self.backend.address(slot), vcpus=vcpus, mem_mib=mem_mib, fault=fault,
                            ready_timeout_s=ready_timeout_s, max_lifetime_s=max_lifetime_s,
                            idle_timeout_s=idle_timeout_s, fixture_base_url=self.backend.fixture_base_url,
                            run_id=run_id, trial_id=trial_id, hypervisor=dict(hypervisor),
                            chromium_extra_flags=list(chromium_extra_flags), console=console,
                            memory_pages=memory_pages)
                self.microvms[s.id] = s
                created.append(s)

        parent = ctx.trace

        def launcher() -> None:
            for i, s in enumerate(created):
                if i and launch_interval_ms:
                    self.sleep(launch_interval_ms / 1000.0)
                self._spawn(lambda s=s: self.launch(s, parent), "launch-%s" % s.id)

        self._spawn(launcher, "launcher")
        return [{"id": s.id, "slot": s.slot, "address": s.address} for s in created]

    def launch(self, s: MicroVM, parent: Optional[TraceContext]) -> None:
        """Create the container/VM and poll readiness. Runs on its own thread per microVM."""
        with s.lock:
            if s.state != State.CREATING:
                return
            s.created_ts = self.clock()
            span = self.tel.start_span("microvm.create", parent=parent, start_ns=int(s.created_ts * 1e9),
                                       attrs=self._attrs(s, **{"fleetkit.vcpus": s.vcpus, "fleetkit.mem_mib": s.mem_mib,
                                                               "fleetkit.fault": s.fault}))
            s.trace = span.ctx
            self.tel.event("microvm.state", ctx=s.trace, **{**self._attrs(s), "microvm_id": s.id, "from": None,
                                                              "to": State.CREATING, "ts": s.created_ts, "outcome": None})
        try:
            self.backend.create(s)
        except BackendError as e:
            self._fail(s, Outcome.STARTUP_ERROR, str(e))
            span.end(error=str(e))
            self.destroy(s.id)
            return
        except Exception as e:  # a bug in a backend must not leak the slot
            self._fail(s, Outcome.STARTUP_ERROR, "%s: %s" % (type(e).__name__, e))
            span.end(error=str(e))
            self.destroy(s.id)
            return
        with s.lock:
            s.process_started_ts = self.clock()
            cancelled = s.state != State.CREATING   # destroyed while the backend was creating
            if not cancelled:
                self._transition(s, State.BOOTING)
        if cancelled:
            # destroy() already ran (or is running) against a container/VM that did not exist
            # yet, so its backend.destroy() was a no-op. Now that create() has returned, wait
            # for that destroy to finish and remove what it could not see. backend.destroy is
            # idempotent on both backends, so the second call is safe.
            span.end(status="cancelled")
            s.destroy_done.wait(timeout=120)
            try:
                self.backend.destroy(s)
            except Exception as e:
                self.tel.log("microVM %s: late destroy after cancelled create: %s: %s" % (s.id, type(e).__name__, e),
                             severity="ERROR", ctx=s.trace, attrs=self._attrs(s))
            else:
                self.tel.log("microVM %s: destroyed after create() returned into state %s" % (s.id, s.state),
                             severity="WARN", ctx=s.trace, attrs=self._attrs(s))
            return
        outcome = self._wait_ready(s)
        if outcome is None and s.ready_ts is None:     # destroyed (reaper or caller) while booting
            span.end(status="cancelled")
        elif outcome is None:
            span.set(**{"fleetkit.startup_ms": s.startup_ms,
                        "fleetkit.setup_ms": (s.process_started_ts - s.created_ts) * 1000.0})
            span.end(end_ns=int(s.ready_ts * 1e9))
        else:
            span.end(error="%s: %s" % (outcome, s.error))
            self.destroy(s.id)

    def _fail(self, s: MicroVM, outcome: str, error: str) -> None:
        with s.lock:
            if s.state in (State.CREATING, State.BOOTING):
                s.error = error
                self._transition(s, State.FAILED, outcome)
                self.tel.log("microVM %s failed during startup: %s (%s)" % (s.id, outcome, error),
                             severity="WARN", ctx=s.trace, attrs=self._attrs(s))

    def _wait_ready(self, s: MicroVM) -> Optional[str]:
        """Poll /health every 250 ms. Returns None once ready, else the failure outcome."""
        if self.dry_run:
            with s.lock:
                if s.state != State.BOOTING:
                    return None
                s.ready_ts = self.clock()
                s.startup_ms = (s.ready_ts - s.created_ts) * 1000.0
                s.last_activity_ts = s.ready_ts
                self._transition(s, State.READY)
            return None
        last_alive_check = self.clock()
        while True:
            with s.lock:
                if s.state != State.BOOTING:      # destroyed underneath us
                    return None
            ready = False
            r = None
            try:
                r = self.guest.health(s.address, timeout=1.0)
                ready = r.status == 200 and isinstance(r.body, dict) and bool(r.body.get("ready"))
            except GuestError:
                pass
            now = self.clock()
            if ready and r is not None:
                phases = boot_phases(r)
                with s.lock:
                    if s.state != State.BOOTING:
                        return None
                    s.kernel_start_ts = phases["kernel_start_ts"]
                    s.guestd_start_ts = phases["guestd_start_ts"]
                    s.chromium_launch_ts = phases["chromium_launch_ts"]
                    s.chromium_ready_ts = phases["chromium_ready_ts"]
                    s.guest_info = guest_info(r.body)
                    s.ready_ts = now
                    s.startup_ms = (now - s.created_ts) * 1000.0
                    s.last_activity_ts = now
                    self._transition(s, State.READY)
                self.tel.log("microVM %s ready in %.0f ms" % (s.id, s.startup_ms), ctx=s.trace, attrs=self._attrs(s))
                return None
            if now - s.created_ts >= s.ready_timeout_s:
                self._fail(s, Outcome.STARTUP_TIMEOUT, "not ready after %.0fs" % s.ready_timeout_s)
                return Outcome.STARTUP_TIMEOUT
            if now - last_alive_check >= 1.0:
                last_alive_check = now
                if not self.backend.alive(s):
                    info = self.backend.exit_info(s) or "process exited during boot"
                    self._fail(s, Outcome.STARTUP_ERROR, info)
                    return Outcome.STARTUP_ERROR
            self.sleep(self.poll_interval)

    # ----- destroy -----------------------------------------------------------
    def destroy(self, microvm_id: str, outcome: Optional[str] = None) -> Dict[str, Any]:
        """Idempotent. Blocks until every per-microVM leftover is gone; cleanup_ms = receipt -> gone."""
        s = self.get(microvm_id)
        receipt = self.clock()
        with s.lock:
            if s.state == State.DESTROYED or (s.state == State.FAILED and s.destroyed_ts is not None):
                return s.record()
            in_progress = s.state == State.DESTROYING or s.handle.get("_destroying")
            if not in_progress:
                s.handle["_destroying"] = True
                if s.state != State.FAILED:
                    self._transition(s, State.DESTROYING, outcome or s.outcome or Outcome.COMPLETED)
                elif outcome and not s.outcome:
                    s.outcome = outcome
        if in_progress:
            s.destroy_done.wait(timeout=120)
            return s.record()
        span = self.tel.start_span("microvm.destroy", parent=s.trace, attrs=self._attrs(s, **{"fleetkit.outcome": s.outcome}))
        error = None
        try:
            self.backend.destroy(s)
        except Exception as e:
            error = "destroy: %s: %s" % (type(e).__name__, e)
            self.tel.log("microVM %s: %s" % (s.id, error), severity="ERROR", ctx=s.trace, attrs=self._attrs(s))
        now = self.clock()
        with s.lock:
            s.destroyed_ts = now
            s.cleanup_ms = (now - receipt) * 1000.0
            if error and not s.error:
                s.error = error
            if s.state == State.DESTROYING:
                self._transition(s, State.DESTROYED)
        with self._lock:
            self._used_slots.discard(s.slot)
        s.destroy_done.set()
        span.set(**{"fleetkit.cleanup_ms": s.cleanup_ms})
        span.end(error=error)
        self.tel.log("microVM %s destroyed (%s) in %.0f ms" % (s.id, s.outcome, s.cleanup_ms), ctx=s.trace,
                     attrs=self._attrs(s))
        return s.record()

    def destroy_all(self) -> None:
        for s in list(self.microvms.values()):
            if s.state not in State.TERMINAL:
                try:
                    self.destroy(s.id)
                except Exception:
                    pass

    # ----- reaper ------------------------------------------------------------
    def reap_once(self, now: Optional[float] = None) -> List[Tuple[str, str]]:
        """Destroy microVMs past their lifetime, or idle past their idle timeout (busy ones skipped)."""
        now = self.clock() if now is None else now
        reaped: List[Tuple[str, str]] = []
        for s in list(self.microvms.values()):
            with s.lock:
                state, created, last = s.state, s.created_ts, s.last_activity_ts
                lifetime, idle = s.max_lifetime_s, s.idle_timeout_s
            outcome = None
            if state in State.LIVE and created is not None and now - created >= lifetime:
                outcome = Outcome.LIFETIME_EXPIRED
            elif state == State.READY and last is not None and now - last >= idle:
                outcome = Outcome.IDLE_EXPIRED
            if outcome:
                self.tel.log("reaper: microVM %s %s (state %s)" % (s.id, outcome, state), severity="WARN",
                             ctx=s.trace, attrs=self._attrs(s, **{"fleetkit.outcome": outcome}))
                reaped.append((s.id, outcome))
                self._spawn(lambda sid=s.id, o=outcome: self.destroy(sid, o), "reap-%s" % s.id)
        return reaped

    def start_reaper(self, period: float = Defaults.REAPER_PERIOD_S) -> None:
        def loop() -> None:
            while not self._reaper_stop.wait(period):
                try:
                    self.reap_once()
                except Exception as e:
                    self.tel.log("reaper error: %s" % e, severity="ERROR")
        self._reaper_thread = threading.Thread(target=loop, name="reaper", daemon=True)
        self._reaper_thread.start()

    def stop_reaper(self) -> None:
        self._reaper_stop.set()

    # ----- task proxy --------------------------------------------------------
    def run_task(self, microvm_id: str, body: Dict[str, Any], ctx: RequestContext) -> Tuple[int, Dict[str, Any]]:
        s = self.get(microvm_id)
        task_id = str(body.get("task_id") or uuid.uuid4().hex[:12])
        # Everything that can reject the body (400) happens before the microVM is marked busy:
        # a rejected request must leave the microVM `ready`, not held until max_lifetime_s.
        req = dict(body)
        req["task_id"] = task_id
        req.setdefault("fixture_base_url", s.fixture_base_url)
        req.setdefault("step_timeout_ms", Defaults.STEP_TIMEOUT_MS)
        req.setdefault("task_timeout_ms", Defaults.TASK_TIMEOUT_MS)
        for k in ("run_id", "trial_id"):
            req.pop(k, None)
        task_timeout_ms = int(_num(req, "task_timeout_ms", Defaults.TASK_TIMEOUT_MS, 1))
        _num(req, "step_timeout_ms", Defaults.STEP_TIMEOUT_MS, 1)
        deadline_s = (task_timeout_ms + Defaults.PROXY_MARGIN_MS) / 1000.0
        run_id = body.get("run_id") or ctx.run_id or s.run_id
        trial_id = body.get("trial_id") or ctx.trial_id or s.trial_id

        with s.lock:
            if s.state != State.READY:
                return 409, {"ok": False, "failure_category": FailureCategory.MICROVM_NOT_READY,
                             "failed_step": None, "steps": [], "task_id": task_id, "microvm_id": s.id,
                             "microvm_state": s.state, "microvm_outcome": s.outcome,
                             "error": "microVM is %s" % s.state}
            self._transition(s, State.BUSY)
            s.last_activity_ts = self.clock()

        attrs = self._attrs(s, **{"fleetkit.run_id": run_id, "fleetkit.trial_id": trial_id,
                                  "fleetkit.task_id": task_id, "fleetkit.proxy_deadline_s": deadline_s})
        span = self.tel.start_span("task.proxy", parent=ctx.trace or s.trace, attrs=attrs)
        t0 = time.monotonic()
        try:
            return self._proxy(s, req, ctx, span, task_id, deadline_s, t0)
        except Exception as e:
            # A bug past this point (e.g. a malformed guest answer) must not leave the microVM
            # busy forever: the idle reaper skips busy microVMs.
            with s.lock:
                if s.state == State.BUSY:
                    self._transition(s, State.READY)
                s.last_activity_ts = self.clock()
            span.end(error="%s: %s" % (type(e).__name__, e))
            raise

    def _proxy(self, s: MicroVM, req: Dict[str, Any], ctx: RequestContext, span: Any, task_id: str,
               deadline_s: float, t0: float) -> Tuple[int, Dict[str, Any]]:
        attrs = dict(span.attrs)
        rtt_ns: Optional[int] = None
        try:
            rtt_ns = self.guest.health(s.address, timeout=2.0).rtt_ns
        except GuestError:
            pass
        try:
            r = self.guest.task(s.address, req, timeout=deadline_s, traceparent=span.ctx.traceparent())
        except GuestError as e:
            return self._unreachable(s, span, task_id, str(e), t0)
        if not isinstance(r.body, dict) or (r.status != 200 and "failure_category" not in r.body):
            return self._unreachable(s, span, task_id, "guest answered HTTP %d: %s" % (r.status, str(r.body)[:200]), t0)

        resp = dict(r.body)
        guest_clock_ns = resp.get("guest_clock_ns")
        clock_offset_ns = None
        if isinstance(guest_clock_ns, (int, float)):
            # rtt of the /health probe just before the task, so a long task doesn't skew the estimate.
            clock_offset_ns = int(r.send_ns + (rtt_ns if rtt_ns is not None else r.rtt_ns) // 2 - int(guest_clock_ns))
            self._emit_guest_spans(s, span.ctx, resp, int(guest_clock_ns) + clock_offset_ns, attrs)
        self._forward_log_tail(s, span.ctx, resp.get("log_tail"), attrs)

        guest_mem_available = chromium_rss = None
        try:
            m = self.guest.metrics(s.address, timeout=2.0)
            if isinstance(m.body, dict):
                guest_mem_available = m.body.get("mem_available")
                chromium_rss = m.body.get("chromium_rss")
        except GuestError:
            pass

        with s.lock:
            if s.state == State.BUSY:
                self._transition(s, State.READY)
            s.last_activity_ts = self.clock()
            microvm_outcome = s.outcome
        resp.update({
            "task_id": task_id, "microvm_id": s.id, "slot": s.slot, "trace_id": span.ctx.trace_id,
            "clock_offset_ns": clock_offset_ns, "guest_mem_available": guest_mem_available,
            "chromium_rss": chromium_rss, "host_rtt_ms": (rtt_ns / 1e6) if rtt_ns is not None else None,
            "proxy_ms": (time.monotonic() - t0) * 1000.0, "microvm_outcome": microvm_outcome,
        })
        resp.setdefault("failure_category", FailureCategory.OK if resp.get("ok") else FailureCategory.GUEST_UNREACHABLE)
        span.set(**{"fleetkit.failure_category": resp["failure_category"], "fleetkit.task_ms": resp.get("task_ms")})
        span.end(error=None if resp.get("ok") else "%s: %s" % (resp["failure_category"], resp.get("error")))
        return 200, resp

    def _unreachable(self, s: MicroVM, span: Any, task_id: str, error: str, t0: float) -> Tuple[int, Dict[str, Any]]:
        with s.lock:
            was_busy = s.state == State.BUSY
            if was_busy:
                # The daemon never answered within the proxy deadline: this microVM is not trusted.
                s.outcome = Outcome.TASK_FAILURE_DESTROYED
            s.last_activity_ts = self.clock()
            microvm_outcome = s.outcome
        self.tel.log("task %s on %s: guest_unreachable: %s" % (task_id, s.id, error), severity="WARN",
                     ctx=span.ctx, attrs=self._attrs(s, **{"fleetkit.task_id": task_id}))
        if was_busy:
            self._spawn(lambda: self.destroy(s.id, Outcome.TASK_FAILURE_DESTROYED), "destroy-%s" % s.id)
        resp = {"ok": False, "failure_category": FailureCategory.GUEST_UNREACHABLE, "failed_step": None,
                "steps": [], "error": error, "task_id": task_id, "microvm_id": s.id, "slot": s.slot,
                "trace_id": span.ctx.trace_id, "clock_offset_ns": None, "guest_mem_available": None,
                "chromium_rss": None, "proxy_ms": (time.monotonic() - t0) * 1000.0,
                "microvm_outcome": microvm_outcome}
        span.set(**{"fleetkit.failure_category": FailureCategory.GUEST_UNREACHABLE})
        span.end(error=error)
        return 200, resp

    def _emit_guest_spans(self, s: MicroVM, parent: TraceContext, resp: Dict[str, Any], base_ns: int,
                          attrs: Dict[str, Any]) -> None:
        """Guest spans after the fact: base_ns is the guest's receipt time on the host clock."""
        task_ms = resp.get("task_ms")
        steps = resp.get("steps") or []
        ends = [base_ns + int(st.get("settle_ns") or 0) for st in steps if isinstance(st, dict)]
        task_end = base_ns + int(task_ms * 1e6) if isinstance(task_ms, (int, float)) else (max(ends) if ends else base_ns)
        gattrs = {**attrs, "fleetkit.failure_category": resp.get("failure_category"),
                  "fleetkit.failed_step": resp.get("failed_step")}
        task_ctx = self.tel.record_span("guest.task", parent, base_ns, task_end, gattrs, service="guest-daemon",
                                        status="ok" if resp.get("ok") else "error",
                                        error=None if resp.get("ok") else resp.get("error"))
        for i, st in enumerate(steps):
            if not isinstance(st, dict):
                continue
            start = base_ns + int(st.get("dispatch_ns") or 0)
            if st.get("settle_ns") is not None:
                end = base_ns + int(st["settle_ns"])
            elif st.get("duration_ms") is not None:
                end = start + int(float(st["duration_ms"]) * 1e6)
            else:
                end = start
            failed = resp.get("failed_step") == st.get("name") and not resp.get("ok")
            self.tel.record_span("step.%s" % st.get("name", i), task_ctx, start, end,
                                 {**attrs, "fleetkit.step": st.get("name"), "fleetkit.step_index": i},
                                 service="guest-daemon", status="error" if failed else "ok",
                                 error=resp.get("error") if failed else None)

    def _forward_log_tail(self, s: MicroVM, ctx: TraceContext, tail: Any, attrs: Dict[str, Any]) -> None:
        if not tail:
            return
        for line in tail if isinstance(tail, list) else [tail]:
            if isinstance(line, dict):
                body = str(line.get("msg") or line.get("message") or line.get("body") or line)
                # `severity`; a guest image built before the D56 rename still writes the
                # logging module's `level` key, so it is read as a fallback.
                sev = str(line.get("severity") or line.get("level") or "INFO").upper()
                ts = line.get("ts")
                ts_ns = int(float(ts) * 1e9) if isinstance(ts, (int, float)) else None
            else:
                body, sev, ts_ns = str(line), "INFO", None
            self.tel.log(body, severity=sev if sev in ("DEBUG", "INFO", "WARN", "ERROR") else "INFO",
                         ctx=ctx, attrs=attrs, service="guest-daemon", ts_ns=ts_ns)
