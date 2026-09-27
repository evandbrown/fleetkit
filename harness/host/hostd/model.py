"""Session model: states, outcomes, failure categories and the session record.

Everything here is the closed vocabulary from docs/harness-design.md section 4.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class State:
    CREATING = "creating"
    BOOTING = "booting"
    READY = "ready"
    BUSY = "busy"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"

    ALL = (CREATING, BOOTING, READY, BUSY, DESTROYING, DESTROYED, FAILED)
    # `failed` is terminal for the state machine; cleanup still runs and sets destroyed_ts.
    TERMINAL = (DESTROYED, FAILED)
    # States in which a session holds resources and the reaper's lifetime rule applies.
    LIVE = (CREATING, BOOTING, READY, BUSY)


# from-state -> allowed to-states
TRANSITIONS: Dict[str, tuple] = {
    State.CREATING: (State.BOOTING, State.FAILED, State.DESTROYING),
    State.BOOTING: (State.READY, State.FAILED, State.DESTROYING),
    State.READY: (State.BUSY, State.DESTROYING),
    State.BUSY: (State.READY, State.DESTROYING),
    State.DESTROYING: (State.DESTROYED,),
    State.DESTROYED: (),
    State.FAILED: (),
}


class Outcome:
    COMPLETED = "completed"
    STARTUP_TIMEOUT = "startup_timeout"
    STARTUP_ERROR = "startup_error"
    LIFETIME_EXPIRED = "lifetime_expired"
    IDLE_EXPIRED = "idle_expired"
    TASK_FAILURE_DESTROYED = "task_failure_destroyed"

    ALL = (COMPLETED, STARTUP_TIMEOUT, STARTUP_ERROR, LIFETIME_EXPIRED, IDLE_EXPIRED, TASK_FAILURE_DESTROYED)


class FailureCategory:
    OK = "ok"
    STEP_TIMEOUT = "step_timeout"
    TASK_TIMEOUT = "task_timeout"
    ASSERTION_FAILED = "assertion_failed"
    NAVIGATION_ERROR = "navigation_error"
    BROWSER_CRASHED = "browser_crashed"
    GUEST_UNREACHABLE = "guest_unreachable"
    SESSION_NOT_READY = "session_not_ready"

    ALL = (OK, STEP_TIMEOUT, TASK_TIMEOUT, ASSERTION_FAILED, NAVIGATION_ERROR, BROWSER_CRASHED,
           GUEST_UNREACHABLE, SESSION_NOT_READY)


FAULTS = ("crash_on_start", "never_ready", "hang_task", "hang_step", "slow_step")


def validate_fault(fault: Optional[str]) -> Optional[str]:
    """Return the fault name if it is in the closed set (slow_step takes a `:<ms>` suffix)."""
    if fault in (None, ""):
        return None
    name, _, arg = fault.partition(":")
    if name not in FAULTS:
        raise ValueError("unknown fault %r; expected one of %s" % (fault, ", ".join(FAULTS)))
    if name == "slow_step":
        if not arg.isdigit():
            raise ValueError("slow_step needs a millisecond argument, e.g. slow_step:15000")
    elif arg:
        raise ValueError("fault %r takes no argument" % name)
    return fault


class Defaults:
    """Section 4 timeout defaults; every one is overridable per trial."""
    STEP_TIMEOUT_MS = 10000
    TASK_TIMEOUT_MS = 45000
    PROXY_MARGIN_MS = 5000       # host proxy deadline = task_timeout_ms + 5000
    READY_TIMEOUT_S = 60
    MAX_LIFETIME_S = 600
    IDLE_TIMEOUT_S = 120
    LAUNCH_INTERVAL_MS = 0
    VCPUS = 2
    MEM_MIB = 2048
    READY_POLL_S = 0.25          # readiness is polled every 250 ms
    REAPER_PERIOD_S = 1.0
    METRICS_PERIOD_S = 1.0


class TransitionError(Exception):
    pass


@dataclass
class TraceContext:
    trace_id: str
    span_id: str
    sampled: bool = True

    def traceparent(self) -> str:
        return "00-%s-%s-%s" % (self.trace_id, self.span_id, "01" if self.sampled else "00")


@dataclass
class Session:
    id: str
    slot: int
    backend: str
    address: str
    vcpus: int
    mem_mib: int
    fault: Optional[str]
    ready_timeout_s: float
    max_lifetime_s: float
    idle_timeout_s: float
    fixture_base_url: str
    run_id: Optional[str] = None
    trial_id: Optional[str] = None
    state: str = State.CREATING
    created_ts: Optional[float] = None
    process_started_ts: Optional[float] = None
    ready_ts: Optional[float] = None
    destroyed_ts: Optional[float] = None
    last_activity_ts: Optional[float] = None
    startup_ms: Optional[float] = None
    cleanup_ms: Optional[float] = None
    outcome: Optional[str] = None
    error: Optional[str] = None
    console_log: Optional[str] = None
    # Backend-private handles (container name, tap, pid...). Never exposed verbatim.
    handle: Dict[str, Any] = field(default_factory=dict)
    # Trace context of the session's create span (parent of every record about it).
    trace: Optional[TraceContext] = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    destroy_done: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)
    transitions: List[Dict[str, Any]] = field(default_factory=list, repr=False, compare=False)

    @staticmethod
    def new_id(slot: int) -> str:
        return "s%03d-%s" % (slot, uuid.uuid4().hex[:8])

    def can_transition(self, to: str) -> bool:
        return to in TRANSITIONS[self.state]

    def transition(self, to: str, ts: float, outcome: Optional[str] = None) -> Dict[str, Any]:
        """Move to `to`, raising TransitionError if the state machine forbids it.

        Returns the `session.state` record (section 4) for the caller to log.
        """
        if not self.can_transition(to):
            raise TransitionError("session %s: %s -> %s is not allowed" % (self.id, self.state, to))
        if outcome is not None:
            if outcome not in Outcome.ALL:
                raise ValueError("unknown outcome %r" % outcome)
            self.outcome = outcome
        record = {"session_id": self.id, "from": self.state, "to": to, "ts": ts, "outcome": self.outcome}
        self.state = to
        self.transitions.append(record)
        return record

    def record(self) -> Dict[str, Any]:
        """The public GET /sessions/{id} shape (section 4) plus identifying extras."""
        return {
            "id": self.id,
            "state": self.state,
            "backend": self.backend,
            "slot": self.slot,
            "address": self.address,
            "created_ts": self.created_ts,
            "process_started_ts": self.process_started_ts,
            "ready_ts": self.ready_ts,
            "destroyed_ts": self.destroyed_ts,
            "last_activity_ts": self.last_activity_ts,
            "startup_ms": self.startup_ms,
            "cleanup_ms": self.cleanup_ms,
            "outcome": self.outcome,
            "error": self.error,
            "vcpus": self.vcpus,
            "mem_mib": self.mem_mib,
            "fault": self.fault,
            "fixture_base_url": self.fixture_base_url,
            "ready_timeout_s": self.ready_timeout_s,
            "max_lifetime_s": self.max_lifetime_s,
            "idle_timeout_s": self.idle_timeout_s,
            "run_id": self.run_id,
            "trial_id": self.trial_id,
            "console_log": self.console_log,
            "trace_id": self.trace.trace_id if self.trace else None,
        }
