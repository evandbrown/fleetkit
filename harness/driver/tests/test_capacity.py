"""End to end: a capacity ladder against the stub host daemon, whose step latency grows with N."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from driver import schemas
from driver.bundle import run_bundle
from driver.outputs import read_csv, read_json
from tests.conftest import FAST, common, run_cli
from tests.stub_hostd import StubServer

EC2 = {"instance_id": "i-00000000000000000", "instance_type": "m8i.4xlarge", "ami_id": "ami-00000000000000000",
       "availability_zone": "us-east-1a"}
CAPACITY = ["--densities", "1,2,4,8", "--stop-at-first-miss", "--boundary-trials", "1", "--warmup", "1",
            "--settle-s", "0.5", "--illustration", "--metrics-hz", "5", "--sample-interval-ms", "50",
            "--step-p50-target-ms", "100"]


def _header(path: Path) -> list[str]:
    with open(path, newline="") as fh:
        return next(csv.reader(fh))


def test_capacity_ladder_end_to_end(fixture_site, tmp_path):
    # steps take 5 ms + 40 ms per live microVM beyond the first: density 1 ~5, 2 ~45, 4 ~125 (misses 100)
    with StubServer(telemetry_dir=str(tmp_path / "hostd"), proxy_margin_ms=300, startup_ms=40, step_ms=5,
                    step_ms_per_n=40, jitter_ms=2, ec2=EC2) as hostd:
        out = tmp_path / "results" / "cap-1"
        rc = run_cli("trial", *common(hostd, fixture_site, out), *CAPACITY)
        payloads = list(hostd.host.task_payloads)
    assert rc == 0  # a missed density is the experiment's result, not a failed run

    # trial ids and kinds, in execution order: warm-up, ladder 1 2 4 (stop), boundary 2 and 4, illustration.
    # Counting trials are numbered within their density; warm-up and illustration are labelled.
    docs = sorted((read_json(p) for p in (out / "trials").glob("*/trial.json")), key=lambda t: t["sequence"])
    trials = {t["trial_id"]: t for t in docs}
    assert list(trials) == ["warmup", "d1-t1", "d2-t1", "d4-t1", "d2-t2", "d4-t2", "illustration"]
    assert [t["sequence"] for t in docs] == list(range(1, 8))
    assert [t["trial_kind"] for t in docs] == ["warmup", "ladder", "ladder", "ladder", "boundary", "boundary",
                                               "illustration"]
    assert [t["trial_number"] for t in docs] == [None, 1, 1, 1, 2, 2, None]
    passed = {tid: t["evaluation"]["passed"] for tid, t in trials.items()}
    assert passed["d1-t1"] and passed["d2-t1"] and passed["d2-t2"]
    assert not passed["d4-t1"] and not passed["d4-t2"]
    t4 = trials["d4-t1"]
    assert t4["evaluation"]["protocol_ok"] and not t4["evaluation"]["targets_met"] and t4["status"] == "ok"
    assert any(r.startswith("step home p50") for r in t4["evaluation"]["reasons"])
    # A trial that misses its targets must not read as passed anywhere in trial.json.
    assert t4["protocol_ok"] is True and t4["passed"] is False
    for t in trials.values():
        assert t["passed"] == t["evaluation"]["passed"]
    assert t4["criteria"]["step_p50_target_ms"] == 100.0
    for t in trials.values():
        pre = t["pre_trial"]
        assert pre["settle_s"] == 0.5 and pre["samples"] >= 1 and pre["cpu_util_mean"] is not None
        assert pre["cpu_util_max"] >= pre["cpu_util_mean"]
        assert t["sample_interval_ms"] == 50
        assert t["timestamps"]["create_start"] > 0
    assert trials["illustration"]["screenshot_each_step"] and not trials["d1-t1"]["screenshot_each_step"]

    # run.json: criteria, the spec, what was observed, and the plan
    run = read_json(out / "run.json")
    assert run["criteria"] == {"step_p50_target_ms": 100.0, "step_p95_target_ms": None, "task_p95_target_ms": None}
    assert "inputs" not in run
    spec = run["spec"]
    assert "--stop-at-first-miss" in spec["argv"] and spec["options"]["boundary_trials"] == 1
    assert spec["densities"] == [1, 2, 4, 8] and spec["trials_per_density"] == 1 and spec["boundary_trials"] == 1
    assert spec["warmup"] == 1 and spec["settle_s"] == 0.5 and spec["metrics_hz"] == 5 and spec["sample_interval_ms"] == 50
    assert spec["criteria"] == run["criteria"] and spec["fixture_check_url"] == fixture_site.url
    assert spec["fixture_base_url"] == "http://fixture" and spec["otlp_endpoint"] is None  # --no-lgtm
    assert "git_commit" in spec
    # measured facts are never inputs: the host's and the guest's own reports sit apart from the spec
    assert "host_info" not in spec and "guest_info" not in spec
    obs = run["observed"]
    assert obs["host_info"]["cpu_model"] == "Stub CPU @ 3.00GHz" and obs["host_info"]["ec2"] == EC2
    assert obs["guest_info"]["guestd_version"] == "stub" and obs["guest_info"]["chromium_flags"]
    plan = run["plan"]
    assert plan["stop_reason"] == "first_miss" and plan["complete"]
    assert plan["densities"] == [1, 2, 4, 8] and plan["trials_per_density"] == 1 and plan["boundary_trials"] == 1
    assert plan["boundary"] == {"last_pass": 2, "first_miss": 4, "last_pass_trials": 2, "first_miss_trials": 2}
    assert [(d["density"], d["passed"]) for d in plan["densities_run"]] == [(1, True), (2, True), (4, False)]
    assert plan["densities_run"][1] == {"density": 2, "trials": ["d2-t1", "d2-t2"], "ladder_trial_count": 1,
                                        "boundary_trial_count": 1, "ladder_passed": True, "passed": True}
    assert plan["densities_not_run"] == [8]
    assert plan["boundary_checks"] == [{"density": 2, "trials": ["d2-t2"], "passed": True},
                                       {"density": 4, "trials": ["d4-t2"], "passed": False}]

    # every task payload carried the sampling interval; only the illustration asked for step screenshots
    assert payloads and all(p["sample_interval_ms"] == 50 for p in payloads)
    assert [p["task_id"] for p in payloads if p["screenshot_each_step"]] == ["illustration-slot000"]

    # CSVs: exact schemas, new columns filled
    for name, cols in (("tasks.csv", schemas.TASKS_COLUMNS), ("steps.csv", schemas.STEPS_COLUMNS),
                       ("microvms.csv", schemas.MICROVMS_COLUMNS), ("host_metrics.csv", schemas.HOST_METRICS_COLUMNS),
                       ("guest_metrics.csv", schemas.GUEST_METRICS_COLUMNS)):
        assert _header(out / name) == cols, name
    tasks = read_csv(out / "tasks.csv")
    assert {t["trial_id"]: t["trial_kind"] for t in tasks}["warmup"] == "warmup"
    assert {t["trial_kind"] for t in tasks} == {"warmup", "ladder", "boundary", "illustration"}
    assert {(t["trial_id"], t["density"], t["trial_number"]) for t in tasks if t["trial_id"] in ("warmup", "d4-t2")} \
        == {("warmup", "1", ""), ("d4-t2", "4", "2")}
    assert all(t["guestd_cpu_ms"] for t in tasks)
    assert {t["timing_valid"] for t in tasks if t["trial_kind"] == "illustration"} == {"false"}
    assert {t["timing_valid"] for t in tasks if t["trial_kind"] != "illustration"} == {"true"}
    steps = read_csv(out / "steps.csv")
    for t in tasks:
        mine = [s for s in steps if s["task_id"] == t["task_id"]]
        assert len(mine) == 5
        assert sum(int(s["bytes_received"]) for s in mine) == int(t["bytes_received"])
        assert sum(int(s["request_count"]) for s in mine) == int(t["request_count"]) == 12
    microvms = read_csv(out / "microvms.csv")
    for s in microvms:
        assert float(s["created_ts"]) < float(s["kernel_start_ts"]) < float(s["guestd_start_ts"]) \
            < float(s["chromium_launch_ts"]) < float(s["chromium_ready_ts"]) <= float(s["ready_ts"])

    gm = read_csv(out / "guest_metrics.csv")
    assert {g["trial_id"] for g in gm} == set(trials)
    metrics = {g["metric"] for g in gm}
    assert {"cpu_total_ms", "cpu_idle_ms", "mem_available", "psi_cpu_some_total_us", "cpu_ms.renderer",
            "rss_bytes.browser", "procs.guestd"} <= metrics
    t4task = next(t for t in tasks if t["trial_id"] == "d4-t1")  # ~600 ms at 50 ms sampling
    one = [g for g in gm if g["task_id"] == t4task["task_id"] and g["metric"] == "cpu_total_ms"]
    assert len(one) >= 5
    # host-clock timestamps inside the task's own dispatch-to-return span
    t_disp = float(t4task["dispatch_ts"])
    t_ret = t_disp + float(t4task["wall_ms"]) / 1000.0
    assert all(t_disp - 0.05 <= float(g["ts"]) <= t_ret + 0.05 for g in one)
    assert sorted(float(g["ts"]) for g in one) == [float(g["ts"]) for g in one]

    hm = read_csv(out / "host_metrics.csv")
    by = {(m["subject"], m["metric"]) for m in hm}
    assert {("host", "cpu_count"), ("host", "hostd_cpu_usec"), ("host", "hostd_rss_bytes"),
            ("driver", "driver_cpu_usec"), ("driver", "driver_rss_bytes"), ("fixture", "fixture_rtt_ms")} <= by
    host_ts = [float(m["ts"]) for m in hm if m["subject"] == "host" and m["metric"] == "cpu_util"]
    assert len(host_ts) == len(set(host_ts))  # no duplicate samples

    # step screenshots only for the illustration trial's task
    shots = sorted(p.name for p in (out / "screenshots").iterdir() if p.name.count("-slot000-"))
    assert shots == [f"illustration-slot000-{s}.jpg" for s in sorted(schemas.STEP_NAMES)]
    ill = trials["illustration"]["microvms"][0]["task"]
    assert ill["step_screenshots"] == [f"screenshots/illustration-slot000-{s}.jpg" for s in schemas.STEP_NAMES]
    assert trials["d1-t1"]["microvms"][0]["task"]["step_screenshots"] == []

    # report --run alone reproduces the run's own verdicts from run.json criteria
    assert run_cli("report", "--run", str(out), "--quiet") == 0
    rep = read_json(out / "report.json")
    assert rep["inputs"]["criteria_source"] == "run.json" and rep["inputs"]["step_target_ms"] == 100.0
    assert rep["inputs"]["instance"] == "m8i.4xlarge" and rep["inputs"]["instance_source"] == "host_info (IMDS)"
    h = rep["headline"]["docker"]
    assert h["highest_density_tested_successfully"] == 2 and h["limit_found"]
    assert h["boundary"] == {"last_pass": 2, "last_pass_trials": 2, "first_miss": 4, "first_miss_trials": 2}
    # D52: hosts are compared by density per host vCPU and cost per 1,000 tasks
    assert h["host_vcpus"] == 16 and h["density_per_host_vcpu"] == 2 / 16
    assert h["cost_usd_per_1000_tasks"] == {"execution_only": None, "observed": None}  # no price given
    densities = {d["density"]: d for d in rep["densities"]}
    assert set(densities) == {1, 2, 4}  # warm-up and illustration trials are listed apart
    assert densities[1]["trials"] == ["d1-t1"] and densities[2]["trial_count"] == 2
    assert densities[2]["passed"] and densities[2]["trial_kinds"] == {"ladder": 1, "boundary": 1}
    assert densities[2]["trials_passed"] == 2
    assert not densities[4]["passed"] and densities[4]["protocol_passed"] and densities[4]["targets"]["pooled"]
    assert [e["trial_id"] for e in densities[4]["trial_evaluations"]] == ["d4-t1", "d4-t2"]
    assert len(densities[4]["attribution"]) == 2 and densities[4]["verdicts"] == ["unknown"]  # docker: no PSI split, no steal
    a = densities[4]["attribution"][0]
    assert a["host"]["cpu_util_mean_pct"] is not None and a["driver_cpu_s"] is not None and a["hostd_cpu_s"] is not None
    assert a["guest"]["top_group"] == "renderer" and 0.49 < a["guest"]["top_group_share"] < 0.51
    assert {t["trial_id"] for t in rep["excluded_trials"]} == {"warmup", "illustration"}
    assert [(t["trial_id"], t["trial_number"], t["passed"]) for t in rep["trials"][:3]] == [
        ("warmup", None, True), ("d1-t1", 1, True), ("d2-t1", 1, True)]
    assert rep["plan"]["boundary"]["first_miss"] == 4 and rep["observed"]["host_info"]["cpu_count"] == 16
    md = (out / "report.md").read_text()
    for section in ("## Criteria", "## Boundary", "## Densities", "limit found: yes", "every step's p50 <= 100 ms",
                    "the run's own criteria (run.json)", "| trial | verdict (rule-based) |", "stop reason first_miss",
                    "highest density tested successfully = **2**", "density per host vCPU: 0.125",
                    "### docker density 4 (2 trial(s): d4-t1, d4-t2)", "| step | samples |"):
        assert section in md, section
    assert "maximum" not in md

    # criteria flags override run.json: with a looser target every tested density passes
    assert run_cli("report", "--run", str(out), "--step-p50-target-ms", "1000", "--quiet") == 0
    rep = read_json(out / "report.json")
    assert rep["inputs"]["criteria_source"] == "cli"
    assert rep["headline"]["docker"]["highest_density_tested_successfully"] == 4
    assert not rep["headline"]["docker"]["limit_found"]
    # the old flag names still work
    assert run_cli("report", "--run", str(out), "--step-target-ms", "1", "--quiet") == 0
    assert read_json(out / "report.json")["headline"]["docker"]["highest_density_tested_successfully"] == 0

    # the bundle manifest takes instance type, AMI and host facts from run.json host_info
    m = run_bundle(str(out), hostd_dir=str(tmp_path / "hostd"))["manifest"]
    assert m["instance_type"] == "m8i.4xlarge" and m["ami_id"] == EC2["ami_id"]
    assert m["host_cpu_count"] == 16 and m["host_cpu_model"] == "Stub CPU @ 3.00GHz" and m["host_mem_total"] == 8e9
    assert m["trials"][0]["trial_kind"] == "warmup" and m["trials"][1]["passed"] is True
    assert (m["trials"][1]["trial_id"], m["trials"][1]["density"], m["trials"][1]["trial_number"]) == ("d1-t1", 1, 1)
    m = run_bundle(str(out), instance_type="m8i.2xlarge")["manifest"]
    assert m["instance_type"] == "m8i.2xlarge"  # the operator's value wins


def test_firecracker_counters_feed_the_attribution(fixture_site, tmp_path):
    with StubServer(proxy_margin_ms=300, startup_ms=40, step_ms=60, jitter_ms=2, host_info=False) as hostd:
        out = tmp_path / "cap-fc"
        rc = run_cli("trial", *common(hostd, fixture_site, out, backend="firecracker"), "--densities", "2",
                     "--metrics-hz", "10", "--no-fixture-probe")
    assert rc == 0
    run = read_json(out / "run.json")
    assert run["observed"]["host_info"] is None and run["spec"]["fixture_base_url"] == "http://10.200.0.1:8081"
    assert run["plan"]["stop_reason"] == "ladder_complete" and run["plan"]["boundary"]["last_pass"] == 2
    hm = read_csv(out / "host_metrics.csv")
    assert not [m for m in hm if m["subject"] == "fixture"]
    microvm_ids = {m["microvm_id"] for m in read_csv(out / "microvms.csv")}
    assert all(re.fullmatch(r"s\d{3}-[0-9a-f]{8}", mid) for mid in microvm_ids)
    assert {m["metric"] for m in hm if m["subject"] in microvm_ids} >= {
        "cpu_vcpu_usec", "cpu_hypervisor_usec", "cpu_throttled_usec", "cpu_pressure_some_total_us"}
    assert run_cli("report", "--run", str(out), "--quiet") == 0
    rep = read_json(out / "report.json")
    assert rep["inputs"]["criteria_source"] == "run.json" and rep["inputs"]["instance"] is None
    a = rep["densities"][0]["attribution"][0]
    assert len(a["microvms"]) == 2 and all(s["vcpu_s"] and s["hypervisor_s"] for s in a["microvms"])
    assert a["hypervisor_s"] == sum(s["hypervisor_s"] for s in a["microvms"])
    assert 0.005 < a["vm_throttled_fraction_mean"] < 0.02  # the stub throttles 1% of the time
    assert a["microvms_cpu_s"] is not None and a["unattributed_cpu_s"] is not None
    assert rep["headline"]["firecracker"]["density_per_host_vcpu"] is None  # no GET /host/info
    assert a["verdicts"] == ["unknown"] and a["missing"] == ["steal_mean_pct"]  # the stub has no steal
    json.dumps(rep)  # serializable


def test_ladder_flags_need_ascending_densities(hostd, fixture_site, tmp_path):
    out = tmp_path / "bad"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "4,2", "--stop-at-first-miss") == 2
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "2", "--metrics-hz", "0") == 2
    assert not hostd.host.microvms


def test_trial_errors_still_fail_a_ladder_run(hostd, tmp_path):
    out = tmp_path / "nofixture"
    rc = run_cli("trial", "--backend", "docker", "--host-url", hostd.url, "--fixture-check-url", "http://127.0.0.1:9",
                 "--out", str(out), "--quiet", *FAST, "--densities", "1,2", "--stop-at-first-miss")
    assert rc == 1
    run = read_json(out / "run.json")
    assert run["plan"]["boundary"] == {"last_pass": None, "first_miss": 1, "last_pass_trials": 0, "first_miss_trials": 1}
    assert run["plan"]["densities_not_run"] == [2]
