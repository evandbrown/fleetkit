"""The spec's guest console and memory pages (microvm.console, microvm.memory_pages) on their way to a
microVM: checked in the create request against what the backend offers, kept on the record, rendered into
each hypervisor's kernel command line and memory setting, passed to a docker guest in its environment, and
absent everywhere by default, so a default microVM is what it always was. Plus the host's transparent huge
page mode in GET /host/info and the THP figure in each hypervisor microVM's sample."""
from __future__ import annotations

import json
import os
import shlex

import pytest

from hostd.backends import CloudHypervisorBackend, FirecrackerBackend
from hostd.backends.docker import DockerBackend
from hostd.guest import guest_info
from hostd.hostinfo import collect_host_info, selected, thp_facts
from hostd.manager import ApiError, Manager
from hostd.model import (CONSOLE_BOOT_ARGS, CONSOLES, DEFAULT_MICROVM_OPTIONS, MEMORY_PAGES, MicroVM,
                         microvm_option_problems, microvm_options)
from hostd.runner import Runner

from conftest import FakeBackend

IP3 = "ip=10.200.0.13::10.200.0.1:255.255.255.0:vm3:eth0:off:10.42.0.2"
QUIET = "quiet loglevel=3"
I8042 = "i8042.noaux i8042.nomux i8042.dumbkbd"


def _microvm(backend, slot=3, console="verbose", memory_pages="4k", fault=None, **kw):
    return MicroVM(id="s%03d-abcdef01" % slot, slot=slot, backend=backend.name, address=backend.address(slot),
                   vcpus=2, mem_mib=2048, fault=fault, ready_timeout_s=60, max_lifetime_s=600, idle_timeout_s=120,
                   fixture_base_url=backend.fixture_base_url, hypervisor={"name": backend.name},
                   console=console, memory_pages=memory_pages, **kw)


def _fc():
    return FirecrackerBackend(Runner(dry_run=True), "/var/log/fleetkit", firecracker_bin="/usr/local/bin/firecracker",
                              kernel="/var/lib/fleetkit/vmlinux", rootfs="/var/lib/fleetkit/guest.ext4")


def _ch():
    return CloudHypervisorBackend(Runner(dry_run=True), "/var/log/fleetkit", kernel="/var/lib/fleetkit/vmlinux",
                                  rootfs="/var/lib/fleetkit/guest.ext4", arch="x86_64")


def _ch_flags(b, s):
    argv = b.hypervisor_argv(s)
    return dict(zip(argv[1::2], argv[2::2]))


# --- Firecracker ---------------------------------------------------------------------------

def test_firecracker_default_is_what_every_run_before_booted():
    b = _fc()
    for s in (_microvm(b), _microvm(b, console="verbose", memory_pages="4k")):
        cfg = b.vm_config(s)
        assert cfg["boot-source"]["boot_args"] == "console=ttyS0 reboot=k panic=1 pci=off " + IP3
        assert cfg["machine-config"] == {"vcpu_count": 2, "mem_size_mib": 2048, "smt": False}


@pytest.mark.parametrize("console, words", [("quiet", QUIET), ("quiet-i8042", QUIET + " " + I8042)])
def test_firecracker_console_words_follow_its_own_and_come_before_ip(console, words):
    b = _fc()
    s = _microvm(b, console=console)
    assert b.boot_args(s) == "console=ttyS0 reboot=k panic=1 pci=off %s %s" % (words, IP3)
    # after the fault and the extra flags too, which follow ip=
    s2 = _microvm(b, console=console, fault="hang_task", chromium_extra_flags=["--no-zygote"])
    assert b.boot_args(s2).startswith("console=ttyS0 reboot=k panic=1 pci=off %s %s fleetkit.fault=hang_task " % (
        words, IP3))
    # on pci: the same words, no pci=off
    pci = _microvm(b, console=console)
    pci.hypervisor = {"name": "firecracker", "virtio_transport": "pci"}
    assert b.boot_args(pci) == "console=ttyS0 reboot=k panic=1 %s %s" % (words, IP3)
    assert b.sidecar(s)["console"] == console and b.sidecar(s)["memory_pages"] == "4k"


def test_firecracker_thp_is_machine_config_huge_pages_transparent():
    b = _fc()
    s = _microvm(b, console="quiet", memory_pages="thp")
    cfg = b.vm_config(s)
    assert cfg["machine-config"] == {"vcpu_count": 2, "mem_size_mib": 2048, "smt": False, "huge_pages": "Transparent"}
    assert cfg["boot-source"]["boot_args"] == "console=ttyS0 reboot=k panic=1 pci=off %s %s" % (QUIET, IP3)
    b.create(s)
    written = [e for e in b.runner.rendered if e["kind"] == "write" and e["text"].endswith("/vm.json")]
    assert json.loads(written[0]["content"]) == cfg
    sidecar = [e for e in b.runner.rendered if e["kind"] == "write" and e["text"].endswith("/microvm.json")]
    assert json.loads(sidecar[0]["content"])["memory_pages"] == "thp"
    # the memory setting alone leaves the command line as it was
    assert b.boot_args(_microvm(b, memory_pages="thp")) == b.boot_args(_microvm(b))


# --- Cloud Hypervisor ----------------------------------------------------------------------

def test_cloud_hypervisor_default_is_what_every_run_before_booted():
    b = _ch()
    f = _ch_flags(b, _microvm(b))
    assert f["--cmdline"] == "console=ttyS0 reboot=k panic=1 root=/dev/vda ro " + IP3
    assert f["--memory"] == "size=2048M,thp=off,hugepages=off,shared=off,prefault=off,mergeable=off"


@pytest.mark.parametrize("console, words", [("quiet", QUIET), ("quiet-i8042", QUIET + " " + I8042)])
def test_cloud_hypervisor_console_and_thp(console, words):
    b = _ch()
    f = _ch_flags(b, _microvm(b, console=console, memory_pages="thp"))
    assert f["--cmdline"] == "console=ttyS0 reboot=k panic=1 root=/dev/vda ro %s %s" % (words, IP3)
    assert f["--memory"] == "size=2048M,thp=on,hugepages=off,shared=off,prefault=off,mergeable=off"
    assert _ch_flags(b, _microvm(b, memory_pages="4k"))["--memory"].startswith("size=2048M,thp=off,")


def test_the_same_console_words_on_both_hypervisors():
    fc, ch = _fc(), _ch()
    for console in CONSOLES:
        want = CONSOLE_BOOT_ARGS[console]
        for b in (fc, ch):
            args = b.boot_args(_microvm(b, console=console)).split()
            base = len(b.base_boot_args(_microvm(b)))
            assert args[base:base + len(want)] == want and args[base + len(want)] == IP3, (b.name, console)


# --- docker ------------------------------------------------------------------------------

def test_docker_passes_a_console_in_the_environment_and_ignores_the_pages():
    b = DockerBackend(Runner(dry_run=True), "/tmp/logs")
    plain = shlex.join(b.run_argv(_microvm(b)))
    assert "FLEETKIT_CONSOLE" not in plain
    assert shlex.join(b.run_argv(_microvm(b, memory_pages="thp"))) == plain
    for console in ("quiet", "quiet-i8042"):
        text = shlex.join(b.run_argv(_microvm(b, console=console)))
        assert text == plain.replace(" fleetkit-guest:dev", " -e FLEETKIT_CONSOLE=%s fleetkit-guest:dev" % console)
    assert microvm_options(b) == {"console": list(CONSOLES), "memory_pages": list(MEMORY_PAGES)}


# --- the create request -------------------------------------------------------------------

def test_create_keeps_them_on_the_record(manager, guest, ctx, backend):
    backend.microvm_options = {"console": list(CONSOLES), "memory_pages": list(MEMORY_PAGES)}
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1, "console": "quiet-i8042", "memory_pages": "thp"}, ctx)[0]["id"]
    rec = manager.get(sid).record()
    assert (rec["console"], rec["memory_pages"]) == ("quiet-i8042", "thp")


def test_absent_or_null_is_the_default(manager, guest, ctx):
    guest.ready.add("guest-0")
    guest.ready.add("guest-1")
    a = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    b = manager.create_microvms({"count": 1, "console": None, "memory_pages": None}, ctx)[0]["id"]
    for sid in (a, b):
        rec = manager.get(sid).record()
        assert (rec["console"], rec["memory_pages"]) == ("verbose", "4k")


@pytest.mark.parametrize("request_, message", [
    ({"console": "silent"}, "console must be one of verbose, quiet, quiet-i8042"),
    ({"console": 3}, "console must be one of"),
    ({"memory_pages": "2M"}, "memory_pages must be one of 4k, thp"),
    ({"console": "quiet"}, "the docker backend can't carry out console = quiet (it offers verbose)"),
    ({"memory_pages": "thp"}, "the docker backend can't carry out memory_pages = thp (it offers 4k)"),
])
def test_what_the_backend_cannot_carry_out_is_refused(manager, ctx, request_, message):
    # FakeBackend has no microvm_options: like a backend written before the fields, it offers the defaults only
    with pytest.raises(ApiError) as e:
        manager.create_microvms({"count": 1, **request_}, ctx)
    assert e.value.status == 400 and message in e.value.message
    assert not manager.microvms


def test_option_problems_and_offers():
    assert microvm_options(FakeBackend()) == DEFAULT_MICROVM_OPTIONS
    assert microvm_options(_fc()) == microvm_options(_ch()) == {"console": list(CONSOLES),
                                                                "memory_pages": list(MEMORY_PAGES)}
    assert microvm_option_problems(_fc(), {"console": "quiet-i8042", "memory_pages": "thp"}) == []
    assert microvm_option_problems(_fc(), {}) == []


def test_docker_notes_that_it_ignores_huge_pages(tmp_path, guest, clock, tel, ctx):
    b = DockerBackend(Runner(dry_run=True), str(tmp_path))
    m = Manager(b, tel, guest=guest, clock=clock.time, sleep=clock.sleep, spawn=lambda fn, name: fn(),
                host_id="test-host", dry_run=True)
    m.create_microvms({"count": 1, "memory_pages": "thp"}, ctx)
    m.create_microvms({"count": 1}, ctx)
    tel.close()
    logs = [json.loads(line) for line in open(os.path.join(tel.log_dir, "logs.jsonl"))]
    notes = [r for r in logs if "memory_pages thp" in str(r.get("body", ""))]
    assert len(notes) == 1 and notes[0]["severity"] == "WARN" and "ignored on the docker backend" in notes[0]["body"]


# --- what the guest said and what the host is -----------------------------------------------

def test_guest_info_keeps_the_console_only_when_the_guest_sends_one():
    body = {"ready": True, "kernel_cmdline": "console=ttyS0", "console": None}
    assert "console" not in guest_info(body)
    assert guest_info({**body, "console": "quiet"})["console"] == "quiet"
    assert guest_info({})["kernel_cmdline"] is None and "console" not in guest_info({})


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def test_the_hosts_transparent_huge_page_mode(tmp_path):
    thp = str(tmp_path / "thp")
    assert thp_facts(thp) is None                                   # macOS, or a kernel without THP
    _write(thp + "/enabled", "always [madvise] never\n")
    _write(thp + "/defrag", "always defer defer+madvise [madvise] never\n")
    assert thp_facts(thp) == {"enabled": "madvise", "defrag": "madvise"}
    _write(thp + "/enabled", "[always] madvise never\n")
    os.remove(thp + "/defrag")
    assert thp_facts(thp) == {"enabled": "always", "defrag": None}
    assert selected("always madvise [never]") == "never" and selected("") is None and selected(None) is None
    info = collect_host_info(FakeBackend(), "h", 1.0, sys_thp=thp, imds_url="http://127.0.0.1:9", imds_timeout_s=0.01,
                             lscpu=lambda: None)
    assert info["transparent_hugepage"] == {"enabled": "always", "defrag": None}
    assert info["microvm_options"] == DEFAULT_MICROVM_OPTIONS


def test_a_hypervisor_sample_reads_the_scopes_huge_pages(tmp_path):
    cg = tmp_path / "cgroup" / "system.slice" / "fc-vm3.scope"
    _write(str(cg / "cgroup.procs"), "")
    _write(str(cg / "memory.stat"), "anon 700000000\nanon_thp 610271232\nfile_thp 0\n")
    b = FirecrackerBackend(Runner(dry_run=False), str(tmp_path / "logs"), cgroup_root=str(tmp_path / "cgroup"),
                           proc_root=str(tmp_path / "proc"))
    assert b.sample(_microvm(b))["anon_thp_bytes"] == 610271232
