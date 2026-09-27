"""End to end: a capacity ladder against the stub host daemon, whose step latency grows with N."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from driver import schemas
from driver.bundle import run_bundle
from driver.outputs import read_csv, read_json
from tests.conftest import FAST, common, run_cli
from tests.stub_hostd import StubServer

EC2 = {"instance_id": "i-00000000000000000", "instance_type": "m8i.4xlarge", "ami_id": "ami-00000000000000000",
       "availability_zone": "us-east-1a"}
LADDER = ["--n", "1,2,4,8", "--stop-at-first-miss", "--confirm-repeats", "1", "--warmup", "1", "--settle-s", "0.5",
          "--illustration", "--metrics-hz", "5", "--sample-interval-ms", "50", "--step-p50-target-ms", "100"]


def _header(path: Path) -> list[str]:
    with open(path, newline="") as fh:
        return next(csv.reader(fh))


def test_capacity_ladder_end_to_end(fixture_site, tmp_path):
    # steps take 5 ms + 40 ms per live session beyond the first: n=1 ~5, n=2 ~45, n=4 ~125 (misses 100)
    with StubServer(telemetry_dir=str(tmp_path / "hostd"), proxy_margin_ms=300, startup_ms=40, step_ms=5,
                    step_ms_per_n=40, jitter_ms=2, ec2=EC2) as hostd:
        out = tmp_path / "results" / "cap-1"
        rc = run_cli("trial", *common(hostd, fixture_site, out), *LADDER)
        payloads = list(hostd.host.task_payloads)
    assert rc == 0  # a missed level is the experiment's result, not a failed run

    # trial ids and kinds, in order: warmup, ladder 1 2 4 (stop), confirm 2 and 4, illustration
    trials = {t["trial_id"]: t for t in (read_json(p) for p in sorted((out / "trials").glob("*/trial.json")))}
    assert list(trials) == ["t001-warmup-n1-r1", "t002-docker-n1-r1", "t003-docker-n2-r1", "t004-docker-n4-r1",
                            "t005-docker-n2-r2", "t006-docker-n4-r2", "t007-illustration-n1"]
    assert [t["kind"] for t in trials.values()] == ["warmup", "ladder", "ladder", "ladder", "confirm", "confirm",
                                                    "illustration"]
    passed = {tid: t["evaluation"]["passed"] for tid, t in trials.items()}
    assert passed["t002-docker-n1-r1"] and passed["t003-docker-n2-r1"] and passed["t005-docker-n2-r2"]
    assert not passed["t004-docker-n4-r1"] and not passed["t006-docker-n4-r2"]
    t4 = trials["t004-docker-n4-r1"]
    assert t4["evaluation"]["protocol_ok"] and not t4["evaluation"]["targets_met"] and t4["status"] == "ok"
    assert any(r.startswith("step home p50") for r in t4["evaluation"]["reasons"])
    # A trial that misses its targets must not read as passed anywhere in trial.json.
    assert t4["protocol_ok"] is True and t4["level_passed"] is False
    for t in trials.values():
        assert t["level_passed"] == t["evaluation"]["passed"]
    assert t4["criteria"]["step_p50_target_ms"] == 100.0
    for t in trials.values():
        pre = t["pre_trial"]
        assert pre["settle_s"] == 0.5 and pre["samples"] >= 1 and pre["cpu_util_mean"] is not None
        assert pre["cpu_util_max"] >= pre["cpu_util_mean"]
        assert t["sample_interval_ms"] == 50
        assert t["timestamps"]["create_start"] > 0
    assert trials["t007-illustration-n1"]["screenshot_each_step"] and not trials["t002-docker-n1-r1"]["screenshot_each_step"]

    # run.json: criteria, inputs and the plan
    run = read_json(out / "run.json")
    assert run["criteria"] == {"step_p50_target_ms": 100.0, "step_p95_target_ms": None, "task_p95_target_ms": None}
    inp = run["inputs"]
    assert "--stop-at-first-miss" in inp["argv"] and inp["options"]["confirm_repeats"] == 1
    assert inp["ladder"] == [1, 2, 4, 8] and inp["confirm_repeats"] == 1 and inp["warmup"] == 1
    assert inp["settle_s"] == 0.5 and inp["metrics_hz"] == 5 and inp["sample_interval_ms"] == 50
    assert inp["criteria"] == run["criteria"] and inp["fixture_check_url"] == fixture_site.url
    assert inp["fixture_base_url"] == "http://fixture" and inp["otlp_endpoint"] is None  # --no-lgtm
    assert inp["host_info"]["cpu_model"] == "Stub CPU @ 3.00GHz" and inp["host_info"]["ec2"] == EC2
    assert inp["guest_info"]["guestd_version"] == "stub" and inp["guest_info"]["chromium_flags"]
    assert "git_commit" in inp
    plan = run["plan"]
    assert plan["stop_reason"] == "first_miss" and plan["complete"]
    assert plan["boundary"] == {"last_pass": 2, "first_miss": 4, "last_pass_trials": 2, "first_miss_trials": 2}
    assert [(l["level"], l["passed"]) for l in plan["levels_run"]] == [(1, True), (2, True), (4, False)]
    assert plan["levels_not_run"] == [8]
    assert plan["confirmations"] == [{"level": 2, "trials": ["t005-docker-n2-r2"], "passed": True},
                                     {"level": 4, "trials": ["t006-docker-n4-r2"], "passed": False}]

    # every task payload carried the sampling interval; only the illustration asked for step screenshots
    assert payloads and all(p["sample_interval_ms"] == 50 for p in payloads)
    assert [p["task_id"] for p in payloads if p["screenshot_each_step"]] == ["t007-illustration-n1-slot000"]

    # CSVs: exact schemas, new columns filled
    for name, cols in (("tasks.csv", schemas.TASKS_COLUMNS), ("steps.csv", schemas.STEPS_COLUMNS),
                       ("sessions.csv", schemas.SESSIONS_COLUMNS), ("host_metrics.csv", schemas.HOST_METRICS_COLUMNS),
                       ("guest_metrics.csv", schemas.GUEST_METRICS_COLUMNS)):
        assert _header(out / name) == cols, name
    tasks = read_csv(out / "tasks.csv")
    assert {t["trial_id"]: t["kind"] for t in tasks}["t001-warmup-n1-r1"] == "warmup"
    assert {t["kind"] for t in tasks} == {"warmup", "ladder", "confirm", "illustration"}
    assert all(t["guestd_cpu_ms"] for t in tasks)
    assert {t["timing_valid"] for t in tasks if t["kind"] == "illustration"} == {"false"}
    assert {t["timing_valid"] for t in tasks if t["kind"] != "illustration"} == {"true"}
    steps = read_csv(out / "steps.csv")
    for t in tasks:
        mine = [s for s in steps if s["task_id"] == t["task_id"]]
        assert len(mine) == 5
        assert sum(int(s["bytes_received"]) for s in mine) == int(t["bytes_received"])
        assert sum(int(s["request_count"]) for s in mine) == int(t["request_count"]) == 12
    sessions = read_csv(out / "sessions.csv")
    for s in sessions:
        assert float(s["created_ts"]) < float(s["kernel_start_ts"]) < float(s["guestd_start_ts"]) \
            < float(s["chromium_launch_ts"]) < float(s["chromium_ready_ts"]) <= float(s["ready_ts"])

    gm = read_csv(out / "guest_metrics.csv")
    assert {g["trial_id"] for g in gm} == set(trials)
    metrics = {g["metric"] for g in gm}
    assert {"cpu_total_ms", "cpu_idle_ms", "mem_available", "psi_cpu_some_total_us", "cpu_ms.renderer",
            "rss_bytes.browser", "procs.guestd"} <= metrics
    t4task = next(t for t in tasks if t["trial_id"] == "t004-docker-n4-r1")  # ~600 ms at 50 ms sampling
    one = [g for g in gm if g["task_id"] == t4task["task_id"] and g["metric"] == "cpu_total_ms"]
    assert len(one) >= 5
    # host-clock timestamps inside the task's own dispatch-to-return span
    t_disp = float(t4task["dispatch_ts"])
    t_ret = t_disp + float(t4task["wall_ms"]) / 1000.0
    assert all(t_disp - 0.05 <= float(g["ts"]) <= t_ret + 0.05 for g in one)
    assert sorted(float(g["ts"]) for g in one) == [float(g["ts"]) for g in one]

    hm = read_csv(out / "host_metrics.csv")
    by = {(m["session_id"], m["metric"]) for m in hm}
    assert {("host", "cpu_count"), ("host", "hostd_cpu_usec"), ("host", "hostd_rss_bytes"),
            ("driver", "driver_cpu_usec"), ("driver", "driver_rss_bytes"), ("fixture", "fixture_rtt_ms")} <= by
    host_ts = [float(m["ts"]) for m in hm if m["session_id"] == "host" and m["metric"] == "cpu_util"]
    assert len(host_ts) == len(set(host_ts))  # no repeated samples

    # step screenshots only for the illustration trial's task
    shots = sorted(p.name for p in (out / "screenshots").iterdir() if p.name.count("-slot000-"))
    assert shots == [f"t007-illustration-n1-slot000-{s}.jpg" for s in sorted(schemas.STEP_NAMES)]
    ill = trials["t007-illustration-n1"]["sessions"][0]["task"]
    assert ill["step_screenshots"] == [f"screenshots/t007-illustration-n1-slot000-{s}.jpg" for s in schemas.STEP_NAMES]
    assert trials["t002-docker-n1-r1"]["sessions"][0]["task"]["step_screenshots"] == []

    # report --run alone reproduces the run's own verdicts from run.json criteria
    assert run_cli("report", "--run", str(out), "--quiet") == 0
    rep = read_json(out / "report.json")
    assert rep["inputs"]["criteria_source"] == "run.json" and rep["inputs"]["step_target_ms"] == 100.0
    assert rep["inputs"]["instance"] == "m8i.4xlarge" and rep["inputs"]["instance_source"] == "host_info (IMDS)"
    h = rep["headline"]["docker"]
    assert h["highest_n_tested_successfully"] == 2 and h["limit_found"]
    assert h["boundary"] == {"last_pass": 2, "last_pass_trials": 2, "first_miss": 4, "first_miss_trials": 2}
    levels = {l["level_n"]: l for l in rep["levels"]}
    assert set(levels) == {1, 2, 4}  # warmup and illustration trials are listed apart
    assert levels[1]["trials"] == ["t002-docker-n1-r1"]
    assert levels[2]["passed"] and levels[2]["kinds"] == {"ladder": 1, "confirm": 1} and levels[2]["trials_passed"] == 2
    assert not levels[4]["passed"] and levels[4]["protocol_passed"] and levels[4]["targets"]["pooled"]
    assert [e["trial_id"] for e in levels[4]["trial_evaluations"]] == ["t004-docker-n4-r1", "t006-docker-n4-r2"]
    assert len(levels[4]["attribution"]) == 2 and levels[4]["verdicts"] == ["unknown"]  # docker: no PSI split, no steal
    a = levels[4]["attribution"][0]
    assert a["host"]["cpu_util_mean_pct"] is not None and a["driver_cpu_s"] is not None and a["hostd_cpu_s"] is not None
    assert a["guest"]["top_group"] == "renderer" and 0.49 < a["guest"]["top_group_share"] < 0.51
    assert {t["trial_id"] for t in rep["excluded_trials"]} == {"t001-warmup-n1-r1", "t007-illustration-n1"}
    assert rep["plan"]["boundary"]["first_miss"] == 4
    md = (out / "report.md").read_text()
    for section in ("## Criteria", "## Boundary", "limit found: yes", "every step's p50 <= 100 ms",
                    "the run's own criteria (run.json)", "| trial | verdict (rule-based) |", "stop reason first_miss"):
        assert section in md, section
    assert "maximum capacity" not in md.replace("not maximum capacity", "")

    # criteria flags override run.json: with a looser target every tested level passes
    assert run_cli("report", "--run", str(out), "--step-p50-target-ms", "1000", "--quiet") == 0
    rep = read_json(out / "report.json")
    assert rep["inputs"]["criteria_source"] == "cli"
    assert rep["headline"]["docker"]["highest_n_tested_successfully"] == 4 and not rep["headline"]["docker"]["limit_found"]
    # the old flag names still work
    assert run_cli("report", "--run", str(out), "--step-target-ms", "1", "--quiet") == 0
    assert read_json(out / "report.json")["headline"]["docker"]["highest_n_tested_successfully"] == 0

    # the bundle manifest takes instance type, AMI and host facts from run.json host_info
    m = run_bundle(str(out), hostd_dir=str(tmp_path / "hostd"))["manifest"]
    assert m["instance_type"] == "m8i.4xlarge" and m["ami_id"] == EC2["ami_id"]
    assert m["host_cpu_count"] == 16 and m["host_cpu_model"] == "Stub CPU @ 3.00GHz" and m["host_mem_total"] == 8e9
    assert m["trials"][0]["kind"] == "warmup" and m["trials"][1]["passed"] is True
    m = run_bundle(str(out), instance_type="m8i.2xlarge")["manifest"]
    assert m["instance_type"] == "m8i.2xlarge"  # the operator's value wins


def test_firecracker_counters_feed_the_attribution(fixture_site, tmp_path):
    with StubServer(proxy_margin_ms=300, startup_ms=40, step_ms=60, jitter_ms=2, host_info=False) as hostd:
        out = tmp_path / "cap-fc"
        rc = run_cli("trial", *common(hostd, fixture_site, out, backend="firecracker"), "--n", "2",
                     "--metrics-hz", "10", "--no-fixture-probe")
    assert rc == 0
    run = read_json(out / "run.json")
    assert run["inputs"]["host_info"] is None and run["inputs"]["fixture_base_url"] == "http://10.200.0.1:8081"
    assert run["plan"]["stop_reason"] == "ladder_complete" and run["plan"]["boundary"]["last_pass"] == 2
    hm = read_csv(out / "host_metrics.csv")
    assert not [m for m in hm if m["session_id"] == "fixture"]
    assert {m["metric"] for m in hm if m["session_id"].startswith("sess-")} >= {
        "cpu_vcpu_usec", "cpu_vmm_usec", "cpu_throttled_usec", "cpu_pressure_some_total_us"}
    assert run_cli("report", "--run", str(out), "--quiet") == 0
    rep = read_json(out / "report.json")
    assert rep["inputs"]["criteria_source"] == "run.json" and rep["inputs"]["instance"] is None
    a = rep["levels"][0]["attribution"][0]
    assert len(a["sessions"]) == 2 and all(s["vcpu_s"] and s["vmm_s"] for s in a["sessions"])
    assert 0.005 < a["vm_throttled_fraction_mean"] < 0.02  # the stub throttles 1% of the time
    assert a["sessions_cpu_s"] is not None and a["unattributed_cpu_s"] is not None
    assert a["verdicts"] == ["unknown"] and a["missing"] == ["steal_mean_pct"]  # the stub has no steal
    json.dumps(rep)  # serializable


def test_ladder_flags_need_ascending_levels(hostd, fixture_site, tmp_path):
    out = tmp_path / "bad"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--n", "4,2", "--stop-at-first-miss") == 2
    assert run_cli("trial", *common(hostd, fixture_site, out), "--n", "2", "--metrics-hz", "0") == 2
    assert not hostd.host.sessions


def test_trial_errors_still_fail_a_ladder_run(hostd, tmp_path):
    out = tmp_path / "nofixture"
    rc = run_cli("trial", "--backend", "docker", "--host-url", hostd.url, "--fixture-check-url", "http://127.0.0.1:9",
                 "--out", str(out), "--quiet", *FAST, "--n", "1,2", "--stop-at-first-miss")
    assert rc == 1
    run = read_json(out / "run.json")
    assert run["plan"]["boundary"] == {"last_pass": None, "first_miss": 1, "last_pass_trials": 0, "first_miss_trials": 1}
    assert run["plan"]["levels_not_run"] == [2]
