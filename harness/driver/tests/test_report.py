from __future__ import annotations

from driver.outputs import read_json
from driver.report import build_report, run_report
from driver.outputs import RunDir
from tests.conftest import common, run_cli


def test_report_numbers_and_headline(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-r"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1,2", "--trials-per-density", "2") == 0
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1", "--fault", "hang_step") == 1
    rc = run_cli("report", "--run", str(out), "--price-per-hour", "0.2117", "--instance", "m8i.xlarge",
                 "--step-target-ms", "1000", "--step-p95-ms", "2000", "--quiet")
    assert rc == 0
    rep = read_json(out / "report.json")
    md = (out / "report.md").read_text()
    assert rep["headline"]["docker"]["highest_density_tested_successfully"] == 2
    assert "highest density tested successfully" in md and "maximum" not in md
    densities = {(d["backend"], d["density"]): d for d in rep["densities"]}
    assert set(densities) == {("docker", 1), ("docker", 2)}
    d2 = densities[("docker", 2)]
    assert d2["trials"] == ["d2-t1", "d2-t2"] and d2["trial_count"] == 2
    assert d2["passed"] and d2["tasks_ok"] == 4 and d2["targets"]["met"]
    assert d2["microvms_requested"] == 4 and d2["microvms_ready"] == 4 and d2["microvms_by_outcome"] == {"completed": 4}
    assert set(d2["steps"]) == {"home", "search", "open_product", "add_to_cart", "verify_cart"}
    assert all(v["count"] == 4 for v in d2["steps"].values())
    assert d2["harness_overhead_ms"]["mean"] is not None and d2["harness_overhead_ms"]["mean"] >= 0
    # fault trials are excluded from densities and listed separately
    assert len(rep["fault_trials"]) == 1 and rep["fault_trials"][0]["fault"] == "hang_step"
    assert rep["fault_trials"][0]["trial_id"] == "fault-hang_step" and rep["fault_trials"][0]["trial_number"] is None
    # timing percentiles are over ok tasks only; a failed task's elapsed-to-failure is labelled apart
    ft = rep["fault_trials"][0]
    assert ft["tasks_dispatched"] == 1 and ft["tasks_ok"] == 0
    assert ft["task_ms"]["count"] == 0 and ft["wall_ms"]["count"] == 0 and ft["harness_overhead_ms"]["count"] == 0
    assert ft["failed_task_elapsed_ms"]["count"] == 1 and ft["failed_task_elapsed_ms"]["p50"] >= 300
    assert d2["task_ms"]["count"] == 4 and d2["failed_task_elapsed_ms"]["count"] == 0
    assert "ok tasks only" in md and "elapsed-to-failure" in md
    assert all(t["fault"] is None for d in rep["densities"] for t in rep["trials"] if t["trial_id"] in d["trials"])

    # cost formulas, checked against trial.json timestamps
    t = read_json(out / "trials" / "d1-t1" / "trial.json")
    ts = t["timestamps"]
    tr = next(x for x in rep["trials"] if x["trial_id"] == "d1-t1")
    exec_expected = 0.2117 * (ts["last_task_return"] - ts["barrier_release"]) / 3600 / 1
    obs_expected = 0.2117 * (ts["verify_clean_pass"] - ts["create_start"]) / 3600 / 1
    assert abs(tr["cost_usd_per_task"]["execution_only"] - exec_expected) < 1e-12
    assert abs(tr["cost_usd_per_task"]["observed"] - obs_expected) < 1e-12
    assert obs_expected > exec_expected
    assert abs(tr["cost_usd_per_task"]["fixture_serving_estimate"] - 12 * 0.0004 / 1000) < 1e-15
    # D52: cost per 1,000 tasks at the highest density tested successfully
    c1000 = rep["headline"]["docker"]["cost_usd_per_1000_tasks"]
    assert abs(c1000["observed"] - d2["cost_usd_per_task"]["observed"] * 1000) < 1e-12
    assert rep["host_provisioning"]["label"] == "not measured"
    for word in ("measured", "modeled", "assumed", "estimate"):
        assert word in md


def test_targets_gate_the_density(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-t"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "2") == 0
    rep, _ = run_report(str(out), price_per_hour=None, instance=None, step_target_ms=None,
                        step_p95_ms=0.001, task_target_ms=None)
    dv = rep["densities"][0]
    assert dv["protocol_passed"] and not dv["targets"]["met"] and not dv["passed"]
    assert rep["headline"]["docker"]["highest_density_tested_successfully"] == 0
    assert all(c["metric"].startswith("step ") for c in dv["targets"]["checked"])
    # the report can be written somewhere else
    rep, _ = run_report(str(out), out_dir=str(tmp_path / "elsewhere"), price_per_hour=None, instance=None,
                        step_target_ms=None, step_p95_ms=None, task_target_ms=None)
    assert read_json(tmp_path / "elsewhere" / "report.json")["densities"][0]["passed"]


def test_report_from_partial_run_dir(tmp_path):
    out = tmp_path / "empty"
    out.mkdir()
    rep = build_report(RunDir(out), None, None, None, None, None)
    assert rep["densities"] == [] and rep["headline"] == {}


def test_fixture_manifest_tolerance_wins_over_flag(hostd, fixture_site, tmp_path):
    import json
    out = tmp_path / "run-m"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1") == 0
    tasks = out / "tasks.csv"
    row = tasks.read_text().splitlines()[1].split(",")
    header = tasks.read_text().splitlines()[0].split(",")
    got_bytes = float(row[header.index("bytes_received")])
    got_reqs = float(row[header.index("request_count")])
    # the fixture's own tolerance object wins: 10% wide enough, 0% never satisfied by an offset expectation
    with_tol = tmp_path / "manifest-tol.json"
    with_tol.write_text(json.dumps({"expected_bytes": got_bytes * 1.05, "expected_request_count": got_reqs + 1,
                                    "tolerance": {"bytes_pct": 10, "requests_pct": 0}}))
    rep, md = run_report(str(out), price_per_hour=None, instance=None, step_target_ms=None, step_p95_ms=None,
                         task_target_ms=None, fixture_manifest=str(with_tol), bytes_tolerance=0.25)
    bf = rep["densities"][0]["bytes_flag"]
    assert bf["bytes_tolerance"] == 0.10 and bf["requests_tolerance"] == 0.0
    assert bf["bytes_within_tolerance"] and not bf["requests_within_tolerance"]
    assert "tolerance (10%)" in md and "OUTSIDE tolerance (0%)" in md
    # without a tolerance object the --bytes-tolerance flag applies to both
    no_tol = tmp_path / "manifest-notol.json"
    no_tol.write_text(json.dumps({"expected_bytes": got_bytes, "expected_request_count": got_reqs}))
    rep, _ = run_report(str(out), price_per_hour=None, instance=None, step_target_ms=None, step_p95_ms=None,
                        task_target_ms=None, fixture_manifest=str(no_tol), bytes_tolerance=0.25)
    bf = rep["densities"][0]["bytes_flag"]
    assert bf["bytes_tolerance"] == 0.25 and bf["requests_tolerance"] == 0.25
    assert bf["bytes_within_tolerance"] and bf["requests_within_tolerance"]
