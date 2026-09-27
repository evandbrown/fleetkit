"""State transitions, outcomes and the task proxy (design sections 4 and 6)."""
from __future__ import annotations

import json

import pytest

from hostd.guest import GuestError
from hostd.model import MicroVM, Outcome, State, TransitionError


def _state_records(tel):
    tel.close()
    recs = []
    with open(tel.log_dir + "/logs.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["attributes"].get("event") == "microvm.state":
                recs.append(r["attributes"])
    return recs


def test_transition_table():
    s = MicroVM(id="x", slot=0, backend="docker", address="a", vcpus=1, mem_mib=1, fault=None,
                ready_timeout_s=1, max_lifetime_s=1, idle_timeout_s=1, fixture_base_url="")
    assert s.state == State.CREATING
    with pytest.raises(TransitionError):
        s.transition(State.READY, 1.0)
    rec = s.transition(State.BOOTING, 1.0)
    assert rec == {"microvm_id": "x", "from": "creating", "to": "booting", "ts": 1.0, "outcome": None}
    s.transition(State.READY, 2.0)
    s.transition(State.BUSY, 3.0)
    s.transition(State.READY, 4.0)
    with pytest.raises(TransitionError):
        s.transition(State.DESTROYED, 5.0)
    s.transition(State.DESTROYING, 5.0, Outcome.COMPLETED)
    s.transition(State.DESTROYED, 6.0)
    with pytest.raises(TransitionError):
        s.transition(State.READY, 7.0)
    f = MicroVM(id="y", slot=1, backend="docker", address="a", vcpus=1, mem_mib=1, fault=None,
                ready_timeout_s=1, max_lifetime_s=1, idle_timeout_s=1, fixture_base_url="")
    f.transition(State.BOOTING, 1.0)
    f.transition(State.FAILED, 2.0, Outcome.STARTUP_TIMEOUT)
    with pytest.raises(TransitionError):
        f.transition(State.DESTROYING, 3.0)
    z = MicroVM(id="z", slot=2, backend="docker", address="a", vcpus=1, mem_mib=1, fault=None,
                ready_timeout_s=1, max_lifetime_s=1, idle_timeout_s=1, fixture_base_url="")
    with pytest.raises(ValueError):
        z.transition(State.BOOTING, 1.0, "not_an_outcome")


def test_healthy_lifecycle(manager, guest, backend, clock, tel, ctx):
    guest.ready.add("guest-0")
    created = manager.create_microvms({"count": 1, "vcpus": 2, "mem_mib": 2048}, ctx)
    s = manager.get(created[0]["id"])
    assert s.state == State.READY
    assert s.created_ts is not None and s.process_started_ts is not None and s.ready_ts is not None
    assert s.created_ts <= s.process_started_ts <= s.ready_ts
    assert s.startup_ms == pytest.approx((s.ready_ts - s.created_ts) * 1000)
    assert s.last_activity_ts == s.ready_ts
    assert s.outcome is None
    receipt = clock.now
    rec = manager.destroy(s.id)
    assert rec["state"] == State.DESTROYED and rec["outcome"] == Outcome.COMPLETED
    assert rec["destroyed_ts"] is not None and rec["cleanup_ms"] == pytest.approx((clock.now - receipt) * 1000)
    assert backend.destroyed == [s.id]
    # idempotent
    assert manager.destroy(s.id)["state"] == State.DESTROYED
    assert backend.destroyed == [s.id]
    recs = _state_records(tel)
    assert [(r["from"], r["to"]) for r in recs] == [
        (None, "creating"), ("creating", "booting"), ("booting", "ready"), ("ready", "destroying"),
        ("destroying", "destroyed")]
    assert recs[-1]["outcome"] == "completed"
    assert all(r["microvm_id"] == s.id for r in recs)


def test_startup_timeout(manager, guest, backend, clock, ctx):
    # never ready: the poll loop runs until ready_timeout_s, then failed/startup_timeout and cleaned up
    created = manager.create_microvms({"count": 1, "ready_timeout_s": 5}, ctx)
    s = manager.get(created[0]["id"])
    assert s.state == State.FAILED
    assert s.outcome == Outcome.STARTUP_TIMEOUT
    assert clock.now - s.created_ts >= 5
    assert s.destroyed_ts is not None and s.cleanup_ms is not None
    assert backend.destroyed == [s.id]
    assert backend.verify_clean() == []


def test_startup_error_when_process_dies(manager, guest, backend, clock, ctx):
    backend_alive = backend.alive_ids

    class Dying(dict):
        def get(self, key, default=None):
            # the container is gone once the clock has moved a bit
            return clock.now - start < 1.5

    start = clock.now
    backend.alive_ids = Dying()
    created = manager.create_microvms({"count": 1, "ready_timeout_s": 60}, ctx)
    s = manager.get(created[0]["id"])
    assert s.state == State.FAILED and s.outcome == Outcome.STARTUP_ERROR
    assert "exit=1" in s.error
    assert clock.now - start < 5           # caught within seconds, not at ready_timeout_s
    assert backend.destroyed == [s.id]
    backend.alive_ids = backend_alive


def test_task_proxy_success(manager, guest, ctx):
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    status, resp = manager.run_task(sid, {"task_id": "t1", "product_id": "p1", "query": "lamp",
                                          "expected_title": "Lamp"}, ctx)
    assert status == 200 and resp["ok"] is True and resp["failure_category"] == "ok"
    assert resp["microvm_id"] == sid and resp["task_id"] == "t1"
    assert resp["guest_mem_available"] == 12345 and resp["chromium_rss"] == 6789
    assert isinstance(resp["clock_offset_ns"], int)
    assert resp["trace_id"] and len(resp["trace_id"]) == 32
    assert len(resp["steps"]) == 5
    sent = guest.task_calls[0]
    assert sent["body"]["fixture_base_url"] == "http://fixture"
    assert sent["body"]["step_timeout_ms"] == 10000 and sent["body"]["task_timeout_ms"] == 45000
    assert sent["timeout"] == 50.0               # proxy deadline = task_timeout_ms + 5000
    assert sent["traceparent"].startswith("00-" + resp["trace_id"])
    s = manager.get(sid)
    assert s.state == State.READY and s.last_activity_ts is not None


def test_task_proxy_passes_through_guest_failures(manager, guest, ctx):
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    guest.task_handler = lambda a, b, t, tp: (200, {"ok": False, "failure_category": "step_timeout",
                                                    "failed_step": "search", "error": "no results",
                                                    "steps": [{"name": "home", "dispatch_ns": 0, "settle_ns": 5, "duration_ms": 0.0}],
                                                    "guest_clock_ns": 1, "log_tail": []})
    status, resp = manager.run_task(sid, {"task_timeout_ms": 1000}, ctx)
    assert status == 200 and resp["failure_category"] == "step_timeout" and resp["failed_step"] == "search"
    assert guest.task_calls[-1]["timeout"] == 6.0
    assert manager.get(sid).state == State.READY   # the microVM stays usable
    assert manager.get(sid).outcome is None


def test_task_on_not_ready_microvm_is_409(manager, guest, ctx):
    created = manager.create_microvms({"count": 1, "ready_timeout_s": 2}, ctx)  # never ready -> failed
    status, resp = manager.run_task(created[0]["id"], {}, ctx)
    assert status == 409 and resp["failure_category"] == "microvm_not_ready" and resp["ok"] is False
    assert resp["microvm_id"] == created[0]["id"] and resp["microvm_state"] == "failed"
    assert resp["microvm_outcome"] == Outcome.STARTUP_TIMEOUT
    with pytest.raises(Exception):
        manager.run_task("nope", {}, ctx)


def test_hang_task_destroys_microvm(manager, guest, backend, ctx):
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]

    def hang(address, body, timeout, tp):
        raise GuestError("timed out after %ss" % timeout)

    guest.task_handler = hang
    status, resp = manager.run_task(sid, {"task_id": "t-hang"}, ctx)
    assert status == 200 and resp["ok"] is False
    assert resp["failure_category"] == "guest_unreachable" and resp["failed_step"] is None
    assert resp["microvm_outcome"] == Outcome.TASK_FAILURE_DESTROYED
    s = manager.get(sid)
    assert s.state == State.DESTROYED and s.outcome == Outcome.TASK_FAILURE_DESTROYED
    assert backend.destroyed == [sid]


def test_destroy_during_task_reports_lifetime_expired(manager, guest, backend, clock, ctx):
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1, "max_lifetime_s": 30}, ctx)[0]["id"]

    def reaped_mid_task(address, body, timeout, tp):
        clock.advance(31)
        assert manager.reap_once() == [(sid, Outcome.LIFETIME_EXPIRED)]
        raise GuestError("connection reset")

    guest.task_handler = reaped_mid_task
    status, resp = manager.run_task(sid, {}, ctx)
    assert resp["failure_category"] == "guest_unreachable"
    assert resp["microvm_outcome"] == Outcome.LIFETIME_EXPIRED
    s = manager.get(sid)
    assert s.state == State.DESTROYED and s.outcome == Outcome.LIFETIME_EXPIRED
    assert backend.destroyed == [sid]


def test_guest_spans_and_logs_are_emitted(manager, guest, tel, ctx):
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    status, resp = manager.run_task(sid, {"task_id": "t-span"}, ctx)
    tel.close()
    spans = [json.loads(l) for l in open(tel.log_dir + "/spans.jsonl")]
    names = [(s["service"], s["name"]) for s in spans]
    assert ("hostd", "microvm.create") in names and ("hostd", "task.proxy") in names
    assert ("guest-daemon", "guest.task") in names
    assert [n for svc, n in names if n.startswith("step.")] == [
        "step.home", "step.search", "step.open_product", "step.add_to_cart", "step.verify_cart"]
    trace_ids = {s["trace_id"] for s in spans if s["name"] in ("task.proxy", "guest.task") or s["name"].startswith("step.")}
    assert trace_ids == {resp["trace_id"]}
    guest_task = next(s for s in spans if s["name"] == "guest.task")
    assert guest_task["duration_ms"] == pytest.approx(500.0)
    assert guest_task["attributes"]["fleetkit.task_id"] == "t-span"
    logs = [json.loads(l) for l in open(tel.log_dir + "/logs.jsonl")]
    forwarded = [l for l in logs if l["service"] == "guest-daemon"]
    assert [l["body"] for l in forwarded] == ["guest line 1", "guest line 2"]
    assert forwarded[1]["severity"] == "WARN" and forwarded[1]["trace_id"] == resp["trace_id"]


def test_log_tail_from_an_older_guest_keeps_its_severity(manager, guest, tel, ctx):
    # A guest image built before the D56 rename writes the logging module's `level` key.
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    guest.task_handler = lambda a, b, t, tp: (200, {"ok": True, "failure_category": "ok", "steps": [],
                                                    "log_tail": [{"level": "ERROR", "msg": "old guest"}]})
    manager.run_task(sid, {}, ctx)
    tel.close()
    logs = [json.loads(l) for l in open(tel.log_dir + "/logs.jsonl")]
    forwarded = [l for l in logs if l["service"] == "guest-daemon"]
    assert [(l["body"], l["severity"]) for l in forwarded] == [("old guest", "ERROR")]


def test_traceparent_and_baggage_flow(manager, guest, tel, ctx):
    from hostd.manager import RequestContext
    from hostd.telemetry import parse_baggage, parse_traceparent
    tp = parse_traceparent("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
    assert tp.trace_id == "0af7651916cd43dd8448eb211c80319c" and tp.span_id == "b7ad6b7169203331"
    assert parse_traceparent("garbage") is None
    bag = parse_baggage("fleetkit.run_id=run-1,fleetkit.trial_id=trial-2;prop=x")
    assert bag == {"fleetkit.run_id": "run-1", "fleetkit.trial_id": "trial-2"}
    rctx = RequestContext(trace=tp, baggage=bag)
    guest.ready.add("guest-0")
    created = manager.create_microvms({"count": 1}, rctx)
    s = manager.get(created[0]["id"])
    assert s.run_id == "run-1" and s.trial_id == "trial-2"
    assert s.trace.trace_id == tp.trace_id
    status, resp = manager.run_task(s.id, {}, rctx)
    assert resp["trace_id"] == tp.trace_id
    tel.close()
    spans = [json.loads(l) for l in open(tel.log_dir + "/spans.jsonl")]
    create = next(x for x in spans if x["name"] == "microvm.create")
    assert create["parent_span_id"] == tp.span_id
    assert create["attributes"]["fleetkit.run_id"] == "run-1"


def test_rejected_task_body_leaves_microvm_ready(manager, guest, ctx):
    from hostd.manager import ApiError
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    with pytest.raises(ApiError) as ei:
        manager.run_task(sid, {"task_timeout_ms": "abc"}, ctx)
    assert ei.value.status == 400
    s = manager.get(sid)
    assert s.state == State.READY                # not stuck busy until max_lifetime_s
    assert guest.task_calls == []                # nothing reached the guest
    # and the microVM is still usable afterwards
    status, resp = manager.run_task(sid, {"task_id": "t-after"}, ctx)
    assert status == 200 and resp["ok"] is True and manager.get(sid).state == State.READY


def test_malformed_guest_answer_leaves_microvm_ready(manager, guest, ctx):
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    # settle_ns that int() cannot parse: a bug in span reconstruction must not hold the slot
    guest.task_handler = lambda a, b, t, tp: (200, {"ok": True, "failure_category": "ok", "task_ms": 1.0,
                                                    "steps": [{"name": "home", "settle_ns": "garbage"}],
                                                    "guest_clock_ns": 1})
    with pytest.raises(ValueError):
        manager.run_task(sid, {}, ctx)
    assert manager.get(sid).state == State.READY


def test_destroy_during_create_does_not_leak(backend, guest, clock, tel, ctx):
    """destroy() while the launch thread is inside backend.create(): the container that
    docker run returns afterwards must still be removed (design: cleanup_ms = every
    per-microVM leftover gone; verify-clean must pass)."""
    import threading
    from hostd.manager import Manager

    entered = threading.Event()
    release = threading.Event()

    class BlockingBackend(type(backend)):
        def create(self, microvm):
            entered.set()
            assert release.wait(timeout=10)
            super().create(microvm)

        def destroy(self, microvm):
            # like `docker rm -f` on a name that does not exist yet: a no-op
            if microvm.id in self.created:
                super().destroy(microvm)

    blocking = BlockingBackend()
    threads = []

    def spawn(fn, name):
        t = threading.Thread(target=fn, name=name, daemon=True)
        threads.append(t)
        t.start()

    manager = Manager(blocking, tel, guest=guest, clock=clock.time, sleep=clock.sleep,
                             spawn=spawn, host_id="test-host")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    assert entered.wait(timeout=10)
    rec = manager.destroy(sid)                    # a DELETE while the backend is still creating
    assert rec["state"] == State.DESTROYED
    assert blocking.destroyed == []               # nothing existed to remove at that moment
    release.set()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    s = manager.get(sid)
    assert s.state == State.DESTROYED and s.outcome == Outcome.COMPLETED
    assert blocking.created == [sid] and blocking.destroyed == [sid]
    assert blocking.verify_clean() == []
    guest.ready.add("guest-0")
    assert manager.create_microvms({"count": 1}, ctx)[0]["slot"] == 0   # the slot was freed
    for t in threads:
        t.join(timeout=10)
    manager.destroy_all()
