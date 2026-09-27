"""The /proc sampler against fake /proc trees (no Linux needed)."""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path

import pytest

from guestd.log import LogRing
from guestd.procstat import GROUPS, ProcSampler, TaskSampling, classify, parse_cpu_line, parse_pid_stat, parse_psi_some_total

CHROMIUM = "/usr/lib/chromium/chromium"
CLK_TCK = 100
PAGE = 4096


def argv(*args: str) -> bytes:
    """A NUL-separated command line, as the kernel stores an unmodified argv."""
    return b"\0".join(a.encode() for a in args) + b"\0"


def retitled(text: str) -> bytes:
    """The single space-joined string a Chromium child leaves after retitling itself."""
    return text.encode() + b"\0"


class FakeProc:
    """Writes the handful of /proc files the sampler reads."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(exist_ok=True)
        self.system(busy=1000, idle=5000, uptime_s=100.0, mem_available_kb=1_500_000, psi_total=777)

    def system(self, busy: int, idle: int, uptime_s: float, mem_available_kb: int, psi_total=None) -> None:
        # busy is split over user/system/irq/softirq/steal, idle over idle/iowait; guest is
        # already inside user and must not be counted twice.
        user, system, irq, softirq, steal = busy - 40, 10, 10, 10, 10
        (self.root / "stat").write_text(
            f"cpu  {user} 0 {system} {idle - 100} 100 {irq} {softirq} {steal} 999 0\n"
            f"cpu0 1 2 3 4 5 6 7 8 0 0\nintr 12345\nctxt 67890\n"
        )
        (self.root / "uptime").write_text(f"{uptime_s:.2f} 0.00\n")
        (self.root / "meminfo").write_text(f"MemTotal:        2014592 kB\nMemFree:          100000 kB\nMemAvailable:    {mem_available_kb} kB\n")
        pressure = self.root / "pressure"
        if psi_total is None:
            if (pressure / "cpu").exists():
                (pressure / "cpu").unlink()
        else:
            pressure.mkdir(exist_ok=True)
            (pressure / "cpu").write_text(f"some avg10=1.00 avg60=0.50 avg300=0.10 total={psi_total}\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")

    def proc(self, pid: int, cmdline: bytes, ticks: int, start: int = 100, rss_pages: int = 10, comm: str = "x") -> None:
        d = self.root / str(pid)
        d.mkdir(exist_ok=True)
        utime, stime = ticks - ticks // 3, ticks // 3
        (d / "stat").write_text(
            f"{pid} ({comm}) S 1 1 1 0 -1 4194560 100 0 0 0 {utime} {stime} 5 5 20 0 1 0 {start} 1000000 {rss_pages} 18446744073709551615 0 0\n"
        )
        (d / "cmdline").write_bytes(cmdline)

    def remove(self, pid: int) -> None:
        d = self.root / str(pid)
        for f in d.iterdir():
            f.unlink()
        d.rmdir()


def _sampler(root: Path) -> ProcSampler:
    return ProcSampler(str(root), chromium_binary=CHROMIUM, self_pid=100, clk_tck=CLK_TCK, page_size=PAGE)


def test_classify_every_group():
    cases = {
        argv(CHROMIUM, "--headless=new", "--no-sandbox", "--remote-debugging-port=9222", "about:blank"): "browser",
        argv(CHROMIUM, "--type=renderer", "--lang=en-US"): "renderer",
        retitled(f"{CHROMIUM} --type=renderer --crashpad-handler-pid=12 --lang=en-US"): "renderer",
        argv(CHROMIUM, "--type=gpu-process", "--headless=new"): "gpu",
        retitled(f"{CHROMIUM} --type=utility --utility-sub-type=network.mojom.NetworkService --lang=en-US"): "network",
        argv(CHROMIUM, "--type=utility", "--utility-sub-type=storage.mojom.StorageService"): "utility",
        argv(CHROMIUM, "--type=utility"): "utility",
        argv(CHROMIUM, "--type=zygote", "--no-zygote-sandbox"): "zygote",
        argv(CHROMIUM, "--type=crashpad-handler", "--monitor-self"): "chromium_other",
        # the same binary name elsewhere (a relocated install) is still Chromium
        argv("/opt/chromium/chromium", "--type=renderer"): "renderer",
        # a different binary in Chromium's directory is not
        argv("/usr/lib/chromium/chrome_crashpad_handler", "--monitor-self"): "other",
        argv("python3", "-m", "guestd"): "other",
        argv("/sbin/init"): "other",
        b"": "other",  # kernel threads and zombies
    }
    for cmdline, group in cases.items():
        assert classify(cmdline, CHROMIUM) == group, cmdline
    # a flag value that merely contains "type=" is not a process type
    assert classify(argv(CHROMIUM, "--utility-sub-type=x", "--content-type=y"), CHROMIUM) == "browser"


def test_parsers():
    # the command name may contain spaces and parentheses; fields count from the last ")"
    stat = b"42 (Chrome_Child) (x y) S 1 1 1 0 -1 4194560 100 0 0 0 70 30 5 5 20 0 12 0 9876 1000000 321 18446744073709551615\n"
    assert parse_pid_stat(stat) == (100, 9876, 321)
    assert parse_pid_stat(b"garbage") is None and parse_pid_stat(b"1 (x) S 1 2") is None
    busy, idle = parse_cpu_line(b"cpu  100 5 20 1000 50 3 2 7 40 0\ncpu0 1 1 1 1 1 1 1 1 0 0\n")
    assert (busy, idle) == (100 + 5 + 20 + 3 + 2 + 7, 1000 + 50)
    assert parse_cpu_line(b"cpu  1 2 3 4\n") == (6, 4)  # old kernels: fewer columns
    assert parse_cpu_line(b"intr 1\n") == (None, None)
    assert parse_psi_some_total(b"some avg10=0.00 avg60=0.00 avg300=0.00 total=123456\nfull avg10=0.00 total=9\n") == 123456
    assert parse_psi_some_total(b"full avg10=0.00 total=9\n") is None


def _tree(tmp_path: Path) -> FakeProc:
    fp = FakeProc(tmp_path / "proc")
    fp.proc(1, argv("/sbin/init"), ticks=50)
    fp.proc(2, b"", ticks=30, comm="kthreadd", rss_pages=0)
    fp.proc(100, argv("python3", "-m", "guestd"), ticks=90, rss_pages=5000)
    fp.proc(200, argv(CHROMIUM, "--headless=new", "about:blank"), ticks=300, rss_pages=30000, comm="chromium")
    fp.proc(201, argv(CHROMIUM, "--type=zygote"), ticks=10, rss_pages=2000)
    fp.proc(202, argv(CHROMIUM, "--type=gpu-process"), ticks=40, rss_pages=8000)
    fp.proc(203, retitled(f"{CHROMIUM} --type=utility --utility-sub-type=network.mojom.NetworkService"), ticks=60, rss_pages=6000)
    fp.proc(204, argv(CHROMIUM, "--type=utility", "--utility-sub-type=storage.mojom.StorageService"), ticks=20, rss_pages=4000)
    fp.proc(205, retitled(f"{CHROMIUM} --type=renderer --lang=en-US"), ticks=500, rss_pages=20000, comm="a) (b")
    fp.proc(206, argv(CHROMIUM, "--type=crashpad-handler"), ticks=5, rss_pages=1000)
    return fp


def test_snapshot_and_delta_accounting(tmp_path):
    fp = _tree(tmp_path)
    s = _sampler(fp.root)
    assert s.available
    base = s.snapshot(t_ns=1_000)

    # 200 ms later: CPU moved, one renderer appeared, the crashpad handler exited,
    # and pid 203 was reused by a new network service (a different start time).
    fp.system(busy=1050, idle=5150, uptime_s=100.2, mem_available_kb=1_400_000, psi_total=900)
    fp.proc(100, argv("python3", "-m", "guestd"), ticks=92, rss_pages=5000)
    fp.proc(200, argv(CHROMIUM, "--headless=new", "about:blank"), ticks=303, rss_pages=31000)
    fp.proc(2, b"", ticks=31, comm="kthreadd", rss_pages=0)
    fp.proc(205, retitled(f"{CHROMIUM} --type=renderer --lang=en-US"), ticks=520, rss_pages=21000, comm="a) (b")
    fp.proc(207, retitled(f"{CHROMIUM} --type=renderer --lang=en-US"), ticks=7, start=10_010, rss_pages=9000)
    fp.remove(206)
    fp.proc(203, retitled(f"{CHROMIUM} --type=utility --utility-sub-type=network.mojom.NetworkService"), ticks=3, start=10_015, rss_pages=6100)
    cur = s.snapshot(t_ns=200_001_000)
    sample = s.delta(base, cur)

    assert set(sample) == {"t_ns", "cpu_total_ms", "cpu_idle_ms", "mem_available", "psi_cpu_some_total_us", "groups"}
    assert sample["t_ns"] == 200_001_000
    assert sample["cpu_total_ms"] == 500.0 and sample["cpu_idle_ms"] == 1500.0  # 50 and 150 ticks at 100 Hz
    assert sample["mem_available"] == 1_400_000 * 1024 and sample["psi_cpu_some_total_us"] == 900
    g = sample["groups"]
    assert list(g) == list(GROUPS)
    assert g["browser"] == {"cpu_ms": 30.0, "rss_bytes": 31000 * PAGE, "procs": 1}
    # pid 205 used 20 ticks; pid 207 is new and counts from zero (7 ticks)
    assert g["renderer"] == {"cpu_ms": 270.0, "rss_bytes": (21000 + 9000) * PAGE, "procs": 2}
    assert g["guestd"] == {"cpu_ms": 20.0, "rss_bytes": 5000 * PAGE, "procs": 1}
    # a reused pid is a new process: its 3 ticks, not 3 - 60
    assert g["network"] == {"cpu_ms": 30.0, "rss_bytes": 6100 * PAGE, "procs": 1}
    assert g["utility"] == {"cpu_ms": 0.0, "rss_bytes": 4000 * PAGE, "procs": 1}
    assert g["zygote"]["procs"] == 1 and g["gpu"]["procs"] == 1
    # the exited crashpad handler loses its last interval
    assert g["chromium_other"] == {"cpu_ms": 0.0, "rss_bytes": 0, "procs": 0}
    assert g["other"] == {"cpu_ms": 10.0, "rss_bytes": 10 * PAGE, "procs": 2}  # init + kthreadd


def test_young_processes_are_reclassified_old_ones_cached(tmp_path):
    fp = _tree(tmp_path)
    s = _sampler(fp.root)
    prev = s.snapshot(0)
    # a child forked from the zygote, not yet retitled: it looks like a zygote
    fp.system(busy=1010, idle=5010, uptime_s=100.1, mem_available_kb=1_500_000)
    fp.proc(300, argv(CHROMIUM, "--type=zygote"), ticks=1, start=10_005)
    cur = s.snapshot(1)
    assert s.delta(prev, cur)["groups"]["zygote"]["procs"] == 2
    # it retitles itself as a renderer; it is young, so the new title counts
    fp.proc(300, retitled(f"{CHROMIUM} --type=renderer"), ticks=4, start=10_005)
    prev, cur = cur, s.snapshot(2)
    g = s.delta(prev, cur)["groups"]
    assert g["zygote"]["procs"] == 1 and g["renderer"]["procs"] == 2 and g["renderer"]["cpu_ms"] == 30.0
    # three seconds on it is old and its group is cached: a later change is not re-read
    fp.system(busy=1020, idle=5020, uptime_s=103.1, mem_available_kb=1_500_000)
    prev, cur = cur, s.snapshot(3)
    fp.proc(300, argv(CHROMIUM, "--type=gpu-process"), ticks=4, start=10_005)
    prev, cur = cur, s.snapshot(4)
    g = s.delta(prev, cur)["groups"]
    assert g["renderer"]["procs"] == 2 and g["gpu"]["procs"] == 1
    # the cache follows the process table: an exited pid is forgotten
    fp.remove(300)
    s.snapshot(5)
    assert all(pid != 300 for pid, _ in s._groups)


def test_missing_files_give_nulls(tmp_path):
    fp = _tree(tmp_path)
    fp.system(busy=1000, idle=5000, uptime_s=100.0, mem_available_kb=1_000_000, psi_total=None)
    (fp.root / "meminfo").unlink()
    s = _sampler(fp.root)
    a = s.snapshot(0)
    (fp.root / "stat").unlink()
    b = s.snapshot(1)
    sample = s.delta(a, b)
    assert sample["psi_cpu_some_total_us"] is None and sample["mem_available"] is None
    assert sample["cpu_total_ms"] is None and sample["cpu_idle_ms"] is None
    assert sample["groups"]["browser"]["procs"] == 1
    assert not ProcSampler(str(tmp_path / "nope")).available


def test_task_sampling_schedule_and_final_sample(tmp_path):
    fp = _tree(tmp_path)
    s = _sampler(fp.root)

    async def go():
        t0 = time.monotonic_ns()
        sampling = TaskSampling(s, 20, t0)
        sampling.start()
        await asyncio.sleep(0.11)
        samples = await sampling.stop()
        return t0, samples

    t0, samples = asyncio.run(go())
    # periodic ticks at 20, 40, 60, 80, 100 ms (some may be skipped on a busy machine) + the final one
    assert 3 <= len(samples) <= 7
    ts = [x["t_ns"] for x in samples]
    assert ts == sorted(ts) and ts[0] >= 20_000_000 and ts[-1] >= 110_000_000
    for x in samples:
        assert set(x["groups"]) == set(GROUPS) and x["cpu_total_ms"] == 0.0


def test_task_sampling_disabled_and_failing(tmp_path):
    fp = _tree(tmp_path)

    async def run(sampler, interval, log=None):
        sampling = TaskSampling(sampler, interval, time.monotonic_ns(), log)
        sampling.start()
        await asyncio.sleep(0.05)
        return await sampling.stop()

    assert asyncio.run(run(_sampler(fp.root), 0)) == []
    assert asyncio.run(run(None, 200)) == []
    assert asyncio.run(run(ProcSampler(str(tmp_path / "no-proc")), 10)) == []  # macOS

    class Broken(ProcSampler):
        calls = 0

        def snapshot(self, t_ns):
            Broken.calls += 1
            if Broken.calls > 2:
                raise RuntimeError("boom")
            return super().snapshot(t_ns)

    out = io.StringIO()
    log = LogRing(stream=out)
    samples = asyncio.run(run(Broken(str(fp.root), chromium_binary=CHROMIUM, self_pid=100), 10, log))
    assert len(samples) == 1  # the baseline, one sample, then the error stops sampling
    assert "proc sampling stopped" in out.getvalue()


def test_cancel_without_final_sample(tmp_path):
    fp = _tree(tmp_path)

    async def go():
        sampling = TaskSampling(_sampler(fp.root), 10, time.monotonic_ns())
        sampling.start()
        await asyncio.sleep(0.035)
        sampling.cancel()
        n = len(sampling.samples)
        await asyncio.sleep(0.05)
        return n, len(sampling.samples)

    n, later = asyncio.run(go())
    assert n == later and n >= 1
