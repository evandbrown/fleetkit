"""``driver report``: per density p50/p95 per step and per task, failure rates by category, targets,
harness overhead, the highest density tested successfully, and cost per task (design section 11,
"Report").

Every number is labeled measured, modeled or assumed. The run's result is the highest density that
passed with every lower density passing too: "tested successfully", never a maximum. Hosts are
compared by that density per host vCPU and by cost per 1,000 tasks (D52).

Criteria come from the CLI flags when any is given, else from run.json ``criteria`` (so ``report
--run`` alone reproduces the run's own verdicts). Each trial is evaluated on its own
(driver/criteria.py); a density passes iff every ladder and boundary trial at it passed. Pooled
percentiles across a density's trials are reported for information only. Warm-up, illustration and
fault trials are listed apart. Each trial carries an attribution over its task window
(driver/attribution.py). A run directory from before the glossary is read through driver/legacy.py
and its report is written elsewhere (``--out``): old evidence is never rewritten.
"""
from __future__ import annotations

import collections
import json
import time
from pathlib import Path

from . import schemas
from .attribution import attribute_trial, load_guest_sums, load_host_series, summarize_verdicts
from .criteria import density_passed, evaluate_trial, make_criteria, normalize
from .outputs import LegacyRunDirError, RunDir, read_json, write_json_atomic
from .stats import mean, percentile, summary, to_float, to_int

S3_GET_PRICE_PER_1000_DEFAULT = 0.0004  # USD per 1,000 GET requests, S3 Standard, public on-demand price

MEASURED, MODELED, ASSUMED, ESTIMATE, DERIVED = "measured", "modeled", "assumed", "estimate", "derived"


def resolve_criteria(step_target_ms, step_p95_ms, task_target_ms, run_json: dict) -> tuple[dict, str]:
    """-> (criteria, source): the CLI's when any flag is given, else run.json's, else none."""
    if any(v is not None for v in (step_target_ms, step_p95_ms, task_target_ms)):
        return make_criteria(step_target_ms, step_p95_ms, task_target_ms), "cli"
    rc = run_json.get("criteria")
    if isinstance(rc, dict):
        return normalize(rc), "run.json"
    return make_criteria(), "none"


def trial_kind(t: dict) -> str:
    """trial.json ``trial_kind``; a trial without one is a fault trial if it injected a fault, else ladder."""
    k = t.get("trial_kind")
    if k in schemas.TRIAL_KINDS:
        return k
    return "fault" if t.get("fault") else "ladder"


def build_report(rundir: RunDir, price_per_hour: float | None, instance: str | None,
                 step_target_ms: float | None, step_p95_ms: float | None, task_target_ms: float | None,
                 s3_get_price_per_1000: float = S3_GET_PRICE_PER_1000_DEFAULT,
                 fixture_manifest: str | None = None, bytes_tolerance: float = 0.25) -> dict:
    trials = rundir.list_trials()
    tasks = rundir.read_rows("tasks")
    steps = rundir.read_rows("steps")
    microvms = rundir.read_rows("microvms")
    manifest = read_json(rundir.manifest_json, {}) or {}
    run_json = rundir.read_run_json()
    expected = _expected_from_manifest(fixture_manifest)
    criteria, criteria_source = resolve_criteria(step_target_ms, step_p95_ms, task_target_ms, run_json)
    observed = run_json.get("observed") if isinstance(run_json.get("observed"), dict) else {}
    host_info = observed.get("host_info") if isinstance(observed.get("host_info"), dict) else {}
    ec2 = host_info.get("ec2") if isinstance(host_info.get("ec2"), dict) else {}
    instance_source = "operator input" if instance else None
    if not instance and ec2.get("instance_type"):
        instance, instance_source = ec2["instance_type"], "host_info (IMDS)"
    host_series = load_host_series(rundir)
    guest_sums = load_guest_sums(rundir)

    tasks_by_trial = collections.defaultdict(list)
    for r in tasks:
        tasks_by_trial[r["trial_id"]].append(r)
    steps_by_trial = collections.defaultdict(list)
    for r in steps:
        steps_by_trial[r["trial_id"]].append(r)
    microvms_by_trial = collections.defaultdict(list)
    for r in microvms:
        microvms_by_trial[r["trial_id"]].append(r)

    by_density: dict[tuple[str, int], dict] = {}
    fault_trials = []
    excluded_trials = []
    trial_rows = []
    for t in trials:
        tid = t["trial_id"]
        tr = _trial_summary(t, tasks_by_trial[tid], steps_by_trial[tid], microvms_by_trial[tid],
                            price_per_hour, s3_get_price_per_1000, expected, bytes_tolerance)
        tr["trial_kind"] = trial_kind(t)
        tr["evaluation"] = evaluate_trial(t, tasks_by_trial[tid], steps_by_trial[tid], criteria)
        tr["passed"] = tr["evaluation"]["passed"]
        tr["attribution"] = attribute_trial(t, host_series, guest_sums.get(tid),
                                            cpu_count=host_info.get("cpu_count"), mem_total=host_info.get("mem_total"))
        tr["pre_trial"] = t.get("pre_trial")
        trial_rows.append(tr)
        if t.get("fault") or tr["trial_kind"] == "fault":
            fault_trials.append(tr)
            continue
        if tr["trial_kind"] not in schemas.COUNTED_TRIAL_KINDS:
            excluded_trials.append(tr)
            continue
        key = (t["backend"], int(t["density"]))
        by_density.setdefault(key, {"backend": key[0], "density": key[1], "trials": [], "tasks": [], "steps": [],
                                    "microvms": []})
        by_density[key]["trials"].append(tr)
        by_density[key]["tasks"].extend(tasks_by_trial[tid])
        by_density[key]["steps"].extend(steps_by_trial[tid])
        by_density[key]["microvms"].extend(microvms_by_trial[tid])

    density_rows = []
    for key in sorted(by_density):
        density_rows.append(_density_summary(by_density[key], criteria["step_p50_target_ms"],
                                             criteria["step_p95_target_ms"], criteria["task_p95_target_ms"],
                                             price_per_hour, s3_get_price_per_1000, expected, bytes_tolerance))

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
            "release_after_ready_s": release_after_ready(trials),
        },
        "observed": {"host_info": host_info or None},
        "read_from_older_schema": rundir.legacy,
        "labels": {
            "price_per_hour_usd": ASSUMED + " (public on-demand price, operator input)",
            "s3_get_price_per_1000_usd": ASSUMED + " (public S3 Standard request price)",
            "timings": MEASURED, "counts": MEASURED, "cost_per_task": MODELED + " from measured windows and assumed prices",
            "cost_per_1000_tasks": MODELED + " from measured windows and assumed prices",
            "density_per_host_vcpu": DERIVED + " (highest density tested successfully / host vCPUs from GET /host/info)",
            "fixture_cost": ESTIMATE + " (nginx served the fixture; modeled at S3 request pricing)",
            "attribution": MEASURED + " numbers; verdicts rule-based, from measured signals",
        },
        "host_provisioning": _host_provisioning(manifest, run_json),
        "headline": _headline(density_rows, host_info.get("cpu_count")),
        "plan": run_json.get("plan"),
        "densities": density_rows,
        "trials": trial_rows,
        "fault_trials": fault_trials,
        "excluded_trials": [{"trial_id": t["trial_id"], "trial_kind": t["trial_kind"], "density": t["density"],
                             "status": t["status"]} for t in excluded_trials + fault_trials],
    }
    return report


def _headline(density_rows: list[dict], host_vcpus=None) -> dict:
    """Per backend: the highest density that passed with every lower tested density passing (the run's
    result, "tested successfully"), the boundary (last pass, first miss, trials at each), and the D52
    comparison measures at that density: density per host vCPU and cost per 1,000 tasks."""
    by_backend: dict[str, list[dict]] = collections.defaultdict(list)
    for dr in density_rows:
        by_backend[dr["backend"]].append(dr)
    vcpus = to_float(host_vcpus)
    out = {}
    for b, rows in by_backend.items():
        last_pass = first_miss = None
        for dr in sorted(rows, key=lambda r: r["density"]):
            if dr["passed"]:
                if first_miss is None:
                    last_pass = dr
            elif first_miss is None:
                first_miss = dr
        best = last_pass["density"] if last_pass else 0
        cost = (last_pass or {}).get("cost_usd_per_task") or {}
        out[b] = {
            "highest_density_tested_successfully": best,
            "density_per_host_vcpu": (best / vcpus) if vcpus else None,
            "host_vcpus": int(vcpus) if vcpus else None,
            "cost_usd_per_1000_tasks": {
                "execution_only": cost["execution_only"] * 1000 if cost.get("execution_only") is not None else None,
                "observed": cost["observed"] * 1000 if cost.get("observed") is not None else None,
            },
            "note": "docker is the development backend; real numbers come only from firecracker on AWS"
            if b == "docker" else f"{b} measurement backend",
            "boundary": {
                "last_pass": last_pass["density"] if last_pass else None,
                "last_pass_trials": last_pass["trial_count"] if last_pass else 0,
                "first_miss": first_miss["density"] if first_miss else None,
                "first_miss_trials": first_miss["trial_count"] if first_miss else 0,
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
    # The wait after ready (a warm start), between all ready and the barrier: in the observed window, not
    # the execution one. None when the trial didn't wait (or ran before the wait existed).
    wait_w = None
    if ts.get("release_wait_end") is not None and ts.get("release_wait_start") is not None:
        wait_w = ts["release_wait_end"] - ts["release_wait_start"]
    return {"execution_s": exec_w, "observed_s": obs_w, "release_wait_s": wait_w}


def release_after_ready(trials: list[dict]) -> float | None:
    """The wait after ready the run's trials asked for (0: none, as in every trial before it existed);
    None when its trials differ."""
    vals = {float(t.get("release_after_ready_s") or 0) for t in trials}
    if len(vals) > 1:
        return None
    return vals.pop() if vals else 0.0


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


def _trial_summary(t: dict, tasks, steps, microvms, price, s3_price, expected, tol) -> dict:
    counts = t.get("counts") or {}
    win = _windows(t)
    tasks_ok = counts.get("tasks_ok", 0)
    timings = _timings(tasks)
    mean_bytes = mean([r["bytes_received"] for r in tasks if r.get("bytes_received")])
    mean_reqs = mean([r["request_count"] for r in tasks if r.get("request_count")])
    return {
        "trial_id": t["trial_id"], "sequence": t.get("sequence"), "backend": t["backend"],
        "density": int(t["density"]), "trial_number": t.get("trial_number"), "fault": t.get("fault"),
        "status": t.get("status"), "complete": t.get("complete"), "error": t.get("error"),
        "microvms_requested": counts.get("microvms_requested"), "microvms_ready": counts.get("microvms_ready"),
        "microvms_by_outcome": counts.get("microvms_by_outcome", {}),
        "tasks_dispatched": counts.get("tasks_dispatched"), "tasks_ok": tasks_ok,
        "tasks_by_failure_category": counts.get("tasks_by_failure_category", {}),
        "verify_clean": (t.get("verify_clean") or {}).get("clean"),
        "windows_s": win,
        **timings,
        "steps": _step_percentiles(steps),
        "startup_ms": summary([r["startup_ms"] for r in microvms if r.get("startup_ms")]),
        "cleanup_ms": summary([r["cleanup_ms"] for r in microvms if r.get("cleanup_ms")]),
        "host_setup_ms": summary([(to_float(r["process_started_ts"]) - to_float(r["created_ts"])) * 1000.0
                                  for r in microvms if r.get("process_started_ts") and r.get("created_ts")]),
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


def _density_summary(dv: dict, step_target, step_p95, task_target, price, s3_price, expected, tol) -> dict:
    trials, tasks, steps, microvms = dv["trials"], dv["tasks"], dv["steps"], dv["microvms"]
    n = dv["density"]
    timings = _timings(tasks)
    task_ms = [to_float(r["task_ms"]) for r in tasks if r.get("ok") == "true" and r.get("task_ms")]
    step_pct = _step_percentiles(steps)
    tasks_ok = sum(1 for r in tasks if r.get("ok") == "true")
    by_cat = collections.Counter(r.get("failure_category", "") for r in tasks)
    by_outcome = collections.Counter(r.get("outcome", "") for r in microvms)
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

    # pooled across the density's trials: information only; the verdict is per trial
    targets["pooled"] = True
    evaluations = [t["evaluation"] for t in trials]
    protocol_passed = bool(trials) and all(e["protocol_ok"] for e in evaluations)
    passed = density_passed(evaluations)
    exec_s = [t["windows_s"]["execution_s"] for t in trials if t["windows_s"]["execution_s"] is not None]
    obs_s = [t["windows_s"]["observed_s"] for t in trials if t["windows_s"]["observed_s"] is not None]
    ok_for_cost = sum(t["tasks_ok"] for t in trials)
    mean_bytes = mean([r["bytes_received"] for r in tasks if r.get("bytes_received")])
    mean_reqs = mean([r["request_count"] for r in tasks if r.get("request_count")])
    return {
        "backend": dv["backend"], "density": n, "trials": [t["trial_id"] for t in trials],
        "trial_count": len(trials), "statuses": [t["status"] for t in trials],
        "trial_kinds": dict(collections.Counter(t["trial_kind"] for t in trials)),
        "protocol_passed": protocol_passed, "targets": targets, "passed": passed,
        "trials_passed": sum(1 for e in evaluations if e["passed"]),
        "trial_evaluations": [{"trial_id": t["trial_id"], "trial_kind": t["trial_kind"], **t["evaluation"]}
                              for t in trials],
        "attribution": [{"trial_id": t["trial_id"], **t["attribution"]} for t in trials],
        "verdicts": summarize_verdicts([t["attribution"] for t in trials]),
        "microvms_requested": n * len(trials), "microvms_ready": sum(t["microvms_ready"] or 0 for t in trials),
        "microvms_by_outcome": dict(by_outcome),
        "tasks_dispatched": dispatched, "tasks_ok": tasks_ok,
        "failure_rate_by_category": {c: {"count": k, "rate": (k / dispatched) if dispatched else None}
                                     for c, k in sorted(by_cat.items()) if c != "ok"},
        **timings,
        "steps": step_pct,
        "startup_ms": summary([r["startup_ms"] for r in microvms if r.get("startup_ms")]),
        "cleanup_ms": summary([r["cleanup_ms"] for r in microvms if r.get("cleanup_ms")]),
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
             "min mem avail | microVMs vCPU s | microVMs hypervisor s | throttled (mean per microVM) | hostd s | "
             "driver s | host busy s | unattributed s | top guest group |")
    L.append("|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for a in attributions:
        h = a.get("host") or {}
        g = a.get("guest") or {}
        top = (f"{g['top_group']} ({_frac_pct(g['top_group_share'])})" if g.get("top_group") else "n/a")
        L.append(f"| {a['trial_id']} | {', '.join(a.get('verdicts') or ['unknown'])} | "
                 f"{_pct1(h.get('cpu_util_mean_pct'))}/{_pct1(h.get('cpu_util_max_pct'))} | {_pct1(h.get('steal_mean_pct'))} | "
                 f"{_pct1(h.get('psi_cpu_some_pct'))}/{_pct1(h.get('psi_memory_some_pct'))}/{_pct1(h.get('psi_io_some_pct'))} | "
                 f"{_frac_pct(h.get('mem_available_min_fraction'))} | {_s(a.get('vcpu_s'))} | {_s(a.get('hypervisor_s'))} | "
                 f"{_frac_pct(a.get('vm_throttled_fraction_mean'))} | {_s(a.get('hostd_cpu_s'))} | {_s(a.get('driver_cpu_s'))} | "
                 f"{_s(h.get('busy_cpu_s'))} | {_s(a.get('unattributed_cpu_s'))} | {top} |")


def _num_or_none(v, fmt="{}"):
    return "none" if v is None else fmt.format(v)


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
    if rep.get("read_from_older_schema"):
        L.append("")
        L.append("This run directory was written before the glossary; its names were translated on read "
                 "(driver/legacy.py) and its trials renumbered within their density.")
    wait = inp.get("release_after_ready_s", 0.0)
    if wait is None or wait > 0:
        L.append("")
        L.append((f"Warm start: every trial released its tasks {wait:g} s after its last microVM was ready"
                  if wait is not None else "Warm start: this run's trials waited different times after ready")
                 + " (procedure.release_after_ready_s). Task times start at the release, so no task's time "
                   "includes the wait; the execution-only cost window leaves it out and the observed one takes it in.")
    L.append("")
    L.append("## Headline")
    L.append("")
    if not rep["headline"]:
        L.append("No ladder or boundary trials in this run.")
    for b, h in rep["headline"].items():
        L.append(f"- **{b}**: highest density tested successfully = **{h['highest_density_tested_successfully']}** "
                 f"(measured; every lower density passed too). {h['note']}.")
        if h.get("density_per_host_vcpu") is not None:
            c = h.get("cost_usd_per_1000_tasks") or {}
            L.append(f"  - density per host vCPU: {h['density_per_host_vcpu']:.3f} ({h['host_vcpus']} vCPUs; derived); "
                     f"cost per 1,000 tasks at that density: execution-only {_usd(c.get('execution_only'))}, "
                     f"observed {_usd(c.get('observed'))} (modeled)")
        bd = h.get("boundary") or {}
        if bd:
            L.append(f"  - boundary: last pass density {_num_or_none(bd.get('last_pass'))} "
                     f"({bd.get('last_pass_trials', 0)} trial(s)); first miss density "
                     f"{_num_or_none(bd.get('first_miss'))} ({bd.get('first_miss_trials', 0)} trial(s)); "
                     f"limit found: {'yes' if h.get('limit_found') else 'no'}")
    L.append("")
    L.append("## Criteria")
    L.append("")
    src = {"cli": "report command-line flags", "run.json": "the run's own criteria (run.json)",
           "none": "none given"}.get(inp.get("criteria_source"), inp.get("criteria_source") or "n/a")
    L.append(f"- targets: {_criteria_line(crit)}; source: {src}")
    L.append("- protocol, for every trial: complete, no error, all N microVMs ready, N tasks dispatched, all N tasks "
             "ok, verify-clean clean")
    L.append("- targets are percentiles over one trial's ok tasks; a density passes only if it has at least one trial "
             "and every ladder and boundary trial at it passed; pooled percentiles below are for information")
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
        L.append(f"- the run's own plan: densities {plan.get('densities')}, trials per density "
                 f"{plan.get('trials_per_density')}, boundary trials {plan.get('boundary_trials')}, stop at first miss "
                 f"{plan.get('stop_at_first_miss')}; stop reason {plan.get('stop_reason')}; last pass "
                 f"{pb.get('last_pass')}, first miss {pb.get('first_miss')}")
        for dr in plan.get("densities_run") or []:
            L.append(f"  - density {dr.get('density')}: {'pass' if dr.get('passed') else 'MISS'} "
                     f"(ladder {'pass' if dr.get('ladder_passed') else 'miss'}; {dr.get('ladder_trial_count')} ladder, "
                     f"{dr.get('boundary_trial_count')} boundary trial(s))")
        for c in plan.get("boundary_checks") or []:
            L.append(f"  - boundary trials at density {c.get('density')}: {len(c.get('trials') or [])} trial(s), "
                     f"{'all passed' if c.get('passed') else 'at least one missed'}")
        if plan.get("densities_not_run"):
            L.append(f"  - not run: {plan['densities_not_run']}")
    elif not rep["headline"]:
        L.append("- no densities")
    L.append("")
    L.append("## Inputs")
    L.append("")
    L.append(f"- instance: {inp['instance'] or 'n/a'}"
             + (f" ({inp['instance_source']})" if inp.get("instance_source") else "")
             + f"; price per hour: {_usd(inp['price_per_hour_usd']) if inp['price_per_hour_usd'] is not None else 'n/a'} (assumed, public on-demand)")
    L.append(f"- targets: step p50 <= {_ms(inp['step_target_ms'])} ms, step p95 <= {_ms(inp['step_p95_ms'])} ms, "
             f"task p95 <= {_ms(inp['task_target_ms'])} ms")
    L.append(f"- S3 GET price per 1,000 requests: {_usd(inp['s3_get_price_per_1000_usd'])} (assumed)")
    hi = (rep.get("observed") or {}).get("host_info") or {}
    if hi:
        L.append(f"- host (measured, GET /host/info): {hi.get('cpu_model') or 'cpu n/a'}, {hi.get('cpu_count')} CPUs, "
                 f"threads per core {hi.get('threads_per_core')}, mem_total {hi.get('mem_total')}, kernel "
                 f"{hi.get('kernel_release')}, virtualized {hi.get('virtualized')}, kvm {hi.get('kvm')}")
    hp = rep["host_provisioning"]
    L.append(f"- host provisioning time: {_s(hp['seconds'])} s ({hp['label']}; {hp['note']})")
    L.append("")
    L.append("## Densities")
    L.append("")
    if not rep["densities"]:
        L.append("No densities.")
    for dv in rep["densities"]:
        L.append(f"### {dv['backend']} density {dv['density']} ({dv['trial_count']} trial(s): {', '.join(dv['trials'])})")
        L.append("")
        L.append(f"- passed: **{'yes' if dv['passed'] else 'no'}** ({dv['trials_passed']}/{dv['trial_count']} trials "
                 f"passed their criteria; protocol {'ok' if dv['protocol_passed'] else 'failed'} in "
                 f"{'every' if dv['protocol_passed'] else 'not every'} trial; pooled targets "
                 f"{'met' if dv['targets']['met'] else 'not met'}, information only); statuses: {', '.join(dv['statuses'])}")
        for ev in dv["trial_evaluations"]:
            if not ev["passed"]:
                L.append(f"  - {ev['trial_id']} ({ev['trial_kind']}) missed: {'; '.join(ev['reasons'])}")
        L.append(f"- microVMs ready: {dv['microvms_ready']}/{dv['microvms_requested']}; outcomes: "
                 f"{json.dumps(dv['microvms_by_outcome'])}")
        L.append(f"- tasks ok: {dv['tasks_ok']}/{dv['tasks_dispatched']}; failures by category: "
                 + (", ".join(f"{c} {v['count']} ({v['rate']*100:.1f}%)" for c, v in dv['failure_rate_by_category'].items()) or "none"))
        L.append(f"- task_ms (measured, guest clock, ok tasks only): p50 {_ms(dv['task_ms']['p50'])} ms, p95 {_ms(dv['task_ms']['p95'])} ms; "
                 f"wall_ms (measured, driver, ok tasks only): p50 {_ms(dv['wall_ms']['p50'])} ms, p95 {_ms(dv['wall_ms']['p95'])} ms")
        oh = dv["harness_overhead_ms"]
        L.append(f"- harness overhead (wall_ms - task_ms, measured, ok tasks only): mean {_ms(oh['mean'])} ms, p50 {_ms(oh['p50'])} ms, p95 {_ms(oh['p95'])} ms")
        fe = dv["failed_task_elapsed_ms"]
        if fe["count"]:
            L.append(f"- failed tasks, elapsed to failure (measured, guest clock; not a task duration): {fe['count']} "
                     f"sample(s), p50 {_ms(fe['p50'])} ms, p95 {_ms(fe['p95'])} ms")
        L.append(f"- startup_ms (measured, hostd): p50 {_ms(dv['startup_ms']['p50'])} ms, p95 {_ms(dv['startup_ms']['p95'])} ms; "
                 f"cleanup_ms: p50 {_ms(dv['cleanup_ms']['p50'])} ms, p95 {_ms(dv['cleanup_ms']['p95'])} ms")
        L.append("")
        L.append("| step | samples | pooled p50 ms | pooled p95 ms | pooled vs p50 target | pooled vs p95 target |")
        L.append("|---|---:|---:|---:|---|---|")
        for name, s in dv["steps"].items():
            t50 = "n/a" if inp["step_target_ms"] is None else ("met" if s["p50"] is not None and s["p50"] <= inp["step_target_ms"] else "MISSED")
            t95 = "n/a" if inp["step_p95_ms"] is None else ("met" if s["p95"] is not None and s["p95"] <= inp["step_p95_ms"] else "MISSED")
            L.append(f"| {name} | {s['count']} | {_ms(s['p50'])} | {_ms(s['p95'])} | {t50} | {t95} |")
        L.append("")
        c = dv["cost_usd_per_task"]
        L.append(f"- cost per task, execution-only (modeled): {_usd(c['execution_only'])}; observed, incl. setup and cleanup (modeled): {_usd(c['observed'])}")
        L.append(f"- fixture-serving cost per task (estimate at S3 request pricing; nginx served the fixture): {_usd(c['fixture_serving_estimate'])}; "
                 f"mean requests {_s(dv['mean_request_count'])}, mean bytes {_s(dv['mean_bytes_received'])}")
        bf = dv["bytes_flag"]
        if bf.get("checked"):
            L.append(f"- expected bytes per task {bf['expected_bytes']:.0f}: {'within' if bf['bytes_within_tolerance'] else 'OUTSIDE'} tolerance "
                     f"({bf['bytes_tolerance']*100:.0f}%); report-time flag only")
            if bf.get("expected_requests") is not None:
                L.append(f"- expected requests per task {bf['expected_requests']:.0f}: "
                         f"{'within' if bf['requests_within_tolerance'] else 'OUTSIDE'} tolerance "
                         f"({bf['requests_tolerance']*100:.0f}%); report-time flag only")
        L.append("")
        L.append(f"Attribution over each trial's task window (numbers measured; verdicts rule-based, from measured "
                 f"signals): {', '.join(dv['verdicts'])}")
        L.append("")
        _attribution_table(L, dv["attribution"])
        L.append("")
    L.append("## Trials")
    L.append("")
    # the measured wait after ready gets a column only in a run that waited, so other reports stay as they were
    waited = any(t["windows_s"].get("release_wait_s") is not None for t in rep["trials"])
    L.append("| trial id | trial kind | backend | density | trial | fault | status | passed | microVMs ready | tasks ok | "
             "clean | " + ("wait after ready s | " if waited else "")
             + "exec window s | observed window s | $/task exec | $/task observed |")
    L.append("|---|---|---|---:|---:|---|---|---|---|---|---|" + ("---:|" if waited else "") + "---:|---:|---:|---:|")
    for t in rep["trials"]:
        w, c = t["windows_s"], t["cost_usd_per_task"]
        passed = "yes" if t["evaluation"]["passed"] else "no"
        number = "" if t.get("trial_number") is None else t["trial_number"]
        wait_cell = f"{_s(w.get('release_wait_s'))} | " if waited else ""
        L.append(f"| {t['trial_id']} | {t['trial_kind']} | {t['backend']} | {t['density']} | {number} | {t['fault'] or ''} | "
                 f"{t['status']} | {passed} | {t['microvms_ready']}/{t['microvms_requested']} | {t['tasks_ok']}/{t['tasks_dispatched']} | "
                 f"{t['verify_clean']} | {wait_cell}{_s(w['execution_s'])} | {_s(w['observed_s'])} | {_usd(c['execution_only'])} | {_usd(c['observed'])} |")
    L.append("")
    if rep.get("excluded_trials"):
        L.append("Warm-up, illustration and fault trials are listed above and excluded from densities and the headline: "
                 + ", ".join(f"{t['trial_id']} ({t['trial_kind']})" for t in rep["excluded_trials"]) + ".")
        L.append("")
    elif rep["fault_trials"]:
        L.append("Fault trials are listed above and excluded from densities and the headline.")
        L.append("")
    L.append("## Definitions")
    L.append("")
    L.append("- a trial starts N fresh microVMs at the same moment, runs one task in each and destroys them; N is its "
             "density; trials are numbered from 1 within their density (d8-t2 is trial 2 at density 8)")
    # where the wait after ready falls, said only in a run that waited, so other reports stay as they were
    L.append("- execution-only $/task = price/h x (last task return - barrier release) / tasks ok"
             + ("; the barrier opens after the wait after ready, so the wait is outside this window" if waited else ""))
    L.append("- observed $/task = price/h x (verify-clean pass - first create) / tasks ok"
             + ("; it includes startup, the wait after ready, and cleanup" if waited else ""))
    L.append("- host provisioning time is on its own line and in neither window")
    L.append("- task_ms, wall_ms and harness overhead percentiles are over ok tasks only; a failed task's "
             "task_ms is its elapsed-to-failure and is reported separately; counts and failure rates cover every task")
    L.append("- a trial passes only if all N microVMs reached ready, all N tasks are ok, verify-clean passed, and "
             "every provided target is met by that trial's own percentiles; a density passes only if every "
             "ladder and boundary trial at it passed; the headline is the highest density that passed with every "
             "lower tested density passing; a degraded trial never counts toward it")
    L.append("- attribution window = barrier release to last task return; PSI and cgroup pressure are the share of "
             "that window with some task stalled; throttled = cgroup throttled time over the window; host busy s = "
             "mean cpu_util x cpu_count x window; unattributed = host busy - microVMs (vCPU + hypervisor) - hostd - driver")
    L.append("")
    return "\n".join(L)


def run_report(run: str, out_dir: str | None = None, **kw) -> tuple[dict, str]:
    """Build the report for ``run`` and write report.json and report.md into ``out_dir`` (default: the
    run directory). A run directory from before the glossary is read, never written: pass ``out_dir``."""
    rundir = RunDir(Path(run))
    dest = Path(out_dir) if out_dir else rundir.root
    if rundir.legacy and dest.resolve() == rundir.root.resolve():
        raise LegacyRunDirError(f"{rundir.root} was written before the glossary and is never rewritten; "
                                "pass --out to write its report elsewhere")
    rep = build_report(rundir, **kw)
    md = render_markdown(rep)
    dest.mkdir(parents=True, exist_ok=True)
    write_json_atomic(dest / "report.json", rep)
    (dest / "report.md").write_text(md, encoding="utf-8")
    return rep, md
