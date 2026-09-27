from __future__ import annotations

import csv
import json
from pathlib import Path

from driver import legacy, schemas
from driver.evidence import iter_spans
from driver.outputs import read_csv, read_json
from driver.trial import categorize
from tests.conftest import common, run_cli
from tests.stub_hostd import StubServer


def _header(path: Path) -> list[str]:
    with open(path, newline="") as fh:
        return next(csv.reader(fh))


def test_trial_densities_write_every_output(hostd, fixture_site, tmp_path):
    out = tmp_path / "results" / "run-1"
    rc = run_cli("trial", *common(hostd, fixture_site, out), "--densities", "2,3", "--trials-per-density", "1")
    assert rc == 0

    # exact schemas
    assert _header(out / "tasks.csv") == schemas.TASKS_COLUMNS
    assert _header(out / "steps.csv") == schemas.STEPS_COLUMNS
    assert _header(out / "microvms.csv") == schemas.MICROVMS_COLUMNS
    assert not (out / legacy.LEGACY_MICROVMS_CSV).exists()
    assert _header(out / "host_metrics.csv") == schemas.HOST_METRICS_COLUMNS

    tasks = read_csv(out / "tasks.csv")
    assert len(tasks) == 5 and all(t["ok"] == "true" and t["failure_category"] == "ok" for t in tasks)
    # trials are numbered from 1 within their density: d2-t1 is trial 1 at density 2
    assert {t["trial_id"] for t in tasks} == {"d2-t1", "d3-t1"}
    assert {(t["density"], t["trial_number"], t["trial_kind"]) for t in tasks} == {("2", "1", "ladder"), ("3", "1", "ladder")}
    assert all(t["run_id"] == "run-1" for t in tasks)
    assert all(t["task_id"].startswith(t["trial_id"] + "-slot") for t in tasks)
    # products assigned list[slot mod len]: 3 products, slots 0..2
    by_slot = {(t["trial_id"], t["slot"]): t["product_id"] for t in tasks}
    assert by_slot[("d3-t1", "0")] == "p-1001" and by_slot[("d3-t1", "2")] == "p-1003"
    for t in tasks:
        assert (out / t["screenshot_path"]).exists() and t["screenshot_path"] == f"screenshots/{t['task_id']}.jpg"
        assert float(t["wall_ms"]) >= float(t["task_ms"])
        assert t["trace_id"] and t["clock_offset_ns"] != "" and t["guest_mem_available"] and t["chromium_rss"]

    steps = read_csv(out / "steps.csv")
    assert len(steps) == 25
    assert [s["name"] for s in steps if s["task_id"] == tasks[0]["task_id"]] == list(schemas.STEP_NAMES)
    assert all(float(s["settle_ts"]) >= float(s["dispatch_ts"]) for s in steps)

    microvms = read_csv(out / "microvms.csv")
    assert len(microvms) == 5 and all(s["outcome"] == "completed" for s in microvms)
    assert all(s["startup_ms"] and s["cleanup_ms"] and s["destroyed_ts"] for s in microvms)
    assert all(s["vcpus"] == "2" and s["mem_mib"] == "2048" for s in microvms)
    assert {s["microvm_id"] for s in microvms} == {t["microvm_id"] for t in tasks}

    metrics = read_csv(out / "host_metrics.csv")
    assert metrics and {m["metric"] for m in metrics if m["subject"] == "host"} >= {"mem_total", "cpu_util", "psi_cpu_some_avg10"}
    assert not any(m["metric"] == "steal" for m in metrics)  # null skipped

    t = read_json(out / "trials" / "d2-t1" / "trial.json")
    assert t["status"] == "ok" and t["passed"] and t["protocol_ok"] and t["complete"] and t["phase"] == "done"
    assert (t["density"], t["trial_number"], t["sequence"], t["trial_kind"]) == (2, 1, 1, "ladder")
    assert read_json(out / "trials" / "d3-t1" / "trial.json")["sequence"] == 2
    old_root_keys = {k for k in legacy.ALIASES["trial_json"] if "." not in k and "[" not in k}
    assert old_root_keys and not old_root_keys & set(t)
    ts = t["timestamps"]
    assert ts["create_start"] < ts["all_ready"] <= ts["barrier_release"] < ts["last_task_return"] < ts["verify_clean_pass"]
    assert t["counts"] == {"microvms_requested": 2, "microvms_created": 2, "microvms_ready": 2,
                           "microvms_failed_startup": 0, "microvms_by_outcome": {"completed": 2},
                           "tasks_dispatched": 2, "tasks_ok": 2, "tasks_by_failure_category": {"ok": 2}}
    assert set(t["percentiles"]["steps"]) == set(schemas.STEP_NAMES)
    assert all(v["count"] == 2 for v in t["percentiles"]["steps"].values())
    assert t["percentiles"]["task_ms"]["p95"] is not None and t["means"]["request_count"] == 12
    assert t["timeouts"]["proxy_deadline_ms"] == 1300 and t["timeouts"]["client_timeout_ms"] == 1600
    assert t["fixture"]["guest_base_url"] == "http://fixture"

    # driver telemetry
    spans = list(iter_spans(out / "spans.jsonl"))
    assert all(s["service"] == "driver" and s["attributes"]["fleetkit.run_id"] == "run-1" for s in spans)
    raw = json.loads((out / "spans.jsonl").read_text().splitlines()[0])
    assert "resourceSpans" in raw  # collector-shaped lines, like hostd's and results/lgtm/otlp/
    assert "resourceLogs" in json.loads((out / "logs.jsonl").read_text().splitlines()[0])
    names = {s["name"] for s in spans}
    assert {"trial", "fixture_check", "microvms.create", "wait_all_ready", "tasks", "task", "cleanup", "verify_clean"} <= names
    trial_span = next(s for s in spans if s["name"] == "trial" and s["attributes"]["fleetkit.trial_id"] == "d2-t1")
    assert trial_span["trace_id"] == t["trace_id"] == tasks[0]["trace_id"]
    assert int(trial_span["attributes"]["fleetkit.density"]) == 2 and int(trial_span["attributes"]["fleetkit.trial_number"]) == 1
    task_span = next(s for s in spans if s["name"] == "task")
    assert task_span["attributes"]["fleetkit.microvm_id"] in {m["microvm_id"] for m in microvms}
    logs = [json.loads(l) for l in (out / "logs.jsonl").read_text().splitlines()]
    trial_logs = [lr["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0] for lr in logs]
    assert any({"key": "density", "value": {"intValue": "2"}} in r["attributes"] for r in trial_logs)
    assert all(r["severityText"] in ("INFO", "WARN", "ERROR") for r in trial_logs)
    assert (out / "logs.jsonl").exists() and (out / "driver.log").exists()
    # the stub hostd saw the traceparent and the run id
    hspans = [json.loads(l) for l in (hostd.telemetry_dir / "spans.jsonl").read_text().splitlines()]
    assert any(s["trace_id"] == t["trace_id"] and s["service"] == "guest-daemon" for s in hspans)
    assert any(s["attributes"].get("fleetkit.run_id") == "run-1" for s in hspans)
    assert (out / "guest-logs").exists() and len(list((out / "guest-logs").iterdir())) == 5


def test_rerun_appends_to_the_same_run_dir(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-x"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1") == 0
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1,2") == 0
    tasks = read_csv(out / "tasks.csv")
    # a second invocation keeps counting within each density, so ids never collide
    assert list(dict.fromkeys(t["trial_id"] for t in tasks)) == ["d1-t1", "d1-t2", "d2-t1"]
    assert [read_json(out / "trials" / tid / "trial.json")["sequence"] for tid in ("d1-t1", "d1-t2", "d2-t1")] == [1, 2, 3]
    with open(out / "tasks.csv") as fh:
        assert fh.read().count("run_id,trial_id") == 1  # one header


def test_degraded_trial_counts_only_what_ran(fixture_site, tmp_path):
    with StubServer(crash_slots={1}, proxy_margin_ms=300) as hostd:
        out = tmp_path / "run-d"
        rc = run_cli("trial", *common(hostd, fixture_site, out), "--densities", "3")
    assert rc == 1
    t = read_json(out / "trials" / "d3-t1" / "trial.json")
    assert t["status"] == "degraded" and not t["passed"] and not t["protocol_ok"]
    assert t["counts"]["tasks_dispatched"] == 2 and t["counts"]["tasks_ok"] == 2
    assert t["counts"]["microvms_by_outcome"] == {"completed": 2, "startup_error": 1}
    microvms = read_csv(out / "microvms.csv")
    crashed = next(s for s in microvms if s["slot"] == "1")
    assert crashed["outcome"] == "startup_error" and crashed["cleanup_ms"] and crashed["destroyed_ts"]
    assert len(read_csv(out / "tasks.csv")) == 2


def test_leftovers_fail_the_trial(fixture_site, tmp_path):
    with StubServer(leak_after_trial=True, proxy_margin_ms=300) as hostd:
        out = tmp_path / "run-l"
        rc = run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1")
    assert rc == 1
    t = read_json(out / "trials" / "d1-t1" / "trial.json")
    assert t["status"] == "failed" and t["verify_clean"]["clean"] is False and t["verify_clean"]["leftovers"]
    assert t["timestamps"]["verify_clean_pass"] is None
    assert t["counts"]["tasks_ok"] == 1  # the task itself was fine; cleanup was not


def test_fixture_check_failure_creates_no_microvms(hostd, tmp_path):
    out = tmp_path / "run-f"
    rc = run_cli("trial", "--backend", "docker", "--host-url", hostd.url, "--fixture-check-url",
                 "http://127.0.0.1:9", "--out", str(out), "--quiet", "--no-lgtm", "--densities", "2")
    assert rc == 1
    t = read_json(out / "trials" / "d2-t1" / "trial.json")
    assert t["status"] == "failed" and "fixture" in t["error"] and t["counts"]["microvms_created"] == 0
    assert not hostd.host.microvms
    assert (out / "tasks.csv").exists() and len(read_csv(out / "tasks.csv")) == 0  # valid, empty


def test_fault_trials(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-fault"
    rc = run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1", "--fault", "hang_step")
    assert rc == 1
    # fault trials are labelled, not numbered
    t = read_json(out / "trials" / "fault-hang_step" / "trial.json")
    assert t["fault"] == "hang_step" and t["status"] == "degraded"
    assert t["trial_kind"] == "fault" and t["trial_number"] is None and t["density"] == 1
    assert {r["trial_number"] for r in read_csv(out / "tasks.csv")} == {""}
    task = t["microvms"][0]["task"]
    assert task["failure_category"] == "step_timeout" and task["failed_step"] == "search"
    assert t["microvms"][0]["state_after_task"] == "ready" and t["microvms"][0]["outcome"] == "completed"
    # the guest returns only the completed steps (home); the driver synthesizes the failed step's row
    # so steps.csv carries the failed step and its error
    steps = read_csv(out / "steps.csv")
    assert [s["name"] for s in steps] == ["home", "search"]
    assert steps[0]["error"] == "" and steps[0]["settle_ts"] and steps[0]["duration_ms"]
    failed = steps[1]
    assert failed["step_index"] == "1" and failed["error"] == task["error"] and "search" in failed["error"]
    assert failed["settle_ts"] == "" and failed["duration_ms"] == ""
    assert failed["dispatch_ts"] == steps[0]["settle_ts"]  # the failed step began where the last one settled
    assert task["steps"] == 1  # the guest's own count of completed steps
    # timing percentiles describe the standard task only; the failure's elapsed time is labelled apart
    pct = t["percentiles"]
    assert pct["task_ms"]["count"] == 0 and pct["wall_ms"]["count"] == 0 and pct["harness_overhead_ms"]["count"] == 0
    assert pct["failed_task_elapsed_ms"]["count"] == 1 and pct["failed_task_elapsed_ms"]["p50"] >= 300

    rc = run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1", "--fault", "hang_task")
    t = read_json(out / "trials" / "fault-hang_task" / "trial.json")
    assert t["sequence"] == 2
    task = t["microvms"][0]["task"]
    assert task["failure_category"] == "guest_unreachable" and task["wall_ms"] <= 1000 + 300 + 300
    assert t["microvms"][0]["outcome"] == "task_failure_destroyed"
    # no failed_step for guest_unreachable, so no synthesized step row either
    assert not [s for s in read_csv(out / "steps.csv") if s["trial_id"] == "fault-hang_task"]
    # the same fault again gets the next label
    run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1", "--fault", "hang_step")
    assert (out / "trials" / "fault-hang_step-2" / "trial.json").exists()


def test_ok_task_percentiles_unchanged_and_no_synthesized_rows(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-ok"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "2") == 0
    t = read_json(out / "trials" / "d2-t1" / "trial.json")
    pct = t["percentiles"]
    assert pct["task_ms"]["count"] == 2 and pct["wall_ms"]["count"] == 2 and pct["harness_overhead_ms"]["count"] == 2
    assert pct["failed_task_elapsed_ms"] == {"count": 0, "p50": None, "p95": None, "mean": None, "min": None, "max": None}
    steps = read_csv(out / "steps.csv")
    assert len(steps) == 10 and all(s["error"] == "" and s["settle_ts"] for s in steps)


def test_categorize_closed_set():
    assert categorize(200, {"ok": True, "failure_category": "ok"}, None) == ("ok", "")
    assert categorize(200, {"ok": False, "failure_category": "step_timeout", "error": "x"}, None) == ("step_timeout", "x")
    assert categorize(409, {"error": "busy"}, None)[0] == "microvm_not_ready"
    assert categorize(504, {}, None)[0] == "guest_unreachable"
    assert categorize(0, None, "timed out")[0] == "guest_unreachable"
    assert categorize(200, {"ok": False, "failure_category": "ok"}, None)[0] == "assertion_failed"
    assert categorize(200, {"ok": True, "failure_category": "made_up"}, None)[0] == "guest_unreachable"
    for c, _ in [categorize(s, b, e) for s, b, e in ((200, {"failure_category": "browser_crashed"}, None),)]:
        assert c in schemas.FAILURE_CATEGORIES
