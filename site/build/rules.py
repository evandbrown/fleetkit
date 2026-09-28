"""The rules in DATA.md ("Rules"), as the builder applies them. site/src/lib/derive.ts has the same rules for
the contract check; tests/test_rules.py pins both to the same cases."""
from __future__ import annotations

import math

COUNTING = ("ladder", "boundary")
SEVERITY = ["host_cpu", "microvm_cpu_allowance", "host_memory", "io", "steal"]
STEPS = ["home", "search", "open_product", "add_to_cart", "verify_cart"]
GUEST = ["renderer", "browser", "other_chromium", "guestd", "other"]
CONSUMERS = ["microvm_vcpus", "hypervisor", "hostd", "driver", "unattributed"]


def r4(v):
    """Summary precision: at most 4 decimals (DATA.md, Precision)."""
    if v is None:
        return None
    v = round(float(v), 4)
    return int(v) if v.is_integer() and abs(v) < 1e15 else v


def sig3(v):
    """Time series precision: 3 significant figures."""
    if v is None:
        return None
    v = float(v)
    if v == 0 or not math.isfinite(v):
        return 0
    out = float(f"{v:.3g}")
    return int(out) if out.is_integer() and abs(out) < 1e15 else out


def num(v):
    """A recorded input as a JSON number: 1000.0 -> 1000; real fractions stay."""
    f = float(v)
    return int(f) if f.is_integer() else f


def rng(values, digits=4):
    v = [x for x in values if x is not None]
    return [r4(round(min(v), digits)), r4(round(max(v), digits))] if v else None


def trial_passes(ready_met: bool, tasks_ok: int, tasks_of: int, checks: list[dict], clean: bool) -> bool:
    """Rule 1: a trial passes if it meets every criterion in its spec."""
    return bool(ready_met and tasks_ok == tasks_of and all(c["met"] for c in checks) and clean)


def density_briefs(densities: list[int], trials: list[dict]) -> list[dict]:
    """Rule 3: each listed density's result from the trials that count; not_tested when none ran there."""
    out = []
    for d in densities:
        at = [t for t in trials if t["counts"] and t["density"] == d]
        passed = sum(1 for t in at if t["passed"] is True)
        result = "not_tested" if not at else "passed" if passed == len(at) else "failed"
        out.append({"density": d, "result": result, "passed": passed, "trials": len(at)})
    return out


def result_core(briefs: list[dict]) -> dict:
    """Rule 4: the highest listed density that passed with every lower listed density passing too."""
    tested = None
    for b in briefs:
        if b["result"] != "passed":
            break
        tested = b["density"]
    failed = [b["density"] for b in briefs if b["result"] == "failed"]
    first_failed = min(failed) if failed else None
    gap = ([tested + 1, first_failed - 1]
           if tested is not None and first_failed is not None and first_failed - tested > 1 else None)
    return {"tested_successfully": tested, "first_failed": first_failed, "gap": gap,
            "not_tested": [b["density"] for b in briefs if b["result"] == "not_tested"]}


def full_size_trials(trials: list[dict], tested: int | None, first_failed: int | None) -> set[str]:
    """Rule 10: the trials whose screenshots are published at full size. The last pass: trial 1 at the run's result
    (every trial there passed). The first failure: the first trial that failed at the run's first failing density,
    the one Results opens at the limit (trial 1, unless it passed there). And every illustration trial (its
    filmstrip)."""
    counting = sorted((t for t in trials if t["counts"]), key=lambda t: t["number"])
    last_pass = next((t for t in counting if t["density"] == tested), None)
    first_failure = next((t for t in counting if t["density"] == first_failed and t["passed"] is False), None)
    return {t["id"] for t in trials if t["role"] == "illustration"} | \
        {t["id"] for t in (last_pass, first_failure) if t is not None}


def midpoint(tested: int | None, first_failed: int | None, host_vcpus: int) -> float | None:
    """Rule 6 (D60): a run's result spans from the highest density that passed (0 if none did) to the lowest
    that failed, per host vCPU; its midpoint. None when no density failed: the result is only "at least"."""
    if first_failed is None:
        return None
    return ((tested or 0) + first_failed) / 2 / host_vcpus


def mean_midpoint(midpoints: list) -> float | None:
    """A spec's midpoint: the mean of its replicas' midpoints; None if any replica has none."""
    if not midpoints or any(m is None for m in midpoints):
        return None
    return sum(midpoints) / len(midpoints)


def cost_per_1000(price_usd_per_hour: float, seconds: float, density: int) -> float:
    """Rule 5: USD per 1,000 tasks for a window of ``seconds`` shared by ``density`` tasks."""
    return 1000 * price_usd_per_hour * seconds / 3600 / density


def fired(value, op: str, threshold) -> bool:
    if value is None:
        return False
    return value >= threshold if op == ">=" else value < threshold


def union_verdicts(lists: list[list[str]]) -> list[str]:
    """The union of verdicts, most severe first; none only if nothing fired and nothing was unknown."""
    seen = {v for lst in lists for v in lst}
    out = [v for v in SEVERITY if v in seen]
    if out:
        return out
    return ["unknown"] if "unknown" in seen or not seen else ["none"]


def verdicts_from_rules(rule_defs: list[dict], values: dict) -> list[str]:
    """What limited a trial, from its signals and the spec's thresholds (the attribution rule)."""
    hit = {rd["verdict"] for rd in rule_defs if fired(values.get(rd["key"]), rd["op"], rd["threshold"])}
    out = [v for v in SEVERITY if v in hit]
    if out:
        return out
    return ["unknown"] if any(values.get(rd["key"]) is None for rd in rule_defs) else ["none"]


def separating_rule(rule_defs: list[dict], passing: dict, failing: dict):
    """The first rule, most severe first, whose values at the last passing and the first failing density don't
    overlap and whose threshold lies between them. ``passing`` and ``failing``: rule key -> [values]."""
    order = sorted(rule_defs, key=lambda rd: SEVERITY.index(rd["verdict"]))
    for rd in order:
        p = [v for v in passing.get(rd["key"], []) if v is not None]
        f = [v for v in failing.get(rd["key"], []) if v is not None]
        if not p or not f:
            continue
        if rd["op"] == ">=" and max(p) < rd["threshold"] <= min(f):
            return rd
        if rd["op"] == "<" and min(p) >= rd["threshold"] > max(f):
            return rd
    return None
