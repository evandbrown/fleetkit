"""Lifetime and idle enforcement (design section 6: idle and lifetime)."""
from __future__ import annotations

from hostd.model import Outcome, State


def _ready_microvm(manager, guest, ctx, **request):
    guest.ready.update({"guest-%d" % i for i in range(4)})
    return manager.get(manager.create_microvms({"count": 1, **request}, ctx)[0]["id"])


def test_idle_expired(manager, guest, backend, clock, ctx):
    s = _ready_microvm(manager, guest, ctx, idle_timeout_s=5, max_lifetime_s=600)
    clock.advance(4.9)
    assert manager.reap_once() == []
    assert s.state == State.READY
    clock.advance(0.2)
    assert manager.reap_once() == [(s.id, Outcome.IDLE_EXPIRED)]
    assert s.state == State.DESTROYED and s.outcome == Outcome.IDLE_EXPIRED
    assert s.cleanup_ms is not None and backend.destroyed == [s.id]
    assert manager.reap_once() == []            # nothing left to reap, no double destroy
    assert backend.destroyed == [s.id]


def test_activity_resets_idle(manager, guest, backend, clock, ctx):
    s = _ready_microvm(manager, guest, ctx, idle_timeout_s=5)
    clock.advance(4)
    manager.run_task(s.id, {}, ctx)             # sets last_activity_ts at start and end
    clock.advance(4)
    assert manager.reap_once() == []
    clock.advance(1.5)
    assert manager.reap_once() == [(s.id, Outcome.IDLE_EXPIRED)]


def test_busy_microvms_are_skipped_by_idle_but_not_lifetime(manager, guest, backend, clock, ctx):
    s = _ready_microvm(manager, guest, ctx, idle_timeout_s=5, max_lifetime_s=20)
    with s.lock:
        manager._transition(s, State.BUSY)       # a task in flight
    clock.advance(10)
    assert manager.reap_once() == []
    assert s.state == State.BUSY
    clock.advance(11)
    assert manager.reap_once() == [(s.id, Outcome.LIFETIME_EXPIRED)]
    assert s.state == State.DESTROYED and s.outcome == Outcome.LIFETIME_EXPIRED


def test_lifetime_expired_from_creation(manager, guest, backend, clock, ctx):
    s = _ready_microvm(manager, guest, ctx, idle_timeout_s=600, max_lifetime_s=8)
    manager.run_task(s.id, {}, ctx)
    clock.advance(7)
    manager.run_task(s.id, {}, ctx)             # activity does not extend the lifetime
    clock.advance(1.1)
    assert manager.reap_once() == [(s.id, Outcome.LIFETIME_EXPIRED)]
    assert s.outcome == Outcome.LIFETIME_EXPIRED
    assert backend.verify_clean() == []


def test_lifetime_applies_while_booting(manager, guest, backend, clock, ctx):
    """A microVM that is still booting when its lifetime ends is torn down with lifetime_expired."""
    calls = {"n": 0}
    original_health = guest.health

    def slow_health(address, timeout=1.0):
        calls["n"] += 1
        if calls["n"] == 3:
            clock.advance(100)
            manager.reap_once()                  # reaper fires while the poll loop is running
        return original_health(address, timeout)

    guest.health = slow_health
    created = manager.create_microvms({"count": 1, "ready_timeout_s": 300, "max_lifetime_s": 50}, ctx)
    s = manager.get(created[0]["id"])
    assert s.state == State.DESTROYED and s.outcome == Outcome.LIFETIME_EXPIRED
    assert s.ready_ts is None
    assert backend.destroyed == [s.id]


def test_terminal_microvms_are_never_reaped(manager, guest, backend, clock, ctx):
    s = _ready_microvm(manager, guest, ctx, idle_timeout_s=1, max_lifetime_s=2)
    manager.destroy(s.id)
    clock.advance(100)
    assert manager.reap_once() == []
    assert backend.destroyed == [s.id]
