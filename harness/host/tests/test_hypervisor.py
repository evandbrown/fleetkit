"""The spec's hypervisor section: carried on every microVM, refused when the backend can't carry it out."""
from __future__ import annotations

import pytest

from hostd.hostinfo import collect_host_info
from hostd.manager import ApiError
from hostd.model import DEFAULT_HYPERVISOR_OPTIONS, hypervisor_options, hypervisor_problems


class _Backend:
    name = "firecracker"


class _Offers(_Backend):
    hypervisor_options = {"virtio_transport": ["mmio", "pci"], "virtio_rng": [False, True]}


def test_a_backend_without_options_offers_only_the_old_setup():
    assert hypervisor_options(_Backend()) == DEFAULT_HYPERVISOR_OPTIONS
    assert hypervisor_problems(_Backend(), None) == []
    assert hypervisor_problems(_Backend(), {"name": "firecracker", "virtio_transport": "mmio",
                                            "virtio_rng": False}) == []
    probs = hypervisor_problems(_Backend(), {"name": "firecracker", "virtio_transport": "pci", "virtio_rng": True})
    assert probs == ['the firecracker backend can\'t carry out virtio_transport = "pci" (it offers "mmio")',
                     "the firecracker backend can't carry out virtio_rng = true (it offers false)"]


def test_name_and_unknown_options_are_refused():
    assert hypervisor_problems(_Backend(), {"name": "cloud-hypervisor"}) == \
        ["this daemon runs firecracker, not cloud-hypervisor"]
    assert hypervisor_problems(_Backend(), {"virtio_balloon": True}) == \
        ["the firecracker backend has no option virtio_balloon"]
    assert hypervisor_problems(_Backend(), "firecracker") == ["hypervisor must be an object"]
    # 0 is not false: JSON types are compared too
    assert hypervisor_problems(_Offers(), {"virtio_rng": 0})
    assert hypervisor_problems(_Offers(), {"virtio_transport": "pci", "virtio_rng": True}) == []


def test_the_manager_records_the_section_on_each_microvm(manager, guest, ctx):
    guest.ready.add("guest-0")
    created = manager.create_microvms({"count": 1, "hypervisor": {"name": "docker"}}, ctx)
    rec = manager.get(created[0]["id"]).record()
    assert rec["hypervisor"] == {"name": "docker"}
    guest.ready.add("guest-1")
    created = manager.create_microvms({"count": 1}, ctx)
    assert manager.get(created[0]["id"]).hypervisor == {"name": "docker"}


def test_the_manager_refuses_what_the_backend_cannot_do(manager, ctx):
    with pytest.raises(ApiError) as e:
        manager.create_microvms({"count": 1, "hypervisor": {"name": "docker", "virtio_rng": True}}, ctx)
    assert e.value.status == 400 and "virtio_rng" in str(e.value)
    with pytest.raises(ApiError):
        manager.create_microvms({"count": 1, "hypervisor": {"name": "cloud-hypervisor"}}, ctx)
    assert manager.list_records() == []


def test_host_info_says_what_a_spec_may_ask(backend, tmp_path):
    info = collect_host_info(backend, "local", 1.0, proc_root=str(tmp_path), sys_cpu_root=str(tmp_path),
                             dev_kvm=str(tmp_path / "kvm"), imds_url="http://127.0.0.1:9", imds_timeout_s=0.1,
                             lscpu=None)
    assert info["max_slots"] == 4
    assert info["hypervisor_options"] == DEFAULT_HYPERVISOR_OPTIONS
