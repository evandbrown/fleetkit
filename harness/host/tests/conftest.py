"""Offline fixtures: a fake backend, a fake guest, a fake clock and a synchronous manager."""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hostd.backends.base import Backend, BackendError  # noqa: E402
from hostd.guest import GuestError, GuestResponse  # noqa: E402
from hostd.manager import RequestContext, SessionManager  # noqa: E402
from hostd.model import Session  # noqa: E402
from hostd.runner import Runner  # noqa: E402
from hostd.telemetry import Telemetry  # noqa: E402


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now += dt

    def advance(self, dt: float) -> None:
        self.now += dt


class FakeBackend(Backend):
    name = "docker"
    max_slots = 4
    fixture_base_url = "http://fixture"

    def __init__(self):
        super().__init__(Runner(dry_run=True), "/nonexistent")
        self.created: List[str] = []
        self.destroyed: List[str] = []
        self.alive_ids: Dict[str, bool] = {}
        self.fail_create: Optional[str] = None
        self.create_calls = 0

    def address(self, slot: int) -> str:
        return "guest-%d" % slot

    def create(self, session: Session) -> None:
        self.create_calls += 1
        if self.fail_create:
            raise BackendError(self.fail_create)
        session.handle["container"] = "c-" + session.id
        session.console_log = "/nonexistent/%s/console.log" % session.id
        self.created.append(session.id)

    def alive(self, session: Session) -> bool:
        return self.alive_ids.get(session.id, True)

    def exit_info(self, session: Session) -> Optional[str]:
        return "status=exited exit=1"

    def destroy(self, session: Session) -> None:
        self.destroyed.append(session.id)

    def verify_clean(self) -> List[str]:
        return ["container c-%s" % sid for sid in self.created if sid not in self.destroyed]


class FakeGuest:
    """Stands in for GuestClient. Ready addresses answer /health; task_handler decides /task."""

    def __init__(self):
        self.ready: set = set()
        self.task_handler: Optional[Callable[..., Tuple[int, Dict[str, Any]]]] = None
        self.metrics_body: Dict[str, Any] = {"mem_total": 1, "mem_available": 12345, "cached": 0,
                                             "chromium_rss": 6789, "tasks_run": 0, "tasks_failed": 0}
        self.task_calls: List[Dict[str, Any]] = []
        # Extra /health fields (boot phases, guest_info) merged into the ready answer.
        self.health_extra: Dict[str, Any] = {}
        self.health_rtt_ns = 2_000_000
        self.health_send_ns: Optional[int] = None

    def _resp(self, status: int, body: Any) -> GuestResponse:
        return GuestResponse(status, body, rtt_ns=2_000_000, send_ns=time.time_ns())

    def health(self, address: str, timeout: float = 1.0) -> GuestResponse:
        if address in self.ready:
            body = {"ready": True, "chromium_version": "154.0", "uptime_s": 1.0, **self.health_extra}
            return GuestResponse(200, body, rtt_ns=self.health_rtt_ns,
                                 send_ns=self.health_send_ns if self.health_send_ns is not None else time.time_ns())
        raise GuestError("connection refused")

    def task(self, address: str, body: Dict[str, Any], timeout: float, traceparent: Optional[str] = None) -> GuestResponse:
        self.task_calls.append({"address": address, "body": body, "timeout": timeout, "traceparent": traceparent})
        if self.task_handler is None:
            return self._resp(200, ok_task_body(body))
        status, resp = self.task_handler(address, body, timeout, traceparent)
        return self._resp(status, resp)

    def metrics(self, address: str, timeout: float = 2.0) -> GuestResponse:
        return self._resp(200, dict(self.metrics_body))


def ok_task_body(req: Dict[str, Any]) -> Dict[str, Any]:
    names = ["home", "search", "open_product", "add_to_cart", "verify_cart"]
    steps = []
    t = 0
    for n in names:
        steps.append({"name": n, "dispatch_ns": t, "settle_ns": t + 100_000_000, "duration_ms": 100.0})
        t += 100_000_000
    return {"ok": True, "failure_category": "ok", "steps": steps, "task_ms": 500.0, "bytes_received": 1234,
            "request_count": 12, "screenshot_b64": "", "guest_clock_ns": time.time_ns(),
            "traceparent": None, "log_tail": ["guest line 1", {"level": "WARN", "msg": "guest line 2"}],
            "task_id": req.get("task_id")}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def guest() -> FakeGuest:
    return FakeGuest()


@pytest.fixture
def tel(tmp_path) -> Telemetry:
    t = Telemetry("hostd", str(tmp_path / "logs"), otlp_endpoint=None, stderr=False)
    yield t
    t.close()


@pytest.fixture
def manager(backend, guest, clock, tel) -> SessionManager:
    """Synchronous spawn: create_sessions() returns after every launch has run to completion."""
    return SessionManager(backend, tel, guest=guest, clock=clock.time, sleep=clock.sleep,
                          spawn=lambda fn, name: fn(), host_id="test-host")


@pytest.fixture
def ctx() -> RequestContext:
    return RequestContext()
