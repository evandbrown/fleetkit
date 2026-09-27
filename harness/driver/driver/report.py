"""``driver report``: per level p50/p95 per step and per task, failure rates by category, targets,
harness overhead, the highest N that passed, and cost per task (design section 11, "Report").

Every number is labeled measured, modeled or assumed. The headline is the highest N actually tested
successfully; it is never called maximum capacity.

Criteria come from the CLI flags when any is given, else from run.json ``criteria`` (so ``report
--run`` alone reproduces the run's own verdicts). Each trial is evaluated on its own
(driver/criteria.py); a level passes iff every ladder/confirm trial at it passed. Pooled
percentiles across a level's trials are reported for information only. Warmup, illustration and
fault trials are listed apart. Each trial carries an attribution over its task window
(driver/attribution.py).
"""
from __future__ import annotations

import collections
import json
import time
from pathlib import Path

from . import schemas
from .attribution import attribute_trial, load_guest_sums, load_host_series, summarize_verdicts
from .criteria import evaluate_trial, level_passed, make_criteria, normalize
from .outputs import RunDir, read_csv, read_json, write_json_atomic
from .stats import mean, percentile, summary, to_float, to_int

S3_GET_PRICE_PER_1000_DEFAULT = 0.0004  # USD per 1,000 GET requests, S3 Standard, public on-demand price

MEASURED, MODELED, ASSUMED, ESTIMATE = "measured", "modeled", "assumed", "estimate"


def resolve_criteria(step_target_ms, step_p95_ms, task_target_ms, run_json: dict) -> tuple[dict, str]:
    """-> (criteria, source): the CLI's when any flag is given, else run.json's, else none."""
    if any(v is not None for v in (step_target_ms, step_p95_ms, task_target_ms)):
        return make_criteria(step_target_ms, step_p95_ms, task_target_ms), "cli"
    rc = run_json.get("criteria")
    if isinstance(rc, dict):
        return normalize(rc), "run.json"
    return make_criteria(), "none"


def trial_kind(t: dict) -> str:
    """trial.json ``kind``; inferred for runs from before kinds existed."""
    k = t.get("kind")
    if k in schemas.TRIAL_KINDS:
        return k
    if t.get("fault"):
        return "fault"
    if "-smoke-" in str(t.get("trial_id") or ""):
        return "smoke"
    return "ladder"


def build_report(rundir: RunDir, price_per_hour: float | None, instance: str | None,
                 step_target_ms: float | None, step_p95_ms: float | None, task_target_ms: float | None,
                 s3_get_price_per_1000: float = S3_GET_PRICE_PER_1000_DEFAULT,
                 fixture_manifest: str | None = None, bytes_tolerance: float = 0.25) -> dict:
    trials = [t for t in rundir.list_trials()]
    tasks = read_csv(rundir.tasks_csv)
    steps = read_csv(rundir.steps_csv)
    sessions = read_csv(rundir.sessions_csv)
    manifest = read_json(rundir.manifest_json, {}) or {}
    run_json = read_json(rundir.run_json, {}) or {}
    expected = _expected_from_manifest(fixture_manifest)
    criteria, criteria_source = resolve_criteria(step_target_ms, step_p95_ms, task_target_ms, run_json)
    run_inputs = run_json.get("inputs") if isinstance(run_json.get("inputs"), dict) else {}
    host_info = run_inputs.get("host_info") if isinstance(run_inputs.get("host_info"), dict) else {}
    ec2 = host_info.get("ec2") if isinstance(host_info.get("ec2"), dict) else {}
    instance_source = "operator input" if instance else None
    if not instance and ec2.get("instance_type"):
        instance, instance_source = ec2["instance_type"], "host_info (IMDS)"
    host_series = load_host_series(rundir.host_metrics_csv)
    guest_sums = load_guest_sums(rundir.guest_metrics_csv)

    tasks_by_trial = collections.defaultdict(list)
    for r in tasks:
        tasks_by_trial[r["trial_id"]].append(r)
    steps_by_trial = collections.defaultdict(list)
    for r in steps:
        steps_by_trial[r["trial_id"]].append(r)
    sessions_by_trial = collections.defaultdict(list)
    for r in sessions:
        sessions_by_trial[r["trial_id"]].append(r)

    levels: dict[tuple[str, int], dict] = {}
    fault_trials = []
    excluded_trials = []
    trial_rows = []
    for t in trials:
        tid = t["trial_id"]
        tr = _trial_summary(t, tasks_by_trial[tid], steps_by_trial[tid], sessions_by_trial[tid],
                            price_per_hour, s3_get_price_per_1000, expected, bytes_tolerance)
        tr["kind"] = trial_kind(t)
        tr["evaluation"] = evaluate_trial(t, tasks_by_trial[tid], steps_by_trial[tid], criteria)
        tr["attribution"] = attribute_trial(t, host_series, guest_sums.get(tid),
                                            cpu_count=host_info.get("cpu_count"), mem_total=host_info.get("mem_total"))
        tr["pre_trial"] = t.get("pre_trial")
        trial_rows.append(tr)
        if t.get("fault") or tr["kind"] == "fault":
            fault_trials.append(tr)
            continue
        if tr["kind"] not in schemas.LEVEL_KINDS:
            excluded_trials.append(tr)
            continue
        key = (t["backend"], int(t["level_n"]))
        levels.setdefault(key, {"backend": key[0], "level_n": key[1], "trials": [], "tasks": [], "steps": [],
                                "sessions": []})
        levels[key]["trials"].append(tr)
        levels[key]["tasks"].extend(tasks_by_trial[tid])
        levels[key]["steps"].extend(steps_by_trial[tid])
        levels[key]["sessions"].extend(sessions_by_trial[tid])

    level_rows = []
    for key in sorted(levels):
        lv = levels[key]
        level_rows.append(_level_summary(lv, criteria["step_p50_target_ms"], criteria["step_p95_target_ms"],
                                         criteria["task_p95_target_ms"], price_per_hour,
                                         s3_get_price_per_1000, expected, bytes_tolerance))

    report = {
        "run_id": rundir.run_id,
        "generated_at": time.time(),
        "inputs": {
            "price_per_hour_usd": price_per_hour, "instance": instance, "instance_source": instance_source,
            "step_target_ms": criteria["step_p50_target_ms"], "step_p95_ms": criteria["step_p95_target_ms"],
            "task_target_ms": criteria["task_p95_target_ms"],
            "criteria": criteria, "criteria_source": criteria_source,
            "s3_get_price_per_1000_usd": s3_get_price_per_1000, "fixture_manifest": fixture_manifest,
            "bytes_tolerance": bytes_tolerance, "expected_per_task": expected,
            "host_info": host_info or None,
        },
        "labels": {
            "price_per_hour_usd": ASSUMED + " (public on-demand price, operator input)",
            "s3_get_price_per_1000_usd": ASSUMED + " (public S3 Standard request price)",
            "timings": MEASURED, "counts": MEASURED, "cost_per_task": MODELED + " from measured windows and assumed prices",
            "fixture_cost": ESTIMATE + " (nginx served the fixture; modeled at S3 request pricing)",
            "attribution": MEASURED + " numbers; verdicts rule-based, from measured signals",
        },
        "host_provisioning": _host_provisioning(manifest, run_json),
        "headline": _headline(level_rows),
        "plan": run_json.get("plan"),
        "levels": level_rows,
        "trials": trial_rows,
        "fault_trials": fault_trials,
        "excluded_trials": [{"trial_id": t["trial_id"], "kind": t["kind"], "level_n": t["level_n"],
                             "status": t["status"]} for t in excluded_trials + fault_trials],
    }
    return report


def _headline(level_rows: list[dict]) -> dict:
    """Per backend: the highest N whose level passed with every lower tested level passing, and the
    boundary (last pass, first miss, trials at each)."""
    by_backend: dict[str, list[dict]] = collections.defaultdict(list)
    for lr in level_rows:
        by_backend[lr["backend"]].append(lr)
    out = {}
    for b, rows in by_backend.items():
        last_pass = first_miss = None
        for lr in sorted(rows, key=lambda r: r["level_n"]):
            if lr["passed"]:
                if first_miss is None:
                    last_pass = lr
            elif first_miss is None:
                first_miss = lr
        out[b] = {
            "highest_n_tested_successfully": last_pass["level_n"] if last_pass else 0,
            "note": "docker is the development backend; real numbers come only from firecracker on AWS"
            if b == "docker" else "firecracker measurement backend",
            "boundary": {
                "last_pass": last_pass["level_n"] if last_pass else None,
                "last_pass_trials": last_pass["repeats"] if last_pass else 0,
                "first_miss": first_miss["level_n"] if first_miss else None,
                "first_miss_trials": first_miss["repeats"] if first_miss else 0,
            },
            "limit_found": first_miss is not None,
        }
    return out


def _expected_from_manifest(path: str | None) -> dict:
    if not path:
        return {}
    m = read_json(Path(path), None)
    if not isinstance(m, dict):
        return {"error": f"fixture manifest {path} not readable"}
    task = m.get("task") if isinstance(m.get("task"), dict) else {}
    # The fixture manifest carries its own tolerance object, {bytes_pct, requests_pct}; it wins over
    # the --bytes-tolerance default when present.
    tol = m.get("tolerance") if isinstance(m.get("tolerance"), dict) else {}
    bytes_pct = to_float(tol.get("bytes_pct"))
    requests_pct = to_float(tol.get("requests_pct"))
    return {
        "bytes": to_float(m.get("expected_bytes", m.get("expected_bytes_per_task", task.get("expected_bytes")))),
        "requests": to_float(m.get("expected_request_count", m.get("expected_requests_per_task",
                                                                 task.get("expected_request_count")))),
        "bytes_tolerance": bytes_pct / 100.0 if bytes_pct is not None else None,
        "requests_tolerance": requests_pct / 100.0 if requests_pct is not None else None,
        "source": path,
    }


def _host_provisioning(manifest: dict, run_json: dict) -> dict:
    v = to_float(manifest.get("host_provisioning_s", run_json.get("host_provisioning_s")))
    return {"seconds": v, "label": MEASURED if v is not None else "not measured",
            "note": "on its own line; in neither cost window"}


def _cost(price_per_hour, window_s, tasks_ok):
    if price_per_hour is None or window_s is None or not tasks_ok:
        return None
    return price_per_hour * (window_s / 3600.0) / tasks_ok


def _windows(t: dict) -> dict:
    ts = t.get("timestamps") or {}
    exec_w = None
    obs_w = None
    if ts.get("last_task_return") is not None and ts.get("barrier_release") is not None:
        exec_w = ts["last_task_return"] - ts["barrier_release"]
    if ts.get("verify_clean_pass") is not None and ts.get("create_start") is not None:
        obs_w = ts["verify_clean_pass"] - ts["create_start"]
    return {"execution_s": exec_w, "observed_s": obs_w}


def _bytes_flag(mean_bytes, mean_reqs, expected: dict, tol: float) -> dict:
    out = {"checked": False}
    if not expected or expected.get("bytes") is None:
        return out
    out["checked"] = True
    eb = expected["bytes"]
    btol = expected.get("bytes_tolerance")
    btol = tol if btol is None else btol
    out["expected_bytes"] = eb
    out["bytes_tolerance"] = btol
    out["bytes_within_tolerance"] = (mean_bytes is not None and abs(mean_bytes - eb) <= btol * eb)
    if expected.get("requests") is not None:
        er = expected["requests"]
        rtol = expected.get("requests_tolerance")
        rtol = btol if rtol is None else rtol
        out["expected_requests"] = er
        out["requests_tolerance"] = rtol
        out["requests_within_tolerance"] = (mean_reqs is not None and abs(mean_reqs - er) <= rtol * er)
    out["note"] = "report-time flag only; never a task failure category"
    return out


def _timings(tasks) -> dict:
    """Timing percentiles over ok tasks only: a failed task's task_ms is its elapsed-to-failure
    (guestd task.py _failure), not a duration of the standard task, so it is summarized apart as
    failed_task_elapsed_ms. Counts and failure rates stay over every task."""
    ok = [r for r in tasks if r.get("ok") == "true"]
    task_ms = [to_float(r["task_ms"]) for r in ok if r.get("task_ms")]
    wall_ms = [to_float(r["wall_ms"]) for r in ok if r.get("wall_ms")]
    overhead = [to_float(r["wall_ms"]) - to_float(r["task_ms"]) for r in ok if r.get("wall_ms") and r.get("task_ms")]
    failed = [to_float(r["task_ms"]) for r in tasks if r.get("ok") != "true" and r.get("task_ms")]
    return {"task_ms": summary(task_ms), "wall_ms": summary(wall_ms), "harness_overhead_ms": summary(overhead),
            "failed_task_elapsed_ms": summary(failed)}


def _trial_summary(t: dict, tasks, steps, sessions, price, s3_price, expected, tol) -> dict:
    counts = t.get("counts") or {}
    win = _windows(t)
    tasks_ok = counts.get("tasks_ok", 0)
    timings = _timings(tasks)
    mean_bytes = mean([r["bytes_received"] for r in tasks if r.get("bytes_received")])
    mean_reqs = mean([r["request_count"] for r in tasks if r.get("request_count")])
    return {
        "trial_id": t["trial_id"], "backend": t["backend"], "level_n": int(t["level_n"]),
        "repeat": t.get("repeat"), "fault": t.get("fault"), "status": t.get("status"),
        "level_passed": bool(t.get("level_passed")), "complete": t.get("complete"), "error": t.get("error"),
        "sessions_requested": counts.get("sessions_requested"), "sessions_ready": counts.get("sessions_ready"),
        "sessions_by_outcome": counts.get("sessions_by_outcome", {}),
        "tasks_dispatched": counts.get("tasks_dispatched"), "tasks_ok": tasks_ok,
        "tasks_by_failure_category": counts.get("tasks_by_failure_category", {}),
        "actual_concurrency": t.get("actual_concurrency"),
        "verify_clean": (t.get("verify_clean") or {}).get("clean"),
        "windows_s": win,
        **timings,
        "steps": _step_percentiles(steps),
        "startup_ms": summary([r["startup_ms"] for r in sessions if r.get("startup_ms")]),
        "cleanup_ms": summary([r["cleanup_ms"] for r in sessions if r.get("cleanup_ms")]),
        "host_setup_ms": summary([(to_float(r["process_started_ts"]) - to_float(r["created_ts"])) * 1000.0
                                  for r in sessions if r.get("process_started_ts") and r.get("created_ts")]),
        "mean_bytes_received": mean_bytes, "mean_request_count": mean_reqs,
        "cost_usd_per_task": {
            "execution_only": _cost(price, win["execution_s"], tasks_ok),
            "observed": _cost(price, win["observed_s"], tasks_ok),
            "fixture_serving_estimate": (mean_reqs * s3_price / 1000.0) if mean_reqs is not None else None,
        },
        "bytes_flag": _bytes_flag(mean_bytes, mean_reqs, expected, tol),
        "timeouts": t.get("timeouts"),
    }


def _step_percentiles(steps) -> dict:
    by = collections.defaultdict(list)
    for r in steps:
        if r.get("duration_ms"):
            by[r["name"]].append(to_float(r["duration_ms"]))
    ordered = [n for n in schemas.STEP_NAMES if n in by] + sorted(n for n in by if n not in schemas.STEP_NAMES)
    return {n: summary(by[n]) for n in ordered}


def _level_summary(lv: dict, step_target, step_p95, task_target, price, s3_price, expected, tol) -> dict:
    trials, tasks, steps, sessions = lv["trials"], lv["tasks"], lv["steps"], lv["sessions"]
    n = lv["level_n"]
    timings = _timings(tasks)
    task_ms = [to_float(r["task_ms"]) for r in tasks if r.get("ok") == "true" and r.get("task_ms")]
    step_pct = _step_percentiles(steps)
    tasks_ok = sum(1 for r in tasks if r.get("ok") == "true")
    by_cat = collections.Counter(r.get("failure_category", "") for r in tasks)
    by_outcome = collections.Counter(r.get("outcome", "") for r in sessions)
    dispatched = len(tasks)

    targets = {"checked": [], "met": True}
    for name, s in step_pct.items():
        if step_target is not None:
            ok = s["p50"] is not None and s["p50"] <= step_target
            targets["checked"].append({"metric": f"step {name} p50", "value_ms": s["p50"], "target_ms": step_target, "met": ok})
            targets["met"] = targets["met"] and ok
        if step_p95 is not None:
            ok = s["p95"] is not None and s["p95"] <= step_p95
            targets["checked"].append({"metric": f"step {name} p95", "value_ms": s["p95"], "target_ms": step_p95, "met": ok})
            targets["met"] = targets["met"] and ok
    if task_target is not None:
        p95 = percentile(task_ms, 95)
        ok = p95 is not None and p95 <= task_target
        targets["checked"].append({"metric": "task p95", "value_ms": p95, "target_ms": task_target, "met": ok})
        targets["met"] = targets["met"] and ok
    if not step_pct and (step_target is not None or step_p95 is not None):
        targets["met"] = False
        targets["checked"].append({"metric": "steps", "value_ms": None, "target_ms": None, "met": False,
                                   "note": "no step rows"})

    # pooled across the level's trials: information only; the verdict is per trial
    targets["pooled"] = True
    evaluations = [t["evaluation"] for t in trials]
    protocol_passed = bool(trials) and all(e["protocol_ok"] for e in evaluations)
    passed = level_passed(evaluations)
    exec_s = [t["windows_s"]["execution_s"] for t in trials if t["windows_s"]["execution_s"] is not None]
    obs_s = [t["windows_s"]["observed_s"] for t in trials if t["windows_s"]["observed_s"] is not None]
    ok_for_cost = sum(t["tasks_ok"] for t in trials)
    mean_bytes = mean([r["bytes_received"] for r in tasks if r.get("bytes_received")])
    mean_reqs = mean([r["request_count"] for r in tasks if r.get("request_count")])
    return {
        "backend": lv["backend"], "level_n": n, "trials": [t["trial_id"] for t in trials],
        "repeats": len(trials), "statuses": [t["status"] for t in trials],
        "kinds": dict(collections.Counter(t["kind"] for t in trials)),
        "protocol_passed": protocol_passed, "targets": targets, "passed": passed,
        "trials_passed": sum(1 for e in evaluations if e["passed"]),
        "trial_evaluations": [{"trial_id": t["trial_id"], "kind": t["kind"], **t["evaluation"]} for t in trials],
        "attribution": [{"trial_id": t["trial_id"], **t["attribution"]} for t in trials],
        "verdicts": summarize_verdicts([t["attribution"] for t in trials]),
        "sessions_requested": n * len(trials), "sessions_ready": sum(t["sessions_ready"] or 0 for t in trials),
        "sessions_by_outcome": dict(by_outcome),
        "tasks_dispatched": dispatched, "tasks_ok": tasks_ok,
        "failure_rate_by_category": {c: {"count": k, "rate": (k / dispatched) if dispatched else None}
                                     for c, k in sorted(by_cat.items()) if c != "ok"},
        **timings,
        "steps": step_pct,
        "startup_ms": summary([r["startup_ms"] for r in sessions if r.get("startup_ms")]),
        "cleanup_ms": summary([r["cleanup_ms"] for r in sessions if r.get("cleanup_ms")]),
        "mean_bytes_received": mean_bytes, "mean_request_count": mean_reqs,
        "cost_usd_per_task": {
            "execution_only": _cost(price, sum(exec_s) if exec_s else None, ok_for_cost),
            "observed": _cost(price, sum(obs_s) if obs_s else None, ok_for_cost),
            "fixture_serving_estimate": (mean_reqs * s3_price / 1000.0) if mean_reqs is not None else None,
        },
        "bytes_flag": _bytes_flag(mean_bytes, mean_reqs, expected, tol),
    }


# ---- markdown ----------------------------------------------------------------------------------

def _ms(v):
    return "n/a" if v is None else f"{v:.0f}"


def _usd(v):
    return "n/a" if v is None else f"${v:.6f}"


def _s(v):
    return "n/a" if v is None else f"{v:.2f}"


def _pct1(v):
    return "n/a" if v is None else f"{v:.1f}"


def _frac_pct(v):
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _criteria_line(c: dict) -> str:
    parts = []
    if c.get("step_p50_target_ms") is not None:
        parts.append(f"every step's p50 <= {_ms(c['step_p50_target_ms'])} ms")
    if c.get("step_p95_target_ms") is not None:
        parts.append(f"every step's p95 <= {_ms(c['step_p95_target_ms'])} ms")
    if c.get("task_p95_target_ms") is not None:
        parts.append(f"task p95 <= {_ms(c['task_p95_target_ms'])} ms")
    return "; ".join(parts) if parts else "none (protocol only)"


def _attribution_table(L: list, attributions: list[dict]) -> None:
    L.append("| trial | verdict (rule-based) | host cpu mean/max % | steal mean % | PSI some cpu/mem/io % | "
             "min mem avail | VMs vCPU s | VMs VMM s | throttled (mean per VM) | hostd s | driver s | "
             "host busy s | unattributed s | top guest group |")
    L.append("|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for a in attributions:
        h = a.get("host") or {}
        g = a.get("guest") or {}
        top = (f"{g['top_group']} ({_frac_pct(g['top_group_share'])})" if g.get("top_group") else "n/a")
        L.append(f"| {a['trial_id']} | {', '.join(a.get('verdicts') or ['unknown'])} | "
                 f"{_pct1(h.get('cpu_util_mean_pct'))}/{_pct1(h.get('cpu_util_max_pct'))} | {_pct1(h.get('steal_mean_pct'))} | "
                 f"{_pct1(h.get('psi_cpu_some_pct'))}/{_pct1(h.get('psi_memory_some_pct'))}/{_pct1(h.get('psi_io_some_pct'))} | "
                 f"{_frac_pct(h.get('mem_available_min_fraction'))} | {_s(a.get('vcpu_s'))} | {_s(a.get('vmm_s'))} | "
                 f"{_frac_pct(a.get('vm_throttled_fraction_mean'))} | {_s(a.get('hostd_cpu_s'))} | {_s(a.get('driver_cpu_s'))} | "
                 f"{_s(h.get('busy_cpu_s'))} | {_s(a.get('unattributed_cpu_s'))} | {top} |")


def render_markdown(rep: dict) -> str:
    L = []
    inp = rep["inputs"]
    crit = inp.get("criteria") or {"step_p50_target_ms": inp.get("step_target_ms"),
                                   "step_p95_target_ms": inp.get("step_p95_ms"),
                                   "task_p95_target_ms": inp.get("task_target_ms")}
    L.append(f"# Report: run {rep['run_id']}")
    L.append("")
    L.append(f"Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(rep['generated_at']))}. "
             "Timings and counts are **measured**; cost per task is **modeled** from measured windows and the "
             "**assumed** public on-demand price; fixture-serving cost is an **estimate** at S3 request pricing.")
    L.append("")
    L.append("## Headline")
    L.append("")
    if not rep["headline"]:
        L.append("No ladder or confirmation trials in this run.")
    for b, h in rep["headline"].items():
        L.append(f"- **{b}**: highest N tested successfully = **{h['highest_n_tested_successfully']}** "
                 f"(measured; this is not maximum capacity). {h['note']}.")
        bd = h.get("boundary") or {}
        if bd:
            L.append(f"  - boundary: last pass n={bd.get('last_pass') if bd.get('last_pass') is not None else 'none'} "
                     f"({bd.get('last_pass_trials', 0)} trial(s)); first miss "
                     f"n={bd.get('first_miss') if bd.get('first_miss') is not None else 'none'} "
                     f"({bd.get('first_miss_trials', 0)} trial(s)); limit found: {'yes' if h.get('limit_found') else 'no'}")
    L.append("")
    L.append("## Criteria")
    L.append("")
    src = {"cli": "report command-line flags", "run.json": "the run's own criteria (run.json)",
           "none": "none given"}.get(inp.get("criteria_source"), inp.get("criteria_source") or "n/a")
    L.append(f"- targets: {_criteria_line(crit)}; source: {src}")
    L.append("- protocol, for every trial: complete, no error, all N sessions ready, N tasks dispatched, all N tasks ok, "
             "verify-clean clean")
    L.append("- targets are percentiles over one trial's ok tasks; a level passes only if it has at least one trial "
             "and every ladder/confirm trial at it passed; pooled percentiles below are for information")
    L.append("")
    L.append("## Boundary")
    L.append("")
    plan = rep.get("plan") if isinstance(rep.get("plan"), dict) else None
    for b, h in rep["headline"].items():
        bd = h.get("boundary") or {}
        L.append(f"- {b}: last pass {bd.get('last_pass')} ({bd.get('last_pass_trials', 0)} trials), first miss "
                 f"{bd.get('first_miss')} ({bd.get('first_miss_trials', 0)} trials), limit found "
                 f"{'yes' if h.get('limit_found') else 'no'} (report's evaluation)")
    if plan:
        pb = plan.get("boundary") or {}
        L.append(f"- the run's own plan: ladder {plan.get('ladder')}, repeats {plan.get('repeats')}, confirm repeats "
                 f"{plan.get('confirm_repeats')}, stop at first miss {plan.get('stop_at_first_miss')}; stop reason "
                 f"{plan.get('stop_reason')}; last pass {pb.get('last_pass')}, first miss {pb.get('first_miss')}")
        for lr in plan.get("levels_run") or []:
            L.append(f"  - n={lr.get('level')}: {'pass' if lr.get('passed') else 'MISS'} "
                     f"(ladder {'pass' if lr.get('ladder_passed') else 'miss'}; {lr.get('ladder_trials')} ladder, "
                     f"{lr.get('confirm_trials')} confirmation trial(s))")
        for c in plan.get("confirmations") or []:
            L.append(f"  - confirmations at n={c.get('level')}: {len(c.get('trials') or [])} trial(s), "
                     f"{'all passed' if c.get('passed') else 'at least one missed'}")
        if plan.get("levels_not_run"):
            L.append(f"  - not run: {plan['levels_not_run']}")
    elif not rep["headline"]:
        L.append("- no levels")
    L.append("")
    L.append("## Inputs")
    L.append("")
    L.append(f"- instance: {inp['instance'] or 'n/a'}"
             + (f" ({inp['instance_source']})" if inp.get("instance_source") else "")
             + f"; price per hour: {_usd(inp['price_per_hour_usd']) if inp['price_per_hour_usd'] is not None else 'n/a'} (assumed, public on-demand)")
    L.append(f"- targets: step p50 <= {_ms(inp['step_target_ms'])} ms, step p95 <= {_ms(inp['step_p95_ms'])} ms, "
             f"task p95 <= {_ms(inp['task_target_ms'])} ms")
    L.append(f"- S3 GET price per 1,000 requests: {_usd(inp['s3_get_price_per_1000_usd'])} (assumed)")
    hi = inp.get("host_info") or {}
    if hi:
        L.append(f"- host (from GET /host/info): {hi.get('cpu_model') or 'cpu n/a'}, {hi.get('cpu_count')} CPUs, "
                 f"threads per core {hi.get('threads_per_core')}, mem_total {hi.get('mem_total')}, kernel "
                 f"{hi.get('kernel_release')}, virtualized {hi.get('virtualized')}, kvm {hi.get('kvm')}")
    hp = rep["host_provisioning"]
    L.append(f"- host provisioning time: {_s(hp['seconds'])} s ({hp['label']}; {hp['note']})")
    L.append("")
    L.append("## Levels")
    L.append("")
    if not rep["levels"]:
        L.append("No levels.")
    for lv in rep["levels"]:
        L.append(f"### {lv['backend']} n={lv['level_n']} ({lv['repeats']} trial(s): {', '.join(lv['trials'])})")
        L.append("")
        L.append(f"- passed: **{'yes' if lv['passed'] else 'no'}** ({lv['trials_passed']}/{lv['repeats']} trials passed "
                 f"their criteria; protocol {'ok' if lv['protocol_passed'] else 'failed'} in "
                 f"{'every' if lv['protocol_passed'] else 'not every'} trial; pooled targets "
                 f"{'met' if lv['targets']['met'] else 'not met'}, information only); statuses: {', '.join(lv['statuses'])}")
        for ev in lv["trial_evaluations"]:
            if not ev["passed"]:
                L.append(f"  - {ev['trial_id']} ({ev['kind']}) missed: {'; '.join(ev['reasons'])}")
        L.append(f"- sessions ready: {lv['sessions_ready']}/{lv['sessions_requested']}; outcomes: {json.dumps(lv['sessions_by_outcome'])}")
        L.append(f"- tasks ok: {lv['tasks_ok']}/{lv['tasks_dispatched']}; failures by category: "
                 + (", ".join(f"{c} {v['count']} ({v['rate']*100:.1f}%)" for c, v in lv['failure_rate_by_category'].items()) or "none"))
        L.append(f"- task_ms (measured, guest clock, ok tasks only): p50 {_ms(lv['task_ms']['p50'])} ms, p95 {_ms(lv['task_ms']['p95'])} ms; "
                 f"wall_ms (measured, driver, ok tasks only): p50 {_ms(lv['wall_ms']['p50'])} ms, p95 {_ms(lv['wall_ms']['p95'])} ms")
        oh = lv["harness_overhead_ms"]
        L.append(f"- harness overhead (wall_ms - task_ms, measured, ok tasks only): mean {_ms(oh['mean'])} ms, p50 {_ms(oh['p50'])} ms, p95 {_ms(oh['p95'])} ms")
        fe = lv["failed_task_elapsed_ms"]
        if fe["n"]:
            L.append(f"- failed tasks, elapsed to failure (measured, guest clock; not a task duration): n {fe['n']}, "
                     f"p50 {_ms(fe['p50'])} ms, p95 {_ms(fe['p95'])} ms")
        L.append(f"- startup_ms (measured, hostd): p50 {_ms(lv['startup_ms']['p50'])} ms, p95 {_ms(lv['startup_ms']['p95'])} ms; "
                 f"cleanup_ms: p50 {_ms(lv['cleanup_ms']['p50'])} ms, p95 {_ms(lv['cleanup_ms']['p95'])} ms")
        L.append("")
        L.append("| step | n | pooled p50 ms | pooled p95 ms | pooled vs p50 target | pooled vs p95 target |")
        L.append("|---|---:|---:|---:|---|---|")
        for name, s in lv["steps"].items():
            t50 = "n/a" if inp["step_target_ms"] is None else ("met" if s["p50"] is not None and s["p50"] <= inp["step_target_ms"] else "MISSED")
            t95 = "n/a" if inp["step_p95_ms"] is None else ("met" if s["p95"] is not None and s["p95"] <= inp["step_p95_ms"] else "MISSED")
            L.append(f"| {name} | {s['n']} | {_ms(s['p50'])} | {_ms(s['p95'])} | {t50} | {t95} |")
        L.append("")
        c = lv["cost_usd_per_task"]
        L.append(f"- cost per task, execution-only (modeled): {_usd(c['execution_only'])}; observed, incl. setup and cleanup (modeled): {_usd(c['observed'])}")
        L.append(f"- fixture-serving cost per task (estimate at S3 request pricing; nginx served the fixture): {_usd(c['fixture_serving_estimate'])}; "
                 f"mean requests {_s(lv['mean_request_count'])}, mean bytes {_s(lv['mean_bytes_received'])}")
        bf = lv["bytes_flag"]
        if bf.get("checked"):
            L.append(f"- expected bytes per task {bf['expected_bytes']:.0f}: {'within' if bf['bytes_within_tolerance'] else 'OUTSIDE'} tolerance "
                     f"({bf['bytes_tolerance']*100:.0f}%); report-time flag only")
            if bf.get("expected_requests") is not None:
                L.append(f"- expected requests per task {bf['expected_requests']:.0f}: "
                         f"{'within' if bf['requests_within_tolerance'] else 'OUTSIDE'} tolerance "
                         f"({bf['requests_tolerance']*100:.0f}%); report-time flag only")
        L.append("")
        L.append(f"Attribution over each trial's task window (numbers measured; verdicts rule-based, from measured "
                 f"signals): {', '.join(lv['verdicts'])}")
        L.append("")
        _attribution_table(L, lv["attribution"])
        L.append("")
    L.append("## Trials")
    L.append("")
    L.append("| trial | kind | backend | n | repeat | fault | status | passed | sessions ready | tasks ok | clean | exec window s | observed window s | $/task exec | $/task observed |")
    L.append("|---|---|---|---:|---:|---|---|---|---|---|---|---:|---:|---:|---:|")
    for t in rep["trials"]:
        w, c = t["windows_s"], t["cost_usd_per_task"]
        passed = "yes" if t["evaluation"]["passed"] else "no"
        L.append(f"| {t['trial_id']} | {t['kind']} | {t['backend']} | {t['level_n']} | {t['repeat']} | {t['fault'] or ''} | {t['status']} | "
                 f"{passed} | {t['sessions_ready']}/{t['sessions_requested']} | {t['tasks_ok']}/{t['tasks_dispatched']} | "
                 f"{t['verify_clean']} | {_s(w['execution_s'])} | {_s(w['observed_s'])} | {_usd(c['execution_only'])} | {_usd(c['observed'])} |")
    L.append("")
    if rep.get("excluded_trials"):
        L.append("Warmup, illustration and fault trials are listed above and excluded from levels and the headline: "
                 + ", ".join(f"{t['trial_id']} ({t['kind']})" for t in rep["excluded_trials"]) + ".")
        L.append("")
    elif rep["fault_trials"]:
        L.append("Fault trials are listed above and excluded from levels and the headline.")
        L.append("")
    L.append("## Definitions")
    L.append("")
    L.append("- execution-only $/task = price/h x (last task return - barrier release) / tasks ok")
    L.append("- observed $/task = price/h x (verify-clean pass - first create) / tasks ok")
    L.append("- host provisioning time is on its own line and in neither window")
    L.append("- task_ms, wall_ms and harness overhead percentiles are over ok tasks only; a failed task's "
             "task_ms is its elapsed-to-failure and is reported separately; counts and failure rates cover every task")
    L.append("- a trial passes only if all N sessions reached ready, all N tasks are ok, verify-clean passed, and "
             "every provided target is met by that trial's own percentiles; a level passes only if every "
             "ladder/confirm trial at it passed; the headline is the highest N whose level passed with every lower "
             "tested level passing; a degraded trial is reported at its actual concurrency and never counts toward it")
    L.append("- attribution window = barrier release to last task return; PSI and cgroup pressure are the share of "
             "that window with some task stalled; throttled = cgroup throttled time over the window; host busy s = "
             "mean cpu_util x cpu_count x window; unattributed = host busy - VMs (vCPU + VMM) - hostd - driver")
    L.append("")
    return "\n".join(L)


def run_report(run: str, **kw) -> tuple[dict, str]:
    rundir = RunDir(Path(run))
    rep = build_report(rundir, **kw)
    md = render_markdown(rep)
    write_json_atomic(rundir.report_json, rep)
    rundir.report_md.write_text(md, encoding="utf-8")
    return rep, md
