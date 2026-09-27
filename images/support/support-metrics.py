#!/usr/bin/env python3
"""1 Hz metrics of the support host (fixture and observability backend).

Runs as the systemd unit fleetkit-support-metrics (images/support/cloud-config.yaml)
and appends to /var/lib/fleetkit/support-metrics.csv in the same long format as the
driver's host_metrics.csv, so one reader handles both:

    ts, session_id, metric, value

``session_id`` is ``support`` for host rows (``cpu_util`` and ``steal`` in percent over
the last interval, from /proc/stat like the host daemon; ``mem_available`` in bytes
from /proc/meminfo) and the container name for container rows (``cpu_usage_usec``,
the cumulative ``usage_usec`` from the container's cgroup v2 ``cpu.stat``). A value
that cannot be read is left out rather than written empty. ``ts`` is Unix seconds.

Standard library only. The proc and cgroup roots are arguments so the parsing can
be exercised against a fake tree off Linux.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

CONTAINERS = ("fleetkit-fixture", "fleetkit-lgtm")
COLUMNS = ["ts", "session_id", "metric", "value"]
RESOLVE_EVERY_S = 10.0


def read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def cpu_counters(proc: Path) -> dict | None:
    """Aggregate jiffies from the first line of /proc/stat: idle (idle + iowait), steal, total."""
    text = read_text(proc / "stat")
    if not text:
        return None
    first = text.splitlines()[0].split()
    if not first or first[0] != "cpu" or len(first) < 9:
        return None
    user, nice, system, idle, iowait, irq, softirq, steal = (int(x) for x in first[1:9])
    return {"idle": idle + iowait, "steal": steal,
            "total": user + nice + system + idle + iowait + irq + softirq + steal}


def mem_available(proc: Path) -> int | None:
    text = read_text(proc / "meminfo")
    for line in (text or "").splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            return int(parts[1]) * 1024 if len(parts) >= 2 else None
    return None


def container_ids(names=CONTAINERS) -> dict:
    """Full ids of the running containers, by name; a missing container is absent."""
    ids = {}
    for name in names:
        try:
            res = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", name],
                                 capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0 and res.stdout.strip():
            ids[name] = res.stdout.strip()
    return ids


def cgroup_dir(cgroup: Path, cid: str) -> Path | None:
    """The container's cgroup v2 directory: systemd driver first, then cgroupfs."""
    for d in (cgroup / "system.slice" / f"docker-{cid}.scope", cgroup / "docker" / cid):
        if (d / "cpu.stat").exists():
            return d
    return None


def usage_usec(d: Path) -> int | None:
    for line in (read_text(d / "cpu.stat") or "").splitlines():
        k, _, v = line.partition(" ")
        if k == "usage_usec":
            return int(v)
    return None


class Sampler:
    def __init__(self, proc: Path, cgroup: Path, resolve=container_ids):
        self.proc, self.cgroup, self.resolve = proc, cgroup, resolve
        self.prev = cpu_counters(proc)
        self.dirs: dict = {}
        self.resolved_at = float("-inf")

    def _cgroups(self, now: float) -> dict:
        # Container ids and cgroup paths are looked up again every RESOLVE_EVERY_S, so a
        # recreated container is picked up within that; a stopped one simply has no rows.
        if now - self.resolved_at >= RESOLVE_EVERY_S:
            self.dirs = {name: cgroup_dir(self.cgroup, cid) for name, cid in self.resolve().items()}
            self.resolved_at = now
        return self.dirs

    def sample(self, ts: float) -> list:
        rows = []
        cur = cpu_counters(self.proc)
        if cur and self.prev:
            total = cur["total"] - self.prev["total"]
            if total > 0:
                rows.append([ts, "support", "cpu_util", round(100.0 * (1 - (cur["idle"] - self.prev["idle"]) / total), 2)])
                rows.append([ts, "support", "steal", round(100.0 * (cur["steal"] - self.prev["steal"]) / total, 2)])
        self.prev = cur or self.prev
        mem = mem_available(self.proc)
        if mem is not None:
            rows.append([ts, "support", "mem_available", mem])
        for name, d in sorted(self._cgroups(time.monotonic()).items()):
            u = usage_usec(d) if d else None
            if u is not None:
                rows.append([ts, name, "cpu_usage_usec", u])
        return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="/var/lib/fleetkit/support-metrics.csv")
    ap.add_argument("--period", type=float, default=1.0, help="seconds between samples")
    ap.add_argument("--count", type=int, default=0, help="stop after this many samples (0: run forever)")
    ap.add_argument("--proc", default="/proc")
    ap.add_argument("--cgroup", default="/sys/fs/cgroup")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists() or out.stat().st_size == 0
    sampler = Sampler(Path(a.proc), Path(a.cgroup))
    with open(out, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(COLUMNS)
            fh.flush()
        n = 0
        nxt = time.monotonic()
        while True:
            nxt += a.period
            time.sleep(max(0.0, nxt - time.monotonic()))
            w.writerows(sampler.sample(round(time.time(), 3)))
            fh.flush()
            n += 1
            if a.count and n >= a.count:
                return 0


if __name__ == "__main__":
    sys.exit(main())
