"""Per-task CPU and memory attribution inside the guest, from /proc.

``POST /task`` samples the guest every ``sample_interval_ms`` (default 200; 0 disables)
from receipt until the end of the timed region, and once more at the end of it, so the
result can say which processes used the guest's CPUs while the task ran. Each sample:

    {"t_ns": <monotonic ns since task receipt, the same zero as the steps' dispatch_ns>,
     "cpu_total_ms": <busy (non-idle, non-iowait) ms over all guest CPUs since the previous sample>,
     "cpu_idle_ms": <idle + iowait ms since the previous sample>,
     "mem_available": <bytes>,
     "psi_cpu_some_total_us": <int, /proc/pressure/cpu "some total"; null without PSI>,
     "groups": {<group>: {"cpu_ms", "rss_bytes", "procs"}}}

The first sample's deltas are relative to a baseline read when sampling starts (at task
receipt). ``cpu_total_ms + cpu_idle_ms`` is the interval times the number of CPUs, which
lets a reader normalize without knowing the exact baseline time. Busy is user + nice +
system + irq + softirq + steal (guest and guest_nice are already inside user and nice).

Every group is always present (zeros when empty), by ``/proc/<pid>/cmdline``:

- ``browser``         the Chromium binary without ``--type=``
- ``renderer``        ``--type=renderer``
- ``gpu``             ``--type=gpu-process``
- ``network``         ``--type=utility`` with ``--utility-sub-type=network.mojom.NetworkService``
- ``utility``         any other ``--type=utility``
- ``zygote``          ``--type=zygote``
- ``chromium_other``  any other ``--type=`` of the Chromium binary (crashpad, ...)
- ``guestd``          this daemon, by pid
- ``other``           everything else: init, tini, kernel threads, zombies

``cpu_ms`` is the delta of utime + stime (fields 14 and 15 of ``/proc/<pid>/stat``, which
cover every thread of the process) converted with ``SC_CLK_TCK``, so its resolution is one
tick (10 ms at 100 Hz); ``rss_bytes`` is field 24 times the page size; ``procs`` counts the
processes seen in that pass. Two accounting rules:

- a process that appears between two samples counts from zero: its whole CPU time so far
  lands in the interval where it is first seen (so is a pid reused with a new start time);
- a process that exits between two samples loses its last interval: its CPU since the
  previous sample is not attributed to any group (it shows only in ``cpu_total_ms``).

Chromium forks its renderers from the zygote and its helpers from the browser, and a child
has its parent's command line until it retitles itself or execs. A classification is
therefore cached per (pid, start time) only once the process is ``STABLE_AGE_S`` old; a
younger process is classified again on every pass.

One pass reads ``/proc/stat``, ``/proc/uptime``, ``/proc/meminfo``, ``/proc/pressure/cpu``,
one ``listdir`` of ``/proc`` and each process's ``stat`` (plus ``cmdline`` for processes not
yet classified). It runs synchronously on the event loop; in a microVM with a few dozen
processes that is on the order of a millisecond. The cost lands in guestd's own CPU, which
the task result reports as ``guestd_cpu_ms``. Without ``<proc_root>/stat`` (macOS) no
sampling happens and the result's ``proc_samples`` is an empty list.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

GROUPS = ("browser", "renderer", "gpu", "network", "utility", "zygote", "chromium_other", "guestd", "other")

NETWORK_SUB_TYPE = "network.mojom.NetworkService"

# A classification is cached once the process is at least this old (see the module doc).
STABLE_AGE_S = 2.0

_TYPE_RE = re.compile(r"(?:^|\s)--type=(\S+)")
_SUB_TYPE_RE = re.compile(r"(?:^|\s)--utility-sub-type=(\S+)")


def _sysconf(name: str, default: int) -> int:
    try:
        value = os.sysconf(name)
    except (AttributeError, ValueError, OSError):
        return default
    return value if value > 0 else default


CLK_TCK = _sysconf("SC_CLK_TCK", 100)
PAGE_SIZE = _sysconf("SC_PAGE_SIZE", 4096)


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb", buffering=0) as f:
            return f.read()
    except OSError:
        return None


# -- parsers (pure; the tests feed them fake files) -----------------------------------


def classify(cmdline: bytes, chromium_binary: str) -> str:
    """The group of a process other than guestd, from its raw ``/proc/<pid>/cmdline``.

    Handles both layouts: NUL-separated arguments, and the single space-joined string
    Chromium's children leave after retitling themselves.
    """
    argv0 = cmdline.split(b"\0", 1)[0].decode("utf-8", "replace")
    if not argv0:
        return "other"  # kernel threads and zombies have an empty command line
    first = argv0.split(" ", 1)[0]
    is_chromium = (
        argv0 == chromium_binary
        or argv0.startswith(chromium_binary + " ")
        or os.path.basename(first) == os.path.basename(chromium_binary)
    )
    if not is_chromium:
        return "other"
    text = cmdline.replace(b"\0", b" ").decode("utf-8", "replace")
    m = _TYPE_RE.search(text)
    if m is None:
        return "browser"
    ptype = m.group(1)
    if ptype == "renderer":
        return "renderer"
    if ptype == "gpu-process":
        return "gpu"
    if ptype == "zygote":
        return "zygote"
    if ptype == "utility":
        sub = _SUB_TYPE_RE.search(text)
        return "network" if sub is not None and sub.group(1) == NETWORK_SUB_TYPE else "utility"
    return "chromium_other"


def parse_pid_stat(data: bytes) -> Optional[Tuple[int, int, int]]:
    """``(utime + stime, starttime, rss)`` from ``/proc/<pid>/stat``: ticks, ticks, pages.

    The command name (field 2) is in parentheses and may itself contain spaces and
    parentheses, so the fields are counted from the last ``)``.
    """
    end = data.rfind(b")")
    if end < 0:
        return None
    fields = data[end + 1 :].split()
    # fields[0] is field 3 (state); field k of proc(5) is fields[k - 3].
    try:
        return int(fields[11]) + int(fields[12]), int(fields[19]), int(fields[21])
    except (IndexError, ValueError):
        return None


def parse_cpu_line(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    """``(busy, idle)`` ticks from the aggregate ``cpu`` line of ``/proc/stat``."""
    for line in data.split(b"\n"):
        if line.startswith(b"cpu "):
            try:
                v = [int(x) for x in line.split()[1:]]
            except ValueError:
                return None, None
            v += [0] * (8 - len(v))
            user, nice, system, idle, iowait, irq, softirq, steal = v[:8]
            return user + nice + system + irq + softirq + steal, idle + iowait
    return None, None


def parse_mem_available(data: bytes) -> Optional[int]:
    for line in data.split(b"\n"):
        if line.startswith(b"MemAvailable:"):
            parts = line.split()
            try:
                return int(parts[1]) * 1024
            except (IndexError, ValueError):
                return None
    return None


def parse_psi_some_total(data: bytes) -> Optional[int]:
    """The ``total=`` of the ``some`` line of a ``/proc/pressure/*`` file, in µs."""
    for line in data.split(b"\n"):
        if line.startswith(b"some "):
            for part in line.split():
                if part.startswith(b"total="):
                    try:
                        return int(part[6:])
                    except ValueError:
                        return None
    return None


def parse_uptime(data: bytes) -> Optional[float]:
    try:
        return float(data.split()[0])
    except (IndexError, ValueError):
        return None


# -- the sampler ----------------------------------------------------------------------


@dataclass
class Snapshot:
    """One pass over /proc. ``procs`` maps pid to (start time, group, cpu ticks, rss bytes)."""

    t_ns: int
    cpu_busy: Optional[int]
    cpu_idle: Optional[int]
    mem_available: Optional[int]
    psi_cpu_some_total_us: Optional[int]
    procs: Dict[int, Tuple[int, str, int, int]] = field(default_factory=dict)


class ProcSampler:
    """Reads a /proc tree (``proc_root`` is injectable for tests) into snapshots and deltas."""

    def __init__(
        self,
        proc_root: str = "/proc",
        chromium_binary: str = "/usr/lib/chromium/chromium",
        self_pid: Optional[int] = None,
        clk_tck: int = CLK_TCK,
        page_size: int = PAGE_SIZE,
        stable_age_s: float = STABLE_AGE_S,
    ) -> None:
        self.proc_root = proc_root
        self.chromium_binary = chromium_binary
        self.self_pid = os.getpid() if self_pid is None else self_pid
        self.clk_tck = clk_tck
        self.page_size = page_size
        self.stable_age_ticks = stable_age_s * clk_tck
        self._groups: Dict[Tuple[int, int], str] = {}

    @property
    def available(self) -> bool:
        return os.path.isfile(os.path.join(self.proc_root, "stat"))

    def snapshot(self, t_ns: int) -> Snapshot:
        root = self.proc_root
        stat = _read(os.path.join(root, "stat"))
        busy, idle = parse_cpu_line(stat) if stat is not None else (None, None)
        meminfo = _read(os.path.join(root, "meminfo"))
        psi = _read(os.path.join(root, "pressure", "cpu"))
        uptime = _read(os.path.join(root, "uptime"))
        up = parse_uptime(uptime) if uptime is not None else None
        now_ticks = up * self.clk_tck if up is not None else None
        snap = Snapshot(
            t_ns,
            busy,
            idle,
            parse_mem_available(meminfo) if meminfo is not None else None,
            parse_psi_some_total(psi) if psi is not None else None,
        )
        try:
            names = os.listdir(root)
        except OSError:
            names = []
        groups: Dict[Tuple[int, int], str] = {}
        for name in names:
            if not name.isdigit():
                continue
            pid = int(name)
            data = _read(os.path.join(root, name, "stat"))
            if data is None:
                continue  # exited since the listdir
            parsed = parse_pid_stat(data)
            if parsed is None:
                continue
            ticks, start, rss_pages = parsed
            key = (pid, start)
            group = self._groups.get(key)
            if group is None:
                if pid == self.self_pid:
                    group = "guestd"
                else:
                    cmdline = _read(os.path.join(root, name, "cmdline"))
                    group = classify(cmdline or b"", self.chromium_binary)
                # Cache only once the process is old enough not to retitle or exec any more.
                if now_ticks is not None and now_ticks - start >= self.stable_age_ticks:
                    groups[key] = group
            else:
                groups[key] = group
            snap.procs[pid] = (start, group, ticks, rss_pages * self.page_size)
        self._groups = groups  # drops the processes that are gone
        return snap

    def delta(self, prev: Snapshot, cur: Snapshot) -> Dict[str, Any]:
        """One sample: ``cur`` against ``prev`` (see the module doc for the accounting rules)."""
        ms_per_tick = 1000.0 / self.clk_tck
        groups: Dict[str, Dict[str, Any]] = {g: {"cpu_ms": 0.0, "rss_bytes": 0, "procs": 0} for g in GROUPS}
        for pid, (start, group, ticks, rss) in cur.procs.items():
            before = prev.procs.get(pid)
            base = before[2] if before is not None and before[0] == start else 0
            g = groups[group]
            g["cpu_ms"] += max(ticks - base, 0) * ms_per_tick
            g["rss_bytes"] += rss
            g["procs"] += 1
        for g in groups.values():
            g["cpu_ms"] = round(g["cpu_ms"], 3)

        def _d(a: Optional[int], b: Optional[int]) -> Optional[float]:
            return None if a is None or b is None else round((b - a) * ms_per_tick, 3)

        return {
            "t_ns": cur.t_ns,
            "cpu_total_ms": _d(prev.cpu_busy, cur.cpu_busy),
            "cpu_idle_ms": _d(prev.cpu_idle, cur.cpu_idle),
            "mem_available": cur.mem_available,
            "psi_cpu_some_total_us": cur.psi_cpu_some_total_us,
            "groups": groups,
        }


class TaskSampling:
    """Samples every ``interval_ms`` from ``start()`` until ``stop()``, which adds the final sample.

    Ticks are scheduled on ``t0_ns + k * interval``; a tick missed because the event loop
    was busy is skipped, not made up. Disabled (``stop()`` returns ``[]``) when there is no
    sampler, the interval is 0, or the proc root has no ``stat`` file. A sampling error is
    logged once and ends sampling; it never fails the task.
    """

    def __init__(self, sampler: Optional[ProcSampler], interval_ms: int, t0_ns: int, log=None) -> None:
        self._sampler = sampler
        self._interval_ns = int(interval_ms) * 1_000_000
        self._t0_ns = t0_ns
        self._log = log
        self._prev: Optional[Snapshot] = None
        self._task: Optional[asyncio.Task] = None
        self.samples: List[Dict[str, Any]] = []
        self.enabled = sampler is not None and self._interval_ns > 0 and sampler.available

    def start(self) -> None:
        """Take the baseline and start the periodic task. Call at task receipt."""
        if not self.enabled:
            return
        try:
            self._prev = self._snapshot()
        except Exception as e:  # noqa: BLE001 - sampling must never fail the task
            self._fail(e)
            return
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="proc-sampler")

    async def stop(self) -> List[Dict[str, Any]]:
        """End periodic sampling and take the final sample (the end of the timed region)."""
        await self._cancel()
        if self.enabled and self._prev is not None:
            try:
                self._take()
            except Exception as e:  # noqa: BLE001
                self._fail(e)
        return self.samples

    def cancel(self) -> None:
        """Stop the periodic task without a final sample (error paths)."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()

    async def _cancel(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        k = 1
        while True:
            delay_ns = self._t0_ns + k * self._interval_ns - time.monotonic_ns()
            if delay_ns > 0:
                await asyncio.sleep(delay_ns / 1e9)
            try:
                self._take()
            except Exception as e:  # noqa: BLE001
                self._fail(e)
                return
            elapsed = time.monotonic_ns() - self._t0_ns
            k = max(k + 1, elapsed // self._interval_ns + 1)

    def _snapshot(self) -> Snapshot:
        assert self._sampler is not None
        return self._sampler.snapshot(time.monotonic_ns() - self._t0_ns)

    def _take(self) -> None:
        assert self._sampler is not None and self._prev is not None
        cur = self._snapshot()
        self.samples.append(self._sampler.delta(self._prev, cur))
        self._prev = cur

    def _fail(self, e: Exception) -> None:
        self.enabled = False
        if self._log is not None:
            self._log.warning("proc sampling stopped", error=repr(e))
