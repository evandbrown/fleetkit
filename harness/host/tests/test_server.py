"""The HTTP API end to end: real server, real guest client, in-process stub guest, fake backend."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubguest"))
from stubguestd import StubGuest  # noqa: E402

from hostd.manager import SessionManager  # noqa: E402
from hostd.metrics import HostSampler  # noqa: E402
from hostd.server import HostdApp, Server  # noqa: E402

from conftest import FakeBackend  # noqa: E402


class StubBackend(FakeBackend):
    """Every slot points at one in-process stub guest."""

    def __init__(self, guest_address: str):
        super().__init__()
        self.guest_address = guest_address

    def address(self, slot: int) -> str:
        return self.guest_address


def _call(base, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _wait(base, sid, states, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, rec = _call(base, "GET", "/sessions/" + sid)
        if rec["state"] in states:
            return rec
        time.sleep(0.05)
    raise AssertionError("session %s did not reach %s: %s" % (sid, states, rec))


@pytest.fixture
def stack(tel):
    guest = StubGuest(fault=None, step_ms=5).start()
    backend = StubBackend(guest.address)
    manager = SessionManager(backend, tel, poll_interval=0.05, host_id="test")
    sampler = HostSampler(manager, tel, host_id="test")
    server = Server(HostdApp(manager, tel, sampler, "test"), port=0)
    server.start_background()
    manager.start_reaper(period=0.2)
    base = "http://127.0.0.1:%d" % server.port
    yield {"base": base, "guest": guest, "backend": backend, "manager": manager}
    manager.stop_reaper()
    server.shutdown()
    guest.stop()


def test_health_and_routes(stack):
    base = stack["base"]
    status, body = _call(base, "GET", "/health")
    assert status == 200 and body["ok"] and body["backend"] == "docker"
    assert _call(base, "GET", "/nope")[0] == 404
    assert _call(base, "GET", "/sessions/none")[0] == 404
    status, body = _call(base, "POST", "/sessions", {"count": "x"})
    assert status == 400
    status, body = _call(base, "GET", "/host/verify-clean")
    assert status == 200 and body == {"clean": True, "leftovers": [], "backend": "docker"}
    status, body = _call(base, "GET", "/host/metrics")
    assert status == 200 and "mem_total" in body and body["sessions"] == [] and "psi" in body


def test_session_task_and_cleanup_over_http(stack):
    base = stack["base"]
    status, created = _call(base, "POST", "/sessions", {"backend": "docker", "count": 2, "vcpus": 1, "mem_mib": 256},
                            headers={"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
                                     "X-Fleetkit-Run-Id": "r1", "baggage": "fleetkit.trial_id=t1"})
    assert status == 200 and len(created) == 2 and {c["slot"] for c in created} == {0, 1}
    recs = [_wait(base, c["id"], ("ready", "failed")) for c in created]
    assert all(r["state"] == "ready" for r in recs)
    assert all(r["run_id"] == "r1" and r["trial_id"] == "t1" for r in recs)
    assert all(r["startup_ms"] is not None and r["process_started_ts"] >= r["created_ts"] for r in recs)
    assert all(r["trace_id"] == "0af7651916cd43dd8448eb211c80319c" for r in recs)
    _, listing = _call(base, "GET", "/sessions")
    assert len(listing) == 2

    sid = created[0]["id"]
    t0 = time.monotonic()
    status, task = _call(base, "POST", "/sessions/%s/task" % sid,
                         {"task_id": "task-1", "product_id": "p1", "query": "lamp", "expected_title": "Lamp"})
    wall_ms = (time.monotonic() - t0) * 1000
    assert status == 200 and task["ok"] is True and task["failure_category"] == "ok"
    assert [s["name"] for s in task["steps"]] == ["home", "search", "open_product", "add_to_cart", "verify_cart"]
    assert task["task_ms"] < wall_ms
    assert task["guest_mem_available"] == 2 ** 30 and task["chromium_rss"] == 150 * 2 ** 20
    assert task["session_id"] == sid and task["clock_offset_ns"] is not None
    assert task["screenshot_b64"]

    status, metrics = _call(base, "GET", "/host/metrics")
    assert {s["id"] for s in metrics["sessions"]} == {c["id"] for c in created}

    status, gone = _call(base, "DELETE", "/sessions/" + sid)
    assert status == 200 and gone["state"] == "destroyed" and gone["outcome"] == "completed"
    assert gone["cleanup_ms"] is not None
    status, again = _call(base, "DELETE", "/sessions/" + sid)
    assert status == 200 and again["state"] == "destroyed"
    status, resp = _call(base, "POST", "/sessions/%s/task" % sid, {})
    assert status == 409 and resp["failure_category"] == "session_not_ready"
    _call(base, "DELETE", "/sessions/" + created[1]["id"])
    status, clean = _call(base, "GET", "/host/verify-clean")
    assert clean["clean"] is True


def test_boot_phases_and_guest_info_over_http(tel):
    """The real guest client against the stub guest's /health: phases in boot order, on the host clock."""
    guest = StubGuest(fault=None, ready_after_s=0.3, chromium_launch_s=0.05, kernel_boot_s=0.4).start()
    backend = StubBackend(guest.address)
    manager = SessionManager(backend, tel, poll_interval=0.05, host_id="test")
    server = Server(HostdApp(manager, tel, None, "test"), port=0)
    server.start_background()
    base = "http://127.0.0.1:%d" % server.port
    try:
        t0 = time.time()
        sid = _call(base, "POST", "/sessions", {"count": 1})[1][0]["id"]
        rec = _wait(base, sid, ("ready",))
        k, g, cl, cr = rec["kernel_start_ts"], rec["guestd_start_ts"], rec["chromium_launch_ts"], rec["chromium_ready_ts"]
        assert None not in (k, g, cl, cr)
        assert k < g < cl < cr <= rec["ready_ts"] + 0.05
        assert g - k == pytest.approx(0.4, abs=0.01)       # stub: kernel ran 0.4 s before the daemon
        assert cr - cl == pytest.approx(0.25, abs=0.01)
        assert g < t0 + 0.05                               # the stub daemon started before the session
        info = rec["guest_info"]
        assert info["guestd_version"] == "stub-0" and info["chromium_version"] == "stub-0"
        assert isinstance(info["chromium_flags"], list) and info["kernel_cmdline"].startswith("console=ttyS0")
        assert info["vcpus"] >= 1 and info["mem_total"] == 2 ** 31
    finally:
        manager.destroy_all()
        server.shutdown()
        guest.stop()


def test_host_info_route_is_cached(tel):
    from hostd.hostinfo import collect_host_info
    backend = FakeBackend()
    manager = SessionManager(backend, tel, host_id="test")
    sampler = HostSampler(manager, tel, period=0.2, host_id="test")
    calls = []

    def info():
        calls.append(1)
        # IMDS at a closed local port: off-EC2 behaviour without touching the network
        return collect_host_info(backend, "test", sampler.period, imds_url="http://127.0.0.1:9")

    server = Server(HostdApp(manager, tel, sampler, "test", host_info=info), port=0)
    server.start_background()
    base = "http://127.0.0.1:%d" % server.port
    try:
        status, body = _call(base, "GET", "/host/info")
        assert status == 200
        assert body["host_id"] == "test" and body["backend"] == "docker" and body["metrics_period_s"] == 0.2
        assert body["ec2"] is None and body["firecracker"] is None
        assert _call(base, "GET", "/host/info") == (200, body)
        assert len(calls) == 1
    finally:
        server.shutdown()


def test_hang_task_over_http(tel):
    guest = StubGuest(fault="hang_task").start()
    backend = StubBackend(guest.address)
    manager = SessionManager(backend, tel, poll_interval=0.05, host_id="test")
    server = Server(HostdApp(manager, tel, None, "test"), port=0)
    server.start_background()
    base = "http://127.0.0.1:%d" % server.port
    try:
        sid = _call(base, "POST", "/sessions", {"count": 1})[1][0]["id"]
        _wait(base, sid, ("ready",))
        t0 = time.monotonic()
        status, task = _call(base, "POST", "/sessions/%s/task" % sid, {"task_timeout_ms": 200})
        elapsed = time.monotonic() - t0
        assert status == 200 and task["failure_category"] == "guest_unreachable" and task["failed_step"] is None
        assert 5.0 <= elapsed < 8.0                      # proxy deadline = 0.2 s + 5 s margin
        assert task["session_outcome"] == "task_failure_destroyed"
        rec = _wait(base, sid, ("destroyed",))
        assert rec["outcome"] == "task_failure_destroyed"
        assert backend.destroyed == [sid]
    finally:
        server.shutdown()
        guest.stop()


def test_step_faults_keep_session_ready(tel):
    for fault in ("hang_step", "slow_step:300"):
        guest = StubGuest(fault=fault).start()
        backend = StubBackend(guest.address)
        manager = SessionManager(backend, tel, poll_interval=0.05, host_id="test")
        server = Server(HostdApp(manager, tel, None, "test"), port=0)
        server.start_background()
        base = "http://127.0.0.1:%d" % server.port
        try:
            sid = _call(base, "POST", "/sessions", {"count": 1})[1][0]["id"]
            _wait(base, sid, ("ready",))
            status, task = _call(base, "POST", "/sessions/%s/task" % sid, {"step_timeout_ms": 100, "task_timeout_ms": 2000})
            assert status == 200 and task["failure_category"] == "step_timeout" and task["failed_step"] == "search"
            assert _call(base, "GET", "/sessions/" + sid)[1]["state"] == "ready"
        finally:
            manager.destroy_all()
            server.shutdown()
            guest.stop()


def test_never_ready_over_http(tel):
    guest = StubGuest(fault="never_ready").start()
    backend = StubBackend(guest.address)
    manager = SessionManager(backend, tel, poll_interval=0.05, host_id="test")
    server = Server(HostdApp(manager, tel, None, "test"), port=0)
    server.start_background()
    base = "http://127.0.0.1:%d" % server.port
    try:
        sid = _call(base, "POST", "/sessions", {"count": 1, "ready_timeout_s": 1})[1][0]["id"]
        rec = _wait(base, sid, ("failed",), timeout=5)
        assert rec["outcome"] == "startup_timeout"
        deadline = time.monotonic() + 5
        while rec["destroyed_ts"] is None and time.monotonic() < deadline:
            time.sleep(0.05)
            rec = _call(base, "GET", "/sessions/" + sid)[1]
        assert rec["destroyed_ts"] is not None and rec["cleanup_ms"] is not None
        assert rec["state"] == "failed"
        assert _call(base, "GET", "/host/verify-clean")[1]["clean"] is True
    finally:
        server.shutdown()
        guest.stop()


def test_idle_and_lifetime_over_http(stack):
    base = stack["base"]
    a = _call(base, "POST", "/sessions", {"count": 1, "idle_timeout_s": 1, "max_lifetime_s": 600})[1][0]["id"]
    b = _call(base, "POST", "/sessions", {"count": 1, "idle_timeout_s": 600, "max_lifetime_s": 1.5})[1][0]["id"]
    _wait(base, a, ("ready",))
    _wait(base, b, ("ready",))
    ra = _wait(base, a, ("destroyed",), timeout=5)
    rb = _wait(base, b, ("destroyed",), timeout=5)
    assert ra["outcome"] == "idle_expired"
    assert rb["outcome"] == "lifetime_expired"
    assert _call(base, "GET", "/host/verify-clean")[1]["clean"] is True


def test_listen_backlog_absorbs_a_burst_of_task_requests():
    # With socketserver's default backlog of 5, a burst of N simultaneous task requests on a
    # saturated host overflowed it and some tasks started a second late (SYN retransmit).
    from hostd.server import _HTTPServer
    assert _HTTPServer.request_queue_size >= 256
    assert _HTTPServer.daemon_threads is True
