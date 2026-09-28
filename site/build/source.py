"""Reads one run directory into a RunSource through the harness's own reader.

``driver.outputs.RunDir`` translates a directory recorded before the glossary on read (driver/legacy.py is the
one alias map for old names), and ``driver.report.build_report`` recomputes every trial's evaluation,
attribution and cost with the harness's own code. So the builder reads a single vocabulary and keeps no copy of
the old names. Nothing here is published directly: assemble.py builds every output field from these values.
Paths come only from the catalog.
"""
from __future__ import annotations

import collections
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness/driver"))

from driver import outputs as harness_outputs  # noqa: E402
from driver import report as harness_report  # noqa: E402

# The harness's names for its attribution rules and verdicts, and the site's (DATA.md). These are the
# harness's current vocabulary, not old names.
RULES = [  # harness threshold key, harness signal key, site key, label, verdict, op
    ("host_psi_cpu_some_pct", "psi_cpu_some_pct", "host_cpu_pressure_pct", "Host CPU pressure", "host_cpu", ">="),
    ("host_cpu_util_mean_pct", "cpu_util_mean_pct", "host_cpu_util_pct", "Host CPU utilisation", "host_cpu", ">="),
    ("vm_throttled_fraction_mean", "vm_throttled_fraction_mean", "microvm_throttled_fraction",
     "MicroVM throttled fraction", "microvm_cpu_allowance", ">="),
    ("host_mem_available_min_fraction", "mem_available_min_fraction", "host_mem_available_fraction",
     "Host memory available", "host_memory", "<"),
    ("host_psi_memory_some_pct", "psi_memory_some_pct", "host_mem_pressure_pct", "Host memory pressure", "host_memory", ">="),
    ("host_psi_io_some_pct", "psi_io_some_pct", "host_io_pressure_pct", "Host IO pressure", "io", ">="),
    ("host_steal_mean_pct", "steal_mean_pct", "host_steal_pct", "Steal", "steal", ">="),
]
SIGNAL_TO_RULE = {sig: key for _, sig, key, *_ in RULES}
VERDICTS = {"host_cpu": "host_cpu", "vm_cpu_quota": "microvm_cpu_allowance", "host_memory": "host_memory",
            "io": "io", "steal": "steal", "none": "none", "unknown": "unknown"}
# Published roles. Smoke, case and fault trials test the harness, not the hosts, so they're never published.
ROLES = ("ladder", "boundary", "warmup", "illustration")
HYPERVISORS = {"firecracker": "firecracker", "cloud-hypervisor": "cloud-hypervisor",
               "cloud_hypervisor": "cloud-hypervisor", "chv": "cloud-hypervisor"}


class SourceError(Exception):
    """The run directory can't be published as it is."""


@dataclass
class TrialSource:
    id: str                 # the harness's trial id; a join key only (the builder numbers published trials itself)
    doc: dict               # trial.json, in the glossary's names
    report: dict            # its row in the harness's report (evaluation, attribution, cost)
    role: str
    density: int
    start_ts: float         # create_start: t = 0 for the trial


@dataclass
class RunSource:
    dir: Path
    run: dict                  # run.json, in the glossary's names
    manifest: dict
    report: dict               # the harness's report, rebuilt from the directory
    instance_type: str
    price_usd_per_hour: float
    trials: list[TrialSource]
    microvm_rows: dict = field(default_factory=dict)   # trial id -> slot -> row
    task_rows: dict = field(default_factory=dict)      # trial id -> slot -> row
    step_rows: dict = field(default_factory=dict)      # task id -> [rows]
    host: dict = field(default_factory=dict)           # (subject, metric) -> [(ts, value)], sorted
    guest: dict = field(default_factory=dict)          # task id -> ts -> metric -> value
    dropped: collections.Counter = field(default_factory=collections.Counter)   # trials not published, by kind


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SourceError(f"missing {p.name} in {p.parent}") from None


def read_env(p: Path | None) -> dict:
    """KEY=VALUE lines (a capacity.env): only the instance types."""
    if not p or not p.exists():
        return {}
    raw = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            raw[k.strip()] = v.strip()
    keep = {"INSTANCE_TYPE_EXPECTED": "instance_type", "SUPPORT_INSTANCE_TYPE": "support_instance_type"}
    return {new: raw[old] for old, new in keep.items() if old in raw}


def read_run(run_dir: Path, types: dict, env_file: Path | None = None) -> tuple[RunSource, dict]:
    """The run, and what the builder read from the env file (for a run recorded before specs existed)."""
    if not (run_dir / "run.json").exists():
        raise SourceError(f"{run_dir}: no run.json")
    rd = harness_outputs.RunDir(run_dir)
    run = rd.read_run_json()
    manifest = read_json(run_dir / "manifest.json")
    if HYPERVISORS.get(str(run.get("backend"))) is None:
        raise SourceError(f"{run_dir}: hypervisor {run.get('backend')!r} isn't published (Docker runs never are, D39)")
    env = read_env(env_file)
    instance = env.get("instance_type") or manifest.get("instance_type")
    if instance not in types:
        raise SourceError(f"{run_dir}: instance type {instance!r} isn't in experiments/schema/instance-types.json")
    price = float(types[instance]["usd_per_hour"])
    report = harness_report.build_report(rd, price, instance, None, None, None)
    report_trials = {t["trial_id"]: t for t in report["trials"]}

    # A trial that failed outside the experiment (D63) isn't here: the harness sets it aside under ops/ and runs it
    # again, and the builder reads only the run's trials.
    trials, dropped = [], collections.Counter()
    for doc in rd.list_trials():
        tid = str(doc["trial_id"])
        kind = harness_report.trial_kind(doc)
        if kind not in ROLES:
            dropped[kind] += 1
            continue
        if kind == "illustration" and doc["timestamps"].get("create_start") is None:
            # An illustration shows the task, it measures nothing: one cut off before it started has nothing to show.
            dropped["illustration not started"] += 1
            continue
        rep = report_trials.get(tid)
        if rep is None:
            raise SourceError(f"{run_dir}: trial {tid} is missing from the harness's report")
        trials.append(TrialSource(id=tid, doc=doc, report=rep, role=kind, density=int(doc["density"]),
                                  start_ts=float(doc["timestamps"]["create_start"])))
    trials.sort(key=lambda t: t.start_ts)
    if not any(t.role in ("ladder", "boundary") for t in trials):
        raise SourceError(f"{run_dir}: no trials that count; not a capacity run (validation runs aren't published)")

    src = RunSource(dir=run_dir, run=run, manifest=manifest, report=report, instance_type=instance,
                    price_usd_per_hour=price, trials=trials, dropped=dropped)
    for r in rd.iter_rows("microvms"):
        src.microvm_rows.setdefault(r["trial_id"], {})[int(r["slot"])] = r
    for r in rd.iter_rows("tasks"):
        src.task_rows.setdefault(r["trial_id"], {})[int(r["slot"])] = r
    for r in rd.iter_rows("steps"):
        src.step_rows.setdefault(r["task_id"], []).append(r)
    host = collections.defaultdict(list)
    for r in rd.iter_rows("host_metrics"):
        host[(r["subject"], r["metric"])].append((float(r["ts"]), float(r["value"])))
    for v in host.values():
        v.sort()
    src.host = dict(host)
    guest = collections.defaultdict(lambda: collections.defaultdict(dict))
    for r in rd.iter_rows("guest_metrics"):
        guest[r["task_id"]][float(r["ts"])][r["metric"]] = float(r["value"])
    src.guest = guest
    return src, env
