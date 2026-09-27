"""Small readers for /proc and cgroup v2 files, shared by the sampler, the Firecracker backend
and GET /host/info.

Every reader takes its root (the proc root, a cgroup directory) as an argument so tests can
point it at a fake tree; macOS has no /proc. A missing or unreadable file reads as None and
nothing here raises on bad input.
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional

PROC_ROOT = "/proc"
VCPU_THREAD_PREFIX = "fc_vcpu"      # Firecracker names its vCPU threads "fc_vcpu <n>"


def read_text(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def clk_tck() -> int:
    """Clock ticks per second for utime/stime (SC_CLK_TCK; 100 when unknown)."""
    try:
        v = os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError, OSError):
        return 100
    return int(v) if v and v > 0 else 100


def parse_pressure(text: Optional[str]) -> Dict[str, Optional[float]]:
    """A PSI file (/proc/pressure/<res> or <cgroup>/<res>.pressure) ->
    {some_avg10, some_total, full_avg10, full_total}. Totals are cumulative microseconds of
    stall, avg10 a percentage; None where the file or the line is missing."""
    entry: Dict[str, Optional[float]] = {"some_avg10": None, "some_total": None, "full_avg10": None, "full_total": None}
    for line in (text or "").splitlines():
        parts = line.split()
        if not parts or parts[0] not in ("some", "full"):
            continue
        kind = parts[0]
        kv = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        for key in ("avg10", "total"):
            if key in kv:
                try:
                    entry["%s_%s" % (kind, key)] = float(kv[key])
                except ValueError:
                    pass
    return entry


def parse_flat_keyed(text: Optional[str]) -> Dict[str, int]:
    """`key value` lines (cgroup cpu.stat, memory.stat) -> {key: int}; unparsable lines skipped."""
    out: Dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


def read_int(path: str) -> Optional[int]:
    text = read_text(path)
    try:
        return int(text.strip()) if text is not None else None
    except ValueError:
        return None


def cgroup_pids(cgroup_dir: str) -> List[str]:
    """The pids in a cgroup's cgroup.procs (empty when unreadable)."""
    return [p.strip() for p in (read_text(os.path.join(cgroup_dir, "cgroup.procs")) or "").splitlines() if p.strip()]


def stat_comm(text: Optional[str]) -> Optional[str]:
    """Field 2 of a /proc/<pid>/stat line, without its parentheses."""
    if not text:
        return None
    start, end = text.find("("), text.rfind(")")
    return text[start + 1:end] if 0 <= start < end else None


def stat_cpu_ticks(text: Optional[str]) -> Optional[int]:
    """utime + stime (fields 14 and 15) in clock ticks from a /proc/<pid>[/task/<tid>]/stat line.

    comm (field 2) may contain spaces and parentheses, so fields are counted after its last ')':
    the first token there is field 3 (state), which puts utime at index 11 and stime at 12.
    """
    if not text:
        return None
    end = text.rfind(")")
    if end < 0:
        return None
    rest = text[end + 1:].split()
    try:
        return int(rest[11]) + int(rest[12])
    except (IndexError, ValueError):
        return None


def status_rss_bytes(proc_root: str, pid: str) -> Optional[int]:
    """VmRSS of /proc/<pid>/status in bytes (None for a kernel thread or a vanished pid)."""
    for line in (read_text(os.path.join(proc_root, str(pid), "status")) or "").splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            try:
                return int(parts[1]) * 1024
            except (IndexError, ValueError):
                return None
    return None


def thread_cpu_split(pids: Iterable[str], proc_root: str = PROC_ROOT, tck: Optional[int] = None,
                     vcpu_prefix: str = VCPU_THREAD_PREFIX) -> Dict[str, Optional[int]]:
    """{vcpu_usec, other_usec}: cumulative utime+stime of every thread of `pids`, split by name.

    Threads are /proc/<pid>/task/<tid>; a thread whose comm starts with `vcpu_prefix` counts as
    vCPU time, every other thread (Firecracker's main event loop with the device emulation, its
    API thread) as hypervisor time. A thread that has already exited is not counted: its time moves to
    the process total, not to any task entry. Firecracker's threads live as long as the VM, so
    nothing is lost in practice. Both values are None when no thread could be read.
    """
    tck = tck or clk_tck()
    vcpu = other = 0
    found = False
    for pid in pids:
        task_dir = os.path.join(proc_root, str(pid), "task")
        try:
            tids = os.listdir(task_dir)
        except OSError:
            continue
        for tid in tids:
            stat = read_text(os.path.join(task_dir, tid, "stat"))
            ticks = stat_cpu_ticks(stat)
            if ticks is None:
                continue
            comm = read_text(os.path.join(task_dir, tid, "comm"))
            name = comm.strip() if comm is not None else (stat_comm(stat) or "")
            found = True
            if name.startswith(vcpu_prefix):
                vcpu += ticks
            else:
                other += ticks
    if not found:
        return {"vcpu_usec": None, "other_usec": None}
    return {"vcpu_usec": vcpu * 1_000_000 // tck, "other_usec": other * 1_000_000 // tck}
