"""The two hypervisor backends offline: rendered command lines and configs, the spec's hypervisor
options, the CPU split on fake /proc trees, Cloud Hypervisor's reset events and verify-clean."""
from __future__ import annotations

import json
import os
import shlex

import pytest

from hostd.__main__ import main
from hostd.backends import (BACKENDS, HYPERVISORS, BackendError, CloudHypervisorBackend, FirecrackerBackend,
                            make_hypervisor_backend)
from hostd.backends.cloud_hypervisor import read_events
from hostd.model import MicroVM, hypervisor_problems
from hostd.runner import ProcessHandle, Result, Runner

TCK = 100
IP3 = "ip=10.200.0.13::10.200.0.1:255.255.255.0:vm3:eth0:off:10.42.0.2"


def _microvm(backend, hypervisor=None, slot=3, fault=None, vcpus=2, mem_mib=2048):
    return MicroVM(id="s%03d-abcdef01" % slot, slot=slot, backend=backend.name, address=backend.address(slot),
                   vcpus=vcpus, mem_mib=mem_mib, fault=fault, ready_timeout_s=60, max_lifetime_s=600,
                   idle_timeout_s=120, fixture_base_url=backend.fixture_base_url,
                   hypervisor=dict(hypervisor or {}, name=backend.name))


def _fc(**kw):
    return FirecrackerBackend(Runner(dry_run=True), "/var/log/fleetkit", firecracker_bin="/usr/local/bin/firecracker",
                              kernel="/var/lib/fleetkit/vmlinux", rootfs="/var/lib/fleetkit/guest.ext4", **kw)


def _ch(arch="x86_64", **kw):
    return CloudHypervisorBackend(Runner(dry_run=True), "/var/log/fleetkit", kernel="/var/lib/fleetkit/vmlinux",
                                  rootfs="/var/lib/fleetkit/guest.ext4", arch=arch, **kw)


# --- Firecracker: both transports, with and without the entropy device --------------------

def test_firecracker_default_is_cap_baseline_1():
    b = _fc()
    default, explicit = _microvm(b), _microvm(b, {"virtio_transport": "mmio", "virtio_rng": False})
    for s in (default, explicit):
        cfg = b.vm_config(s)
        assert set(cfg) == {"boot-source", "drives", "machine-config", "network-interfaces", "logger"}
        assert cfg["boot-source"]["boot_args"] == "console=ttyS0 reboot=k panic=1 pci=off " + IP3
        assert b.hypervisor_argv(s) == ["/usr/local/bin/firecracker", "--api-sock", "/run/fleetkit/s003-abcdef01/fc.sock",
                                        "--config-file", "/run/fleetkit/s003-abcdef01/vm.json"]
    assert b.options(default) == {"virtio_transport": "mmio", "virtio_rng": False}
    # a microVM made before the spec existed carries no hypervisor section at all
    bare = _microvm(b)
    bare.hypervisor = {}
    assert b.vm_config(bare) == b.vm_config(default)


def test_firecracker_on_pci_with_an_entropy_device():
    b = _fc()
    s = _microvm(b, {"virtio_transport": "pci", "virtio_rng": True})
    cfg = b.vm_config(s)
    assert cfg["entropy"] == {}
    assert cfg["boot-source"]["boot_args"] == "console=ttyS0 reboot=k panic=1 " + IP3      # no pci=off
    assert b.hypervisor_argv(s)[-1] == "--enable-pci"
    b.create(s)
    text = b.runner.render_text()
    written = [e for e in b.runner.rendered if e["kind"] == "write" and e["text"].endswith("/vm.json")]
    assert json.loads(written[0]["content"]) == cfg
    assert "--config-file /run/fleetkit/s003-abcdef01/vm.json --enable-pci > " in text
    sidecar = [e for e in b.runner.rendered if e["kind"] == "write" and e["text"].endswith("/microvm.json")]
    assert json.loads(sidecar[0]["content"])["options"] == {"virtio_transport": "pci", "virtio_rng": True}


def test_firecracker_mmio_with_entropy_and_pci_without():
    b = _fc()
    mmio_rng = _microvm(b, {"virtio_rng": True})
    assert "pci=off" in b.boot_args(mmio_rng) and b.vm_config(mmio_rng)["entropy"] == {}
    assert "--enable-pci" not in b.hypervisor_argv(mmio_rng)
    pci = _microvm(b, {"virtio_transport": "pci"})
    assert "entropy" not in b.vm_config(pci) and "pci=off" not in b.boot_args(pci)


@pytest.mark.parametrize("hypervisor, message", [
    ({"virtio_transport": "virtio-ccw"}, "virtio_transport"),
    ({"virtio_rng": 1}, "virtio_rng"),              # JSON types count: 1 is not true
    ({"virtio_balloon": True}, "no option virtio_balloon"),
])
def test_firecracker_refuses_what_it_cannot_carry_out(hypervisor, message):
    b = _fc()
    s = _microvm(b, hypervisor)
    with pytest.raises(BackendError, match=message):
        b.create(s)
    assert not [e for e in b.runner.rendered if e["kind"] in ("run", "popen")]    # nothing touched the host
    s.hypervisor["name"] = "cloud-hypervisor"
    with pytest.raises(BackendError, match="not cloud-hypervisor"):
        b.options(s)


# --- Cloud Hypervisor ----------------------------------------------------------------------

def test_cloud_hypervisor_command_line():
    b = _ch()
    s = _microvm(b)
    assert b.options(s) == {"virtio_transport": "pci", "virtio_rng": True}
    argv = b.hypervisor_argv(s)
    assert argv[0] == "/usr/local/bin/cloud-hypervisor"
    flags = dict(zip(argv[1::2], argv[2::2]))
    assert len(argv) == 1 + 2 * len(flags)
    assert flags == {
        "--api-socket": "path=/run/fleetkit/s003-abcdef01/ch.sock",
        "--kernel": "/var/lib/fleetkit/vmlinux",
        "--cmdline": "console=ttyS0 reboot=k panic=1 root=/dev/vda ro " + IP3,
        "--cpus": "boot=2,max=2,topology=1:2:1:1,nested=off,core_scheduling=off,kvm_hyperv=off",
        "--memory": "size=2048M,thp=off,hugepages=off,shared=off,prefault=off,mergeable=off",
        "--disk": "path=/var/lib/fleetkit/guest.ext4,readonly=on,direct=off,image_type=raw,sparse=off,num_queues=1,"
                  "queue_size=256,id=rootfs,_disable_io_uring=on,_disable_aio=on",
        "--net": "tap=ch-3,mac=06:00:0A:C8:00:0D,num_queues=2,queue_size=256,offload_tso=on,offload_ufo=on,"
                 "offload_csum=on,id=eth0",
        "--rng": "src=/dev/urandom",
        "--serial": "tty",
        "--console": "off",
        "--seccomp": "true",
        "--event-monitor": "path=/run/fleetkit/s003-abcdef01/events.json",
        "--log-file": "/var/log/fleetkit/microvms/s003-abcdef01/cloud-hypervisor.log",
    }
    # 4 vCPUs, 4 GiB and a fault: the shape follows the microVM, the fault the command line
    big = _microvm(b, vcpus=4, mem_mib=4096, fault="crash_on_start")
    flags = dict(zip(b.hypervisor_argv(big)[1::2], b.hypervisor_argv(big)[2::2]))
    assert flags["--cpus"].startswith("boot=4,max=4,topology=1:4:1:1,")
    assert flags["--memory"].startswith("size=4096M,")
    assert flags["--cmdline"].endswith(" fleetkit.fault=crash_on_start")
    # the same guest: kernel, rootfs, ip= and MAC match Firecracker's for the slot
    fc = _fc()
    assert fc.vm_config(_microvm(fc))["network-interfaces"][0]["guest_mac"] == "06:00:0A:C8:00:0D"
    assert IP3 in fc.boot_args(_microvm(fc))


def test_cloud_hypervisor_console_follows_the_architecture():
    for arch, console, serial, virtio_console in (("x86_64", "ttyS0", "tty", "off"), ("aarch64", "hvc0", "off", "tty")):
        b = _ch(arch)
        s = _microvm(b)
        assert b.boot_args(s).startswith("console=%s reboot=k" % console)
        flags = dict(zip(b.hypervisor_argv(s)[1::2], b.hypervisor_argv(s)[2::2]))
        assert (flags["--serial"], flags["--console"]) == (serial, virtio_console)


def test_cloud_hypervisor_create_and_destroy_render():
    b = _ch()
    s = _microvm(b)
    b.create(s)
    b.destroy(s)
    text = b.runner.render_text()
    assert "ip tuntap add dev ch-3 mode tap" in text
    assert "ip link set ch-3 master fcbr0" in text and "ip link set ch-3 up" in text
    scope = shlex.join(b.scope_argv(s))
    assert scope.startswith("systemd-run --scope --quiet --unit ch-vm3 --description 'fleetkit microVM s003-abcdef01' "
                            "-p MemoryMax=2304M -p MemorySwapMax=0 -p CPUQuota=200% /usr/local/bin/cloud-hypervisor ")
    assert "> /var/log/fleetkit/microvms/s003-abcdef01/console.log 2>&1" in text
    assert not [e for e in b.runner.rendered if e["kind"] == "write" and e["text"].endswith("/vm.json")]
    assert "systemctl kill --signal=SIGKILL ch-vm3.scope" in text
    assert "ip link del ch-3" in text and "systemctl reset-failed ch-vm3.scope" in text
    assert "rm -rf /run/fleetkit/s003-abcdef01" in text
    assert s.handle["unit"] == "ch-vm3.scope" and s.console_log.endswith("s003-abcdef01/console.log")


@pytest.mark.parametrize("hypervisor", [{"virtio_transport": "mmio"}, {"virtio_rng": False}])
def test_cloud_hypervisor_has_only_pci_and_always_an_entropy_device(hypervisor):
    b = _ch()
    with pytest.raises(BackendError):
        b.create(_microvm(b, hypervisor))
    # the manager refuses the same before create(), from hypervisor_options
    assert hypervisor_problems(b, dict(hypervisor, name="cloud-hypervisor"))
    assert hypervisor_problems(b, {"name": "cloud-hypervisor", "virtio_transport": "pci", "virtio_rng": True}) == []


def test_backends_by_spec_name():
    assert HYPERVISORS == {"firecracker": FirecrackerBackend, "cloud-hypervisor": CloudHypervisorBackend}
    assert BACKENDS["cloud-hypervisor"] is CloudHypervisorBackend
    ch = make_hypervisor_backend("cloud-hypervisor", Runner(dry_run=True), "/tmp/logs", binary="/opt/ch")
    assert isinstance(ch, CloudHypervisorBackend) and ch.binary == "/opt/ch"
    fc = make_hypervisor_backend("firecracker", Runner(dry_run=True), "/tmp/logs")
    assert fc.binary == "/usr/local/bin/firecracker" and fc.firecracker == fc.binary
    with pytest.raises(BackendError, match="qemu"):
        make_hypervisor_backend("qemu", Runner(dry_run=True), "/tmp/logs")


def test_render_cli_cloud_hypervisor(capsys):
    rc = main(["--backend", "cloud-hypervisor", "--render", "--slot", "2", "--fault", "hang_task"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "systemd-run --scope --quiet --unit ch-vm2" in out
    assert "/usr/local/bin/cloud-hypervisor --api-socket" in out
    assert "fleetkit.fault=hang_task" in out and "tap=ch-2,mac=06:00:0A:C8:00:0C" in out


# --- reset and shutdown events --------------------------------------------------------------

EVENTS = """{
  "timestamp": {"secs": 0, "nanos": 1000},
  "source": "vmm",
  "event": "starting",
  "properties": null
}

{
  "timestamp": {"secs": 0, "nanos": 90000},
  "source": "vm",
  "event": "booted",
  "properties": null
}

"""
REBOOTING = """{
  "timestamp": {"secs": 3, "nanos": 5},
  "source": "vm",
  "event": "rebooting",
  "properties": null
}

{
  "timestamp": {"secs": 3, "nan"""


def test_read_events_keeps_whole_objects_only():
    assert [e["event"] for e in read_events(EVENTS)] == ["starting", "booted"]
    assert [e["event"] for e in read_events(EVENTS + REBOOTING)] == ["starting", "booted", "rebooting"]
    assert read_events("") == [] and read_events("{\"sou") == [] and read_events("garbage") == []
    # one object per line works too
    assert [e["event"] for e in read_events('{"source":"vm","event":"booting"}\n{"source":"vm","event":"booted"}\n')] \
        == ["booting", "booted"]


def test_a_guest_reset_counts_as_an_exit(tmp_path):
    b = CloudHypervisorBackend(Runner(dry_run=False), str(tmp_path / "logs"), run_root=str(tmp_path / "run"))
    s = _microvm(b)
    s.handle["process"] = ProcessHandle(None, [])        # a process that never exits
    events = b.events_path(s)
    os.makedirs(os.path.dirname(events))
    assert b.alive(s)                                   # no events file yet
    with open(events, "w") as f:
        f.write(EVENTS)
    assert b.alive(s)
    with open(events, "a") as f:
        f.write(REBOOTING)
    os.makedirs(os.path.dirname(b.console_log_path(s)))
    with open(b.console_log_path(s), "w") as f:
        f.write("fleetkit-init: starting guestd\nKernel panic - not syncing: Attempted to kill init!\n")
    s.console_log = b.console_log_path(s)
    assert not b.alive(s)
    info = b.exit_info(s)
    assert info.startswith("cloud-hypervisor event 'vm rebooting': the guest reset or shut down")
    assert "Attempted to kill init" in info


# --- the CPU split on fake /proc trees ------------------------------------------------------

def _stat_line(tid, comm, utime, stime):
    return "%d (%s) S 1 %d %d 0 -1 4194560 10 0 0 0 %d %d 0 0 20 0 4 0 12345 1000000 200 18446744073709551615\n" % (
        tid, comm, tid, tid, utime, stime)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _process(proc_root, pid, threads, rss_kb):
    for i, (comm, utime, stime) in enumerate(threads):
        task = os.path.join(proc_root, str(pid), "task", str(pid + i))
        _write(os.path.join(task, "comm"), comm + "\n")
        _write(os.path.join(task, "stat"), _stat_line(pid + i, comm, utime, stime))
    _write(os.path.join(proc_root, str(pid), "status"), "Name:\t%s\nVmRSS:\t %d kB\n" % (threads[0][0], rss_kb))


def _scope(cgroup_root, unit, pids, usage_usec):
    cg = os.path.join(cgroup_root, "system.slice", unit)
    _write(os.path.join(cg, "cgroup.procs"), "".join("%s\n" % p for p in pids))
    _write(os.path.join(cg, "memory.current"), "2254857830\n")
    _write(os.path.join(cg, "memory.peak"), "2300000000\n")
    _write(os.path.join(cg, "cpu.stat"), "usage_usec %d\nuser_usec 1\nsystem_usec 1\nnr_periods 120\n"
                                         "nr_throttled 9\nthrottled_usec 450000\n" % usage_usec)
    _write(os.path.join(cg, "cpu.pressure"), "some avg10=0.00 avg60=0.00 avg300=0.00 total=812345\n"
                                             "full avg10=0.00 avg60=0.00 avg300=0.00 total=601234\n")
    _write(os.path.join(cg, "memory.pressure"), "some avg10=0.00 avg60=0.00 avg300=0.00 total=4321\n")


# Cloud Hypervisor's threads (comm as the kernel shows it); vCPU ticks 500 + 350 = 850.
CH_THREADS = [("cloud-hyperviso", 20, 10), ("vmm", 5, 5), ("http-server", 0, 1), ("event-monitor", 0, 1),
              ("vmm_signal_hand", 0, 0), ("vcpu0", 400, 100), ("vcpu1", 300, 50), ("rootfs_q0", 3, 7),
              ("eth0_qp0", 20, 40), ("_rng0_q0", 0, 1)]


def test_cloud_hypervisor_split_counts_exited_threads_as_the_hypervisor(tmp_path, monkeypatch):
    monkeypatch.setattr("hostd.procfs.clk_tck", lambda: TCK)
    proc, cgroot = str(tmp_path / "proc"), str(tmp_path / "cgroup")
    _process(proc, 7000, CH_THREADS, rss_kb=2_150_000)
    living_other = (30 + 10 + 1 + 1 + 0 + 10 + 60 + 1) * 10_000          # 1.14 s in threads still listed
    exited = 400_000                                                       # the kernel loader, gone from task/
    usage = 850 * 10_000 + living_other + exited
    _scope(cgroot, "ch-vm3.scope", [7000], usage)
    b = CloudHypervisorBackend(Runner(dry_run=False), str(tmp_path / "logs"), cgroup_root=cgroot, proc_root=proc)
    out = b.sample(_microvm(b))
    assert out["cpu_vcpu_usec"] == 850 * 10_000
    assert out["cpu_hypervisor_usec"] == living_other + exited == usage - 850 * 10_000
    assert out["cpu_usage_usec"] == usage and out["rss_bytes"] == 2_150_000 * 1024
    assert out["cgroup_memory_peak"] == 2300000000 and out["cpu_nr_throttled"] == 9


def test_firecracker_split_is_cgroup_minus_vcpu(tmp_path, monkeypatch):
    monkeypatch.setattr("hostd.procfs.clk_tck", lambda: TCK)
    proc, cgroot = str(tmp_path / "proc"), str(tmp_path / "cgroup")
    _process(proc, 4242, [("firecracker", 30, 20), ("fc_api", 1, 1), ("fc_vcpu 0", 400, 100), ("fc_vcpu 1", 300, 50)],
             rss_kb=2_100_000)
    _scope(cgroot, "fc-vm3.scope", [4242], 850 * 10_000 + 52 * 10_000 + 3_000)   # 3 ms below a tick
    b = FirecrackerBackend(Runner(dry_run=False), str(tmp_path / "logs"), cgroup_root=cgroot, proc_root=proc)
    out = b.sample(_microvm(b))
    assert out["cpu_vcpu_usec"] == 850 * 10_000
    assert out["cpu_hypervisor_usec"] == 52 * 10_000 + 3_000
    # vCPU ticks read after cpu.stat can run ahead of it: never negative
    _scope(cgroot, "fc-vm3.scope", [4242], 840 * 10_000)
    b2 = FirecrackerBackend(Runner(dry_run=False), str(tmp_path / "logs"), cgroup_root=cgroot, proc_root=proc)
    assert b2.sample(_microvm(b2))["cpu_hypervisor_usec"] == 0


def test_vcpu_prefixes_do_not_cross():
    # Cloud Hypervisor's "vcpu" prefix never matches a Firecracker thread and the other way round
    assert not "fc_vcpu 0".startswith(CloudHypervisorBackend.vcpu_thread_prefix)
    assert not "vcpu0".startswith(FirecrackerBackend.vcpu_thread_prefix)
    assert [c for c, _, _ in CH_THREADS if c.startswith(CloudHypervisorBackend.vcpu_thread_prefix)] == ["vcpu0", "vcpu1"]


# --- verify-clean sees both hypervisors' leftovers ------------------------------------------

class _CannedRunner(Runner):
    def __init__(self, answers):
        super().__init__(dry_run=False)
        self.answers = answers
        self.calls = []

    def run(self, argv, check=True, timeout=60, input_text=None):
        self.calls.append(list(argv))
        out = self.answers.get(" ".join(argv), "")
        return Result(list(argv), 0 if out else 1, out, "")


@pytest.mark.parametrize("cls", [FirecrackerBackend, CloudHypervisorBackend])
def test_verify_clean_covers_both_hypervisors(cls, tmp_path):
    runner = _CannedRunner({
        "pgrep -x firecracker": "101\n",
        "pgrep -x cloud-hyperviso": "202\n203\n",
        "ip -o link show": "1: lo: <LOOPBACK> mtu 65536\n7: fc-4: <BROADCAST> mtu 1500\n9: ch-0@if2: <BROADCAST>\n"
                           "10: fcbr0: <BROADCAST> mtu 1500\n",
        "systemctl list-units --all --plain --no-legend fc-vm*.scope": "fc-vm4.scope loaded active running x\n",
        "systemctl list-units --all --plain --no-legend ch-vm*.scope": "ch-vm0.scope loaded failed failed x\n",
    })
    run_root = tmp_path / "run"
    (run_root / "s000-deadbeef").mkdir(parents=True)
    b = cls(runner, str(tmp_path / "logs"), run_root=str(run_root))
    assert b.verify_clean() == [
        "process firecracker pid 101", "process cloud-hyperviso pid 202", "process cloud-hyperviso pid 203",
        "tap fc-4", "tap ch-0", "scope fc-vm4.scope", "scope ch-vm0.scope",
        "run dir %s" % (run_root / "s000-deadbeef"),
    ]
    clean = cls(_CannedRunner({}), str(tmp_path / "logs"), run_root=str(tmp_path / "none"))
    assert clean.verify_clean() == []
