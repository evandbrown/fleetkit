"""``driver report``: per level p50/p95 per step and per task, failure rates by category, targets,
harness overhead, the highest N that passed, and cost per task (design section 11, "Report").

Every number is labeled measured, modeled or assumed. The headline is the highest N actually tested
successfully; it is never called maximum capacity.
"""
from __future__ import annotations

import collections
import json
import time
from pathlib import Path

from . import schemas
from .outputs import RunDir, read_csv, read_json, write_json_atomic
from .stats import mean, percentile, summary, to_float, to_int

S3_GET_PRICE_PER_1000_DEFAULT = 0.0004  # USD per 1,000 GET requests, S3 Standard, public on-demand price

MEASURED, MODELED, ASSUMED, ESTIMATE = "measured", "modeled", "assumed", "estimate"


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
    trial_rows = []
    for t in trials:
        tid = t["trial_id"]
        tr = _trial_summary(t, tasks_by_trial[tid], steps_by_trial[tid], sessions_by_trial[tid],
                            price_per_hour, s3_get_price_per_1000, expected, bytes_tolerance)
        trial_rows.append(tr)
        if t.get("fault"):
            fault_trials.append(tr)
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
        level_rows.append(_level_summary(lv, step_target_ms, step_p95_ms, task_target_ms, price_per_hour,
                                         s3_get_price_per_1000, expected, bytes_tolerance))

    headline = {}
    for lr in level_rows:
        b = lr["backend"]
        if lr["passed"]:
            headline[b] = max(headline.get(b, 0), lr["level_n"])
        headline.setdefault(b, headline.get(b, 0))

    report = {
        "run_id": rundir.run_id,
        "generated_at": time.time(),
        "inputs": {
            "price_per_hour_usd": price_per_hour, "instance": instance,
            "step_target_ms": step_target_ms, "step_p95_ms": step_p95_ms, "task_target_ms": task_target_ms,
            "s3_get_price_per_1000_usd": s3_get_price_per_1000, "fixture_manifest": fixture_manifest,
            "bytes_tolerance": bytes_tolerance, "expected_per_task": expected,
        },
        "labels": {
            "price_per_hour_usd": ASSUMED + " (public on-demand price, operator input)",
            "s3_get_price_per_1000_usd": ASSUMED + " (public S3 Standard request price)",
            "timings": MEASURED, "counts": MEASURED, "cost_per_task": MODELED + " from measured windows and assumed prices",
            "fixture_cost": ESTIMATE + " (nginx served the fixture; modeled at S3 request pricing)",
        },
        "host_provisioning": _host_provisioning(manifest, run_json),
        "headline": {b: {"highest_n_tested_successfully": n,
                         "note": "docker is the development backend; real numbers come only from firecracker on AWS"
                         if b == "docker" else "firecracker measurement backend"}
                     for b, n in headline.items()},
        "levels": level_rows,
        "trials": trial_rows,
        "fault_trials": fault_trials,
    }
    return report


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

    protocol_passed = bool(trials) and all(t["level_passed"] for t in trials)
    passed = protocol_passed and targets["met"]
    exec_s = [t["windows_s"]["execution_s"] for t in trials if t["windows_s"]["execution_s"] is not None]
    obs_s = [t["windows_s"]["observed_s"] for t in trials if t["windows_s"]["observed_s"] is not None]
    ok_for_cost = sum(t["tasks_ok"] for t in trials)
    mean_bytes = mean([r["bytes_received"] for r in tasks if r.get("bytes_received")])
    mean_reqs = mean([r["request_count"] for r in tasks if r.get("request_count")])
    return {
        "backend": lv["backend"], "level_n": n, "trials": [t["trial_id"] for t in trials],
        "repeats": len(trials), "statuses": [t["status"] for t in trials],
        "protocol_passed": protocol_passed, "targets": targets, "passed": passed,
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


def render_markdown(rep: dict) -> str:
    L = []
    inp = rep["inputs"]
    L.append(f"# Report: run {rep['run_id']}")
    L.append("")
    L.append(f"Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(rep['generated_at']))}. "
             "Timings and counts are **measured**; cost per task is **modeled** from measured windows and the "
             "**assumed** public on-demand price; fixture-serving cost is an **estimate** at S3 request pricing.")
    L.append("")
    L.append("## Headline")
    L.append("")
    if not rep["headline"]:
        L.append("No non-fault trials in this run.")
    for b, h in rep["headline"].items():
        L.append(f"- **{b}**: highest N tested successfully = **{h['highest_n_tested_successfully']}** "
                 f"(measured; this is not maximum capacity). {h['note']}.")
    L.append("")
    L.append("## Inputs")
    L.append("")
    L.append(f"- instance: {inp['instance'] or 'n/a'}; price per hour: {_usd(inp['price_per_hour_usd']) if inp['price_per_hour_usd'] is not None else 'n/a'} (assumed, public on-demand)")
    L.append(f"- targets: step p50 <= {_ms(inp['step_target_ms'])} ms, step p95 <= {_ms(inp['step_p95_ms'])} ms, "
             f"task p95 <= {_ms(inp['task_target_ms'])} ms")
    L.append(f"- S3 GET price per 1,000 requests: {_usd(inp['s3_get_price_per_1000_usd'])} (assumed)")
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
        L.append(f"- passed: **{'yes' if lv['passed'] else 'no'}** (protocol {'ok' if lv['protocol_passed'] else 'failed'}, "
                 f"targets {'met' if lv['targets']['met'] else 'not met'}); statuses: {', '.join(lv['statuses'])}")
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
        L.append("| step | n | p50 ms | p95 ms | p50 target | p95 target |")
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
    L.append("## Trials")
    L.append("")
    L.append("| trial | backend | n | repeat | fault | status | sessions ready | tasks ok | clean | exec window s | observed window s | $/task exec | $/task observed |")
    L.append("|---|---|---:|---:|---|---|---|---|---|---:|---:|---:|---:|")
    for t in rep["trials"]:
        w, c = t["windows_s"], t["cost_usd_per_task"]
        L.append(f"| {t['trial_id']} | {t['backend']} | {t['level_n']} | {t['repeat']} | {t['fault'] or ''} | {t['status']} | "
                 f"{t['sessions_ready']}/{t['sessions_requested']} | {t['tasks_ok']}/{t['tasks_dispatched']} | "
                 f"{t['verify_clean']} | {_s(w['execution_s'])} | {_s(w['observed_s'])} | {_usd(c['execution_only'])} | {_usd(c['observed'])} |")
    L.append("")
    if rep["fault_trials"]:
        L.append("Fault trials are listed above and excluded from levels and the headline.")
        L.append("")
    L.append("## Definitions")
    L.append("")
    L.append("- execution-only $/task = price/h x (last task return - barrier release) / tasks ok")
    L.append("- observed $/task = price/h x (verify-clean pass - first create) / tasks ok")
    L.append("- host provisioning time is on its own line and in neither window")
    L.append("- task_ms, wall_ms and harness overhead percentiles are over ok tasks only; a failed task's "
             "task_ms is its elapsed-to-failure and is reported separately; counts and failure rates cover every task")
    L.append("- a level passes only if all N sessions reached ready, all N tasks are ok, verify-clean passed, and, "
             "once targets exist, every provided target is met; a degraded trial is reported at its actual "
             "concurrency and never counts toward the headline")
    L.append("")
    return "\n".join(L)


def run_report(run: str, **kw) -> tuple[dict, str]:
    rundir = RunDir(Path(run))
    rep = build_report(rundir, **kw)
    md = render_markdown(rep)
    write_json_atomic(rundir.report_json, rep)
    rundir.report_md.write_text(md, encoding="utf-8")
    return rep, md
