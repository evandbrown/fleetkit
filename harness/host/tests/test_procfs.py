"""/proc and cgroup readers, and the Firecracker backend's per-microVM sample, on fake trees."""
from __future__ import annotations

import os

from hostd.backends.base import MICROVM_FIGURES
from hostd.backends.docker import DockerBackend
from hostd.backends.firecracker import FirecrackerBackend
from hostd.model import MicroVM
from hostd.procfs import (parse_flat_keyed, parse_pressure, stat_comm, stat_cpu_ticks, status_rss_bytes,
                          thread_cpu_split)
from hostd.runner import Runner

TCK = 100


def stat_line(tid, comm, utime, stime):
    # fields: 1 pid, 2 (comm), 3 state, 4..13, 14 utime, 15 stime, then the rest
    return "%d (%s) S 1 %d %d 0 -1 4194560 10 0 0 0 %d %d 0 0 20 0 4 0 12345 1000000 200 18446744073709551615\n" % (
        tid, comm, tid, tid, utime, stime)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def fake_firecracker(proc_root, pid=4242, threads=None, rss_kb=2_100_000):
    """A Firecracker process: main thread, API thread, two vCPU threads (ticks as given)."""
    threads = threads or [(pid, "firecracker", 30, 20), (pid + 1, "fc_api", 1, 1),
                          (pid + 2, "fc_vcpu 0", 400, 100), (pid + 3, "fc_vcpu 1", 300, 50)]
    for tid, comm, utime, stime in threads:
        task = os.path.join(proc_root, str(pid), "task", str(tid))
        write(os.path.join(task, "comm"), comm + "\n")
        write(os.path.join(task, "stat"), stat_line(tid, comm, utime, stime))
    write(os.path.join(proc_root, str(pid), "status"), "Name:\tfirecracker\nVmPeak:\t 1 kB\nVmRSS:\t %d kB\n" % rss_kb)
    return pid


def test_stat_parsing_survives_spaces_and_parens_in_comm():
    assert stat_cpu_ticks(stat_line(7, "fc_vcpu 0", 12, 3)) == 15
    assert stat_comm(stat_line(7, "fc_vcpu 0", 12, 3)) == "fc_vcpu 0"
    assert stat_cpu_ticks(stat_line(8, "weird) (name", 5, 6)) == 11
    assert stat_comm(stat_line(8, "weird) (name", 5, 6)) == "weird) (name"
    assert stat_cpu_ticks("") is None and stat_cpu_ticks("8 (short) S 1") is None
    assert stat_cpu_ticks(None) is None


def test_thread_cpu_split_by_comm(tmp_path):
    proc = str(tmp_path / "proc")
    pid = fake_firecracker(proc)
    split = thread_cpu_split([str(pid)], proc, tck=TCK)
    # vCPU: (400+100) + (300+50) = 850 ticks; hypervisor: (30+20) + (1+1) = 52 ticks; 1 tick = 10 ms
    assert split == {"vcpu_usec": 850 * 10_000, "other_usec": 52 * 10_000}


def test_thread_cpu_split_falls_back_to_stat_comm_and_tolerates_gaps(tmp_path):
    proc = str(tmp_path / "proc")
    pid = fake_firecracker(proc)
    os.remove(os.path.join(proc, str(pid), "task", str(pid + 2), "comm"))    # name from stat instead
    write(os.path.join(proc, str(pid), "task", "9999", "stat"), "garbage")   # unparsable: skipped
    split = thread_cpu_split([str(pid), "31337"], proc, tck=TCK)              # 31337 has vanished
    assert split == {"vcpu_usec": 850 * 10_000, "other_usec": 52 * 10_000}
    assert thread_cpu_split(["31337"], proc, tck=TCK) == {"vcpu_usec": None, "other_usec": None}
    assert thread_cpu_split([], proc, tck=TCK) == {"vcpu_usec": None, "other_usec": None}


def test_pressure_and_cpu_stat_parsers():
    psi = parse_pressure("some avg10=1.50 avg60=0.40 avg300=0.10 total=123456\n"
                         "full avg10=0.25 avg60=0.00 avg300=0.00 total=7890\n")
    assert psi == {"some_avg10": 1.5, "some_total": 123456.0, "full_avg10": 0.25, "full_total": 7890.0}
    # memory.pressure on older kernels, or a file that is missing
    assert parse_pressure("some avg10=0.00 avg60=0.00 avg300=0.00 total=5\n")["full_total"] is None
    assert parse_pressure(None) == {"some_avg10": None, "some_total": None, "full_avg10": None, "full_total": None}
    assert parse_pressure("some avg10=x total=12\n") == {"some_avg10": None, "some_total": 12.0,
                                                        "full_avg10": None, "full_total": None}
    stat = parse_flat_keyed("usage_usec 1000\nuser_usec 600\nsystem_usec 400\nnr_periods 50\n"
                            "nr_throttled 7\nthrottled_usec 91000\nbogus line here\n")
    assert stat["usage_usec"] == 1000 and stat["nr_throttled"] == 7 and stat["throttled_usec"] == 91000
    assert "bogus" not in stat


def test_status_rss(tmp_path):
    proc = str(tmp_path / "proc")
    write(os.path.join(proc, "self", "status"), "Name:\tpython\nVmRSS:\t   51200 kB\n")
    write(os.path.join(proc, "2", "status"), "Name:\tkthreadd\n")                # kernel thread: no VmRSS
    assert status_rss_bytes(proc, "self") == 51200 * 1024
    assert status_rss_bytes(proc, "2") is None and status_rss_bytes(proc, "404") is None


def _microvm(backend, slot=0):
    return MicroVM(id="s%03d-abcdef01" % slot, slot=slot, backend=backend.name, address=backend.address(slot),
                   vcpus=2, mem_mib=2048, fault=None, ready_timeout_s=60, max_lifetime_s=600,
                   idle_timeout_s=120, fixture_base_url=backend.fixture_base_url)


def _scope(cgroup_root, slot, pids, cpu_pressure=True, memory_pressure=True):
    cg = os.path.join(cgroup_root, "system.slice", "fc-vm%d.scope" % slot)
    write(os.path.join(cg, "cgroup.procs"), "".join("%s\n" % p for p in pids))
    write(os.path.join(cg, "memory.current"), "2254857830\n")
    write(os.path.join(cg, "memory.peak"), "2300000000\n")
    write(os.path.join(cg, "cpu.stat"), "usage_usec 9020000\nuser_usec 8000000\nsystem_usec 1020000\n"
                                        "nr_periods 120\nnr_throttled 9\nthrottled_usec 450000\n"
                                        "nr_bursts 0\nburst_usec 0\n")
    if cpu_pressure:
        write(os.path.join(cg, "cpu.pressure"), "some avg10=3.10 avg60=1.00 avg300=0.20 total=812345\n"
                                                "full avg10=2.00 avg60=0.50 avg300=0.10 total=601234\n")
    if memory_pressure:
        write(os.path.join(cg, "memory.pressure"), "some avg10=0.00 avg60=0.00 avg300=0.00 total=4321\n"
                                                   "full avg10=0.00 avg60=0.00 avg300=0.00 total=1234\n")
    return cg


def test_firecracker_sample_reads_the_scope_and_threads(tmp_path, monkeypatch):
    monkeypatch.setattr("hostd.procfs.clk_tck", lambda: TCK)
    proc, cgroot = str(tmp_path / "proc"), str(tmp_path / "cgroup")
    pid = fake_firecracker(proc)
    _scope(cgroot, 0, [pid])
    b = FirecrackerBackend(Runner(dry_run=False), str(tmp_path / "logs"), cgroup_root=cgroot, proc_root=proc)
    out = b.sample(_microvm(b, 0))
    assert set(out) == set(MICROVM_FIGURES)
    assert out == {
        "rss_bytes": 2_100_000 * 1024,
        "cgroup_memory_current": 2254857830,
        "cgroup_memory_peak": 2300000000,
        "cpu_usage_usec": 9020000,
        "cpu_vcpu_usec": 850 * 10_000,
        "cpu_hypervisor_usec": 52 * 10_000,
        "cpu_throttled_usec": 450000,
        "cpu_nr_throttled": 9,
        "cpu_pressure_some_total_us": 812345,
        "cpu_pressure_full_total_us": 601234,
        "memory_pressure_some_total_us": 4321,
    }
    assert all(isinstance(v, int) for v in out.values())


def test_firecracker_sample_without_pressure_files_or_threads(tmp_path):
    proc, cgroot = str(tmp_path / "proc"), str(tmp_path / "cgroup")
    _scope(cgroot, 1, ["5555"], cpu_pressure=False, memory_pressure=False)     # pid 5555 has no /proc entry
    b = FirecrackerBackend(Runner(dry_run=False), str(tmp_path / "logs"), cgroup_root=cgroot, proc_root=proc)
    out = b.sample(_microvm(b, 1))
    assert out["cgroup_memory_current"] == 2254857830 and out["cpu_throttled_usec"] == 450000
    for k in ("rss_bytes", "cpu_vcpu_usec", "cpu_hypervisor_usec", "cpu_pressure_some_total_us",
              "cpu_pressure_full_total_us", "memory_pressure_some_total_us"):
        assert out[k] is None, k


def test_every_backend_sample_has_every_key(tmp_path):
    fc = FirecrackerBackend(Runner(dry_run=True), str(tmp_path))
    assert fc.sample(_microvm(fc)) == {k: None for k in MICROVM_FIGURES}
    dk = DockerBackend(Runner(dry_run=True), str(tmp_path))
    s = _microvm(dk)
    s.handle["container"] = "fleetkit-microvm-x"
    assert dk.sample(s) == {k: None for k in MICROVM_FIGURES}
