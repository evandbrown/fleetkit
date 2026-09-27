"""Guest metrics from /proc: memory totals and Chromium's resident set.

All values are bytes. On a platform without /proc (running the tests on macOS) the
values are None rather than wrong.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def meminfo() -> Dict[str, Optional[int]]:
    """``mem_total``, ``mem_available`` and ``cached`` from /proc/meminfo, in bytes."""
    out: Dict[str, Optional[int]] = {"mem_total": None, "mem_available": None, "cached": None}
    wanted = {"MemTotal": "mem_total", "MemAvailable": "mem_available", "Cached": "cached"}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in wanted:
                    parts = rest.split()
                    if parts:
                        out[wanted[key]] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass
    return out


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
