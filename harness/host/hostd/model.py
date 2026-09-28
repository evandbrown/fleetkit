"""MicroVM model: states, outcomes, failure categories and the microVM record.

Everything here is the closed vocabulary from docs/harness-design.md section 4.
"""
from __future__ import annotations

import base64
import json
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
    # States in which a microVM holds resources and the reaper's lifetime rule applies.
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
    MICROVM_NOT_READY = "microvm_not_ready"

    ALL = (OK, STEP_TIMEOUT, TASK_TIMEOUT, ASSERTION_FAILED, NAVIGATION_ERROR, BROWSER_CRASHED,
           GUEST_UNREACHABLE, MICROVM_NOT_READY)


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


# The spec's extra Chromium flags (workload.chromium_extra_flags) reach the guest on every microVM as
# ``MicroVM.chromium_extra_flags``. expand.py checked them against experiments/schema/chromium-flags.json's
# allowed list before the run; hostd checks only their shape and size, so they fit on a kernel command line.
MAX_CHROMIUM_FLAGS = 16
MAX_CHROMIUM_FLAG_CHARS = 512


def validate_chromium_flags(flags: Any) -> List[str]:
    """The flags as a list, or ValueError: at most MAX_CHROMIUM_FLAGS strings, each a ``--flag`` of printable
    ASCII (spaces allowed, for --js-flags), MAX_CHROMIUM_FLAG_CHARS at most together. None is none."""
    if flags is None:
        return []
    if not isinstance(flags, list) or len(flags) > MAX_CHROMIUM_FLAGS:
        raise ValueError("chromium_extra_flags must be a list of at most %d flags" % MAX_CHROMIUM_FLAGS)
    for f in flags:
        if not isinstance(f, str) or not f.startswith("--") or not all(" " <= c <= "~" for c in f):
            raise ValueError("chromium_extra_flags: %r is not a --flag in printable ASCII" % (f,))
    if sum(len(f) for f in flags) > MAX_CHROMIUM_FLAG_CHARS:
        raise ValueError("chromium_extra_flags: more than %d characters of flags" % MAX_CHROMIUM_FLAG_CHARS)
    return list(flags)


def encode_chromium_flags(flags: List[str]) -> str:
    """The flags as one word with no spaces or quotes, for an environment variable or the kernel command
    line: unpadded URL-safe base64 of their JSON list. guestd.chromium.decode_extra_flags reverses it."""
    raw = json.dumps(list(flags), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


# The spec's hypervisor section reaches a backend on every microVM as ``MicroVM.hypervisor``:
# {"name": <backend name>, "virtio_transport": "mmio" | "pci", "virtio_rng": bool}, the keys
# after "name" present only when the create request carried them. A backend says which values
# it can carry out with a class attribute ``hypervisor_options`` ({option: [accepted values]});
# a backend without one is taken to offer only what every backend did before the spec existed.
DEFAULT_HYPERVISOR_OPTIONS: Dict[str, List[Any]] = {"virtio_transport": ["mmio"], "virtio_rng": [False]}


def hypervisor_options(backend: Any) -> Dict[str, List[Any]]:
    """The values `backend` can carry out, per hypervisor option (GET /host/info shows them)."""
    opts = getattr(backend, "hypervisor_options", None)
    return {k: list(v) for k, v in (opts if isinstance(opts, dict) else DEFAULT_HYPERVISOR_OPTIONS).items()}


def hypervisor_problems(backend: Any, hypervisor: Any) -> List[str]:
    """Why `backend` can't carry out this hypervisor section; empty when it can."""
    if hypervisor is None:
        return []
    if not isinstance(hypervisor, dict):
        return ["hypervisor must be an object"]
    name = getattr(backend, "name", "")
    problems = []
    if hypervisor.get("name", name) != name:
        problems.append("this daemon runs %s, not %s" % (name, hypervisor.get("name")))
    offered = hypervisor_options(backend)
    for key, value in hypervisor.items():
        if key == "name":
            continue
        if key not in offered:
            problems.append("the %s backend has no option %s" % (name, key))
        elif not any(value == v and type(value) is type(v) for v in offered[key]):
            problems.append("the %s backend can't carry out %s = %s (it offers %s)" % (
                name, key, json.dumps(value), ", ".join(json.dumps(v) for v in offered[key])))
    return problems


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
class MicroVM:
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
    # The spec's hypervisor section this microVM was created with (see DEFAULT_HYPERVISOR_OPTIONS).
    hypervisor: Dict[str, Any] = field(default_factory=dict)
    # The spec's extra Chromium flags, added after the guest's base flags (see validate_chromium_flags).
    chromium_extra_flags: List[str] = field(default_factory=list)
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
    # Boot phases on the host clock, from the /health answer that found the microVM ready
    # (guest.boot_phases): kernel start, guest daemon start, Chromium launched, Chromium ready.
    kernel_start_ts: Optional[float] = None
    guestd_start_ts: Optional[float] = None
    chromium_launch_ts: Optional[float] = None
    chromium_ready_ts: Optional[float] = None
    # What the guest said about itself in that answer (guest.guest_info).
    guest_info: Optional[Dict[str, Any]] = None
    # Backend-private handles (container name, tap, pid...). Never exposed verbatim.
    handle: Dict[str, Any] = field(default_factory=dict)
    # Trace context of the microVM's create span (parent of every record about it).
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

        Returns the `microvm.state` record (section 4) for the caller to log.
        """
        if not self.can_transition(to):
            raise TransitionError("microVM %s: %s -> %s is not allowed" % (self.id, self.state, to))
        if outcome is not None:
            if outcome not in Outcome.ALL:
                raise ValueError("unknown outcome %r" % outcome)
            self.outcome = outcome
        record = {"microvm_id": self.id, "from": self.state, "to": to, "ts": ts, "outcome": self.outcome}
        self.state = to
        self.transitions.append(record)
        return record

    def record(self) -> Dict[str, Any]:
        """The public GET /microvms/{id} shape (section 4) plus identifying extras."""
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
            "hypervisor": dict(self.hypervisor),
            "chromium_extra_flags": list(self.chromium_extra_flags),
            "console_log": self.console_log,
            "trace_id": self.trace.trace_id if self.trace else None,
            "kernel_start_ts": self.kernel_start_ts,
            "guestd_start_ts": self.guestd_start_ts,
            "chromium_launch_ts": self.chromium_launch_ts,
            "chromium_ready_ts": self.chromium_ready_ts,
            "guest_info": dict(self.guest_info) if self.guest_info is not None else None,
        }
