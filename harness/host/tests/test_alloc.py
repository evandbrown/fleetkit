"""Slot, port, address, MAC and IP allocation (design section 3)."""
from __future__ import annotations

import pytest

from hostd.backends.docker import PORT_BASE, PORT_LAST, DockerBackend
from hostd.backends.firecracker import FirecrackerBackend
from hostd.manager import ApiError
from hostd.runner import Runner


def test_docker_ports_by_slot():
    b = DockerBackend(Runner(dry_run=True), "/tmp/x")
    assert b.port(0) == 18080
    assert b.port(119) == 18199
    assert b.address(5) == "127.0.0.1:18085"
    assert b.max_slots == PORT_LAST - PORT_BASE + 1 == 120
    with pytest.raises(ValueError):
        b.port(120)


def test_firecracker_slot_naming():
    b = FirecrackerBackend(Runner(dry_run=True), "/tmp/x")
    assert b.guest_ip(0) == "10.200.0.10"
    assert b.guest_ip(7) == "10.200.0.17"
    assert b.guest_mac(0) == "06:00:0A:C8:00:0A"
    assert b.guest_mac(7) == "06:00:0A:C8:00:11"
    assert b.guest_mac(235) == "06:00:0A:C8:00:F5"
    assert b.tap(3) == "fc-3"
    assert b.unit(3) == "fc-vm3"
    assert b.address(3) == "10.200.0.13:8080"
    assert b.ip_arg(3) == "ip=10.200.0.13::10.200.0.1:255.255.255.0:vm3:eth0:off:10.42.0.2"
    with pytest.raises(ValueError):
        b.guest_ip(245)


def test_slots_are_lowest_free_and_recycled(manager, guest, ctx):
    guest.ready.update({"guest-0", "guest-1", "guest-2", "guest-3"})
    created = manager.create_microvms({"count": 2}, ctx)
    assert [c["slot"] for c in created] == [0, 1]
    assert created[0]["address"] == "guest-0"
    assert created[0]["id"].startswith("s000-")
    more = manager.create_microvms({"count": 2}, ctx)
    assert [c["slot"] for c in more] == [2, 3]
    with pytest.raises(ApiError) as e:
        manager.create_microvms({"count": 1}, ctx)
    assert e.value.status == 409
    manager.destroy(created[1]["id"])
    again = manager.create_microvms({"count": 1}, ctx)
    assert again[0]["slot"] == 1


def test_create_validates_input(manager, ctx):
    for bad in ({"count": 0}, {"vcpus": "two"}, {"fault": "explode"}, {"fault": "slow_step"},
                {"fault": "hang_task:1"}, {"backend": "firecracker"}):
        with pytest.raises(ApiError) as e:
            manager.create_microvms(bad, ctx)
        assert e.value.status == 400, bad


def test_slot_freed_when_create_fails(manager, backend, ctx):
    backend.fail_create = "docker run failed: no such image"
    created = manager.create_microvms({"count": 1}, ctx)
    s = manager.get(created[0]["id"])
    assert s.state == "failed" and s.outcome == "startup_error"
    assert s.destroyed_ts is not None and s.cleanup_ms is not None
    backend.fail_create = None
    assert manager.create_microvms({"count": 1}, ctx)[0]["slot"] == 0


def test_unusable_slots_are_skipped(manager, backend, guest, ctx):
    guest.ready.update({"guest-0", "guest-1", "guest-2", "guest-3"})
    backend.slot_usable = lambda slot: slot != 0          # e.g. port 18080 held by another process
    created = manager.create_microvms({"count": 2}, ctx)
    assert [c["slot"] for c in created] == [1, 2]


def test_docker_slot_probe_detects_a_held_port():
    import socket
    from hostd.runner import Runner as R
    b = DockerBackend(R(dry_run=False), "/tmp/x")
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        b.port = lambda slot: port                          # point slot 0 at the held port
        assert b.slot_usable(0) is False
    finally:
        holder.close()
    assert b.slot_usable(0) is True
