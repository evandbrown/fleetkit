from __future__ import annotations

import csv
import json
from pathlib import Path

from driver import schemas
from driver.evidence import iter_spans
from driver.outputs import read_csv, read_json
from driver.trial import categorize
from tests.conftest import common, run_cli
from tests.stub_hostd import StubServer


def _header(path: Path) -> list[str]:
    with open(path, newline="") as fh:
        return next(csv.reader(fh))


def test_trial_levels_write_every_output(hostd, fixture_site, tmp_path):
    out = tmp_path / "results" / "run-1"
    rc = run_cli("trial", *common(hostd, fixture_site, out), "--n", "2,3", "--repeats", "1")
    assert rc == 0

    # exact schemas
    assert _header(out / "tasks.csv") == schemas.TASKS_COLUMNS
    assert _header(out / "steps.csv") == schemas.STEPS_COLUMNS
    assert _header(out / "sessions.csv") == schemas.SESSIONS_COLUMNS
    assert _header(out / "host_metrics.csv") == schemas.HOST_METRICS_COLUMNS

    tasks = read_csv(out / "tasks.csv")
    assert len(tasks) == 5 and all(t["ok"] == "true" and t["failure_category"] == "ok" for t in tasks)
    assert {t["trial_id"] for t in tasks} == {"t001-docker-n2-r1", "t002-docker-n3-r1"}
    assert all(t["run_id"] == "run-1" for t in tasks)
    # products assigned list[slot mod len]: 3 products, slots 0..2
    by_slot = {(t["trial_id"], t["slot"]): t["product_id"] for t in tasks}
    assert by_slot[("t002-docker-n3-r1", "0")] == "p-1001" and by_slot[("t002-docker-n3-r1", "2")] == "p-1003"
    for t in tasks:
        assert (out / t["screenshot_path"]).exists() and t["screenshot_path"] == f"screenshots/{t['task_id']}.jpg"
        assert float(t["wall_ms"]) >= float(t["task_ms"])
        assert t["trace_id"] and t["clock_offset_ns"] != "" and t["guest_mem_available"] and t["chromium_rss"]

    steps = read_csv(out / "steps.csv")
    assert len(steps) == 25
    assert [s["name"] for s in steps if s["task_id"] == tasks[0]["task_id"]] == list(schemas.STEP_NAMES)
    assert all(float(s["settle_ts"]) >= float(s["dispatch_ts"]) for s in steps)

    sessions = read_csv(out / "sessions.csv")
    assert len(sessions) == 5 and all(s["outcome"] == "completed" for s in sessions)
    assert all(s["startup_ms"] and s["cleanup_ms"] and s["destroyed_ts"] for s in sessions)
    assert all(s["vcpus"] == "2" and s["mem_mib"] == "2048" for s in sessions)

    metrics = read_csv(out / "host_metrics.csv")
    assert metrics and {m["metric"] for m in metrics if m["session_id"] == "host"} >= {"mem_total", "cpu_util", "psi_cpu_some_avg10"}
    assert not any(m["metric"] == "steal" for m in metrics)  # null skipped

    t = read_json(out / "trials" / "t001-docker-n2-r1" / "trial.json")
    assert t["status"] == "ok" and t["level_passed"] and t["complete"] and t["phase"] == "done"
    ts = t["timestamps"]
    assert ts["create_start"] < ts["all_ready"] <= ts["barrier_release"] < ts["last_task_return"] < ts["verify_clean_pass"]
    assert t["counts"] == {"sessions_requested": 2, "sessions_created": 2, "sessions_ready": 2,
                           "sessions_failed_startup": 0, "sessions_by_outcome": {"completed": 2},
                           "tasks_dispatched": 2, "tasks_ok": 2, "tasks_by_failure_category": {"ok": 2}}
    assert set(t["percentiles"]["steps"]) == set(schemas.STEP_NAMES)
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
    assert {"trial", "fixture_check", "sessions.create", "wait_all_ready", "tasks", "task", "cleanup", "verify_clean"} <= names
    trial_span = next(s for s in spans if s["name"] == "trial" and s["attributes"]["fleetkit.trial_id"] == "t001-docker-n2-r1")
    assert trial_span["trace_id"] == t["trace_id"] == tasks[0]["trace_id"]
    assert (out / "logs.jsonl").exists() and (out / "driver.log").exists()
    # the stub hostd saw the traceparent and the run id
    hspans = [json.loads(l) for l in (hostd.telemetry_dir / "spans.jsonl").read_text().splitlines()]
    assert any(s["trace_id"] == t["trace_id"] and s["service"] == "guest-daemon" for s in hspans)
    assert any(s["attributes"].get("fleetkit.run_id") == "run-1" for s in hspans)
    assert (out / "guest-logs").exists() and len(list((out / "guest-logs").iterdir())) == 5


def test_rerun_appends_to_the_same_run_dir(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-x"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--n", "1") == 0
    assert run_cli("trial", *common(hostd, fixture_site, out), "--n", "1") == 0
    tasks = read_csv(out / "tasks.csv")
    assert [t["trial_id"] for t in tasks] == ["t001-docker-n1-r1", "t002-docker-n1-r1"]
    with open(out / "tasks.csv") as fh:
        assert fh.read().count("run_id,trial_id") == 1  # one header


def test_degraded_trial_runs_at_actual_concurrency(fixture_site, tmp_path):
    with StubServer(crash_slots={1}, proxy_margin_ms=300) as hostd:
        out = tmp_path / "run-d"
        rc = run_cli("trial", *common(hostd, fixture_site, out), "--n", "3")
    assert rc == 1
    t = read_json(out / "trials" / "t001-docker-n3-r1" / "trial.json")
    assert t["status"] == "degraded" and not t["level_passed"]
    assert t["actual_concurrency"] == 2 and t["counts"]["tasks_ok"] == 2
    assert t["counts"]["sessions_by_outcome"] == {"completed": 2, "startup_error": 1}
    sessions = read_csv(out / "sessions.csv")
    crashed = next(s for s in sessions if s["slot"] == "1")
    assert crashed["outcome"] == "startup_error" and crashed["cleanup_ms"] and crashed["destroyed_ts"]
    assert len(read_csv(out / "tasks.csv")) == 2


def test_leftovers_fail_the_trial(fixture_site, tmp_path):
    with StubServer(leak_after_trial=True, proxy_margin_ms=300) as hostd:
        out = tmp_path / "run-l"
        rc = run_cli("trial", *common(hostd, fixture_site, out), "--n", "1")
    assert rc == 1
    t = read_json(out / "trials" / "t001-docker-n1-r1" / "trial.json")
    assert t["status"] == "failed" and t["verify_clean"]["clean"] is False and t["verify_clean"]["leftovers"]
    assert t["timestamps"]["verify_clean_pass"] is None
    assert t["counts"]["tasks_ok"] == 1  # the task itself was fine; cleanup was not


def test_fixture_check_failure_creates_no_sessions(hostd, tmp_path):
    out = tmp_path / "run-f"
    rc = run_cli("trial", "--backend", "docker", "--host-url", hostd.url, "--fixture-check-url",
                 "http://127.0.0.1:9", "--out", str(out), "--quiet", "--no-lgtm", "--n", "2")
    assert rc == 1
    t = read_json(out / "trials" / "t001-docker-n2-r1" / "trial.json")
    assert t["status"] == "failed" and "fixture" in t["error"] and t["counts"]["sessions_created"] == 0
    assert not hostd.host.sessions
    assert (out / "tasks.csv").exists() and len(read_csv(out / "tasks.csv")) == 0  # valid, empty


def test_fault_trials(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-fault"
    rc = run_cli("trial", *common(hostd, fixture_site, out), "--n", "1", "--fault", "hang_step")
    assert rc == 1
    t = read_json(out / "trials" / "t001-docker-n1-r1" / "trial.json")
    assert t["fault"] == "hang_step" and t["status"] == "degraded"
    task = t["sessions"][0]["task"]
    assert task["failure_category"] == "step_timeout" and task["failed_step"] == "search"
    assert t["sessions"][0]["state_after_task"] == "ready" and t["sessions"][0]["outcome"] == "completed"
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
    assert pct["task_ms"]["n"] == 0 and pct["wall_ms"]["n"] == 0 and pct["harness_overhead_ms"]["n"] == 0
    assert pct["failed_task_elapsed_ms"]["n"] == 1 and pct["failed_task_elapsed_ms"]["p50"] >= 300

    rc = run_cli("trial", *common(hostd, fixture_site, out), "--n", "1", "--fault", "hang_task")
    t = read_json(out / "trials" / "t002-docker-n1-r1" / "trial.json")
    task = t["sessions"][0]["task"]
    assert task["failure_category"] == "guest_unreachable" and task["wall_ms"] <= 1000 + 300 + 300
    assert t["sessions"][0]["outcome"] == "task_failure_destroyed"
    # no failed_step for guest_unreachable, so no synthesized step row either
    assert not [s for s in read_csv(out / "steps.csv") if s["trial_id"] == "t002-docker-n1-r1"]


def test_ok_task_percentiles_unchanged_and_no_synthesized_rows(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-ok"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--n", "2") == 0
    t = read_json(out / "trials" / "t001-docker-n2-r1" / "trial.json")
    pct = t["percentiles"]
    assert pct["task_ms"]["n"] == 2 and pct["wall_ms"]["n"] == 2 and pct["harness_overhead_ms"]["n"] == 2
    assert pct["failed_task_elapsed_ms"] == {"n": 0, "p50": None, "p95": None, "mean": None, "min": None, "max": None}
    steps = read_csv(out / "steps.csv")
    assert len(steps) == 10 and all(s["error"] == "" and s["settle_ts"] for s in steps)


def test_categorize_closed_set():
    assert categorize(200, {"ok": True, "failure_category": "ok"}, None) == ("ok", "")
    assert categorize(200, {"ok": False, "failure_category": "step_timeout", "error": "x"}, None) == ("step_timeout", "x")
    assert categorize(409, {"error": "busy"}, None)[0] == "session_not_ready"
    assert categorize(504, {}, None)[0] == "guest_unreachable"
    assert categorize(0, None, "timed out")[0] == "guest_unreachable"
    assert categorize(200, {"ok": False, "failure_category": "ok"}, None)[0] == "assertion_failed"
    assert categorize(200, {"ok": True, "failure_category": "made_up"}, None)[0] == "guest_unreachable"
    for c, _ in [categorize(s, b, e) for s, b, e in ((200, {"failure_category": "browser_crashed"}, None),)]:
        assert c in schemas.FAILURE_CATEGORIES
