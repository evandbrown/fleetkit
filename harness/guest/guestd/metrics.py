"""Guest metrics and static facts from /proc: memory totals, Chromium's resident set,
the kernel's uptime and command line.

Memory values are bytes. On a platform without /proc (running the tests on macOS) the
values are None rather than wrong. ``proc_root`` is injectable so tests can use a fake tree.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def meminfo(proc_root: str = "/proc") -> Dict[str, Optional[int]]:
    """``mem_total``, ``mem_available`` and ``cached`` from /proc/meminfo, in bytes."""
    out: Dict[str, Optional[int]] = {"mem_total": None, "mem_available": None, "cached": None}
    wanted = {"MemTotal": "mem_total", "MemAvailable": "mem_available", "Cached": "cached"}
    try:
        with open(os.path.join(proc_root, "meminfo"), "r", encoding="ascii") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in wanted:
                    parts = rest.split()
                    if parts:
                        out[wanted[key]] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass
    return out


def kernel_uptime_s(proc_root: str = "/proc") -> Optional[float]:
    """The first field of /proc/uptime: seconds since the kernel booted."""
    try:
        with open(os.path.join(proc_root, "uptime"), "r", encoding="ascii") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def kernel_cmdline(proc_root: str = "/proc") -> Optional[str]:
    """/proc/cmdline without the trailing newline."""
    try:
        with open(os.path.join(proc_root, "cmdline"), "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def rss_of_processes(cmdline_prefix: str) -> Optional[int]:
    """Sum of resident set sizes of every process whose argv[0] starts with the prefix.

    Chromium is a process tree (browser, renderers, GPU, utility); all of them share the
    same binary path, so matching argv[0] sums the whole tree. Shared pages are counted
    once per process, which overstates the tree's real footprint; that is what RSS means.
    """
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    total = 0
    needle = cmdline_prefix.encode()
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv0 = f.read().split(b"\0", 1)[0]
            if not argv0.startswith(needle):
                continue
            with open(f"/proc/{pid}/statm", "r", encoding="ascii") as f:
                fields = f.read().split()
            total += int(fields[1]) * _PAGE_SIZE
        except (OSError, ValueError, IndexError):
            continue
    return total
