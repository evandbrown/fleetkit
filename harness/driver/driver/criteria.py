"""Pass criteria, shared by the trial loop (``--stop-at-first-miss``, confirmations) and ``driver report``.

A trial passes when its protocol held and every target it is given is met:

* protocol: the trial completed, recorded no error, all N sessions reached ready, N tasks were
  dispatched, all N tasks are ok, and verify-clean was clean;
* targets (each optional): for every step name, p50 and p95 of ``duration_ms`` over the trial's ok
  tasks; p95 of ``task_ms`` over the trial's ok tasks.

A level passes iff it has at least one trial and every one of its trials passed; percentiles are
never pooled across trials for the verdict. Rows may be CSV rows (strings) or the trial loop's own
rows (typed); both give the same verdict because the CSV writer round-trips floats exactly.
"""
from __future__ import annotations

import collections

from . import schemas
from .stats import percentile, to_float

CRITERIA_KEYS = ("step_p50_target_ms", "step_p95_target_ms", "task_p95_target_ms")


def make_criteria(step_p50_target_ms=None, step_p95_target_ms=None, task_p95_target_ms=None) -> dict:
    return normalize({"step_p50_target_ms": step_p50_target_ms, "step_p95_target_ms": step_p95_target_ms,
                      "task_p95_target_ms": task_p95_target_ms})


def normalize(criteria) -> dict:
    """Every key present, each a float or None; tolerates None and unknown keys."""
    c = criteria if isinstance(criteria, dict) else {}
    return {k: to_float(c.get(k)) for k in CRITERIA_KEYS}


def has_targets(criteria) -> bool:
    return any(v is not None for v in normalize(criteria).values())


def is_ok(v) -> bool:
    return v is True or str(v).strip().lower() == "true"


def _ordered(names) -> list[str]:
    return [n for n in schemas.STEP_NAMES if n in names] + sorted(n for n in names if n not in schemas.STEP_NAMES)


def evaluate_trial(trial_doc: dict, task_rows, step_rows, criteria) -> dict:
    """-> {"passed", "protocol_ok", "targets_met", "checks": [{"name", "value", "target", "met"}], "reasons"}"""
    t = trial_doc if isinstance(trial_doc, dict) else {}
    c = normalize(criteria)
    tid = t.get("trial_id")
    n = int(to_float(t.get("level_n"), 0) or 0)
    counts = t.get("counts") or {}
    reasons: list[str] = []

    # ---- protocol
    if not t.get("complete"):
        reasons.append(f"trial not complete (phase {t.get('phase') or 'unknown'})")
    if t.get("error"):
        reasons.append(f"trial error: {t['error']}")
    ready = int(to_float(counts.get("sessions_ready"), 0) or 0)
    dispatched = int(to_float(counts.get("tasks_dispatched"), 0) or 0)
    tasks_ok = int(to_float(counts.get("tasks_ok"), 0) or 0)
    if n < 1:
        reasons.append("no concurrency level recorded")
    if ready != n:
        reasons.append(f"sessions ready {ready}/{n}")
    if dispatched != n:
        reasons.append(f"tasks dispatched {dispatched}/{n}")
    if tasks_ok != n:
        reasons.append(f"tasks ok {tasks_ok}/{n}")
    if not (t.get("verify_clean") or {}).get("clean"):
        reasons.append("verify-clean not clean")
    protocol_ok = not reasons

    # ---- targets, over the trial's ok tasks only
    tasks = [r for r in (task_rows or []) if not tid or not r.get("trial_id") or r.get("trial_id") == tid]
    steps = [r for r in (step_rows or []) if not tid or not r.get("trial_id") or r.get("trial_id") == tid]
    ok_ids = {r.get("task_id") for r in tasks if is_ok(r.get("ok"))}
    task_ms = [to_float(r.get("task_ms")) for r in tasks if is_ok(r.get("ok"))]
    task_ms = [v for v in task_ms if v is not None]
    by_step: dict[str, list[float]] = collections.defaultdict(list)
    for r in steps:
        d = to_float(r.get("duration_ms"))
        if d is not None and r.get("task_id") in ok_ids:
            by_step[str(r.get("name") or "")].append(d)

    checks: list[dict] = []

    def check(name: str, value, target: float) -> None:
        checks.append({"name": name, "value": value, "target": target,
                       "met": value is not None and value <= target})

    p50_t, p95_t, task_t = c["step_p50_target_ms"], c["step_p95_target_ms"], c["task_p95_target_ms"]
    if p50_t is not None or p95_t is not None:
        if not by_step:
            checks.append({"name": "steps", "value": None, "target": None, "met": False})
        for name in _ordered(by_step):
            if p50_t is not None:
                check(f"step {name} p50", percentile(by_step[name], 50), p50_t)
            if p95_t is not None:
                check(f"step {name} p95", percentile(by_step[name], 95), p95_t)
    if task_t is not None:
        check("task p95", percentile(task_ms, 95), task_t)
    targets_met = all(ch["met"] for ch in checks)
    for ch in checks:
        if ch["met"]:
            continue
        if ch["value"] is None:
            reasons.append(f"{ch['name']}: no data from ok tasks")
        else:
            reasons.append(f"{ch['name']} {ch['value']:.0f} ms > target {ch['target']:.0f} ms")
    return {"passed": protocol_ok and targets_met, "protocol_ok": protocol_ok, "targets_met": targets_met,
            "checks": checks, "reasons": reasons}


def level_passed(evaluations) -> bool:
    """A level passes iff it has at least one trial and every trial passed."""
    evs = [e for e in (evaluations or []) if e is not None]
    return bool(evs) and all(bool(e.get("passed")) for e in evs)
