"""Run directories written before the glossary (D56) are read, translated, and never written.

The fixtures here are deliberately in the old names: they are what an older driver wrote.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from driver.bundle import REPO_ROOT
from driver.evidence import microvm_state_ids
from driver.legacy import ALIASES, dump_aliases, trial_id_map
from driver.outputs import LegacyRunDirError, RunDir, read_json
from driver.report import run_report
from tests.conftest import run_cli

BASELINE = REPO_ROOT / "results" / "cap-baseline-1" / "host" / "capacity"
BASELINE_FILES = ("run.json", "trials", "tasks.csv", "steps.csv", "sessions.csv", "host_metrics.csv",
                  "guest_metrics.csv", "manifest.json", "hostd-logs.jsonl")

# The column lists an older driver wrote.
OLD_TASKS = ["run_id", "trial_id", "backend", "level_n", "repeat", "session_id", "slot", "task_id", "product_id",
             "dispatch_ts", "task_ms", "wall_ms", "ok", "failure_category", "failed_step", "bytes_received",
             "request_count", "guest_mem_available", "chromium_rss", "screenshot_path", "trace_id", "clock_offset_ns",
             "error", "timing_valid", "guestd_cpu_ms", "kind"]
OLD_SESSIONS = ["run_id", "trial_id", "session_id", "slot", "backend", "vcpus", "mem_mib", "created_ts",
                "process_started_ts", "ready_ts", "destroyed_ts", "startup_ms", "cleanup_ms", "outcome", "error"]
OLD_STEPS = ["run_id", "trial_id", "task_id", "step_index", "name", "dispatch_ts", "settle_ts", "duration_ms", "error"]
STEPS = ("home", "search", "open_product", "add_to_cart", "verify_cart")


def _csv(path: Path, header: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def _digest(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def write_old_run(root: Path) -> Path:
    """A small run directory as the driver wrote it before the glossary: a warm-up, a ladder trial at 1
    and 2 (the second recorded before trials had a kind), a confirmation at 2, and a smoke session case."""
    root.mkdir(parents=True)
    trials = [  # old id, kind (None: before kinds existed), level, repeat, home-step ms
        ("t001-warmup-n1-r1", "warmup", 1, 1, 40.0),
        ("t002-firecracker-n1-r1", None, 1, 1, 50.0),
        ("t003-firecracker-n2-r1", "ladder", 2, 1, 150.0),
        ("t004-firecracker-n2-r2", "confirm", 2, 2, 60.0),
    ]
    tasks, steps, sessions, host, guest = [], [], [], [], []
    for i, (tid, kind, n, r, home_ms) in enumerate(trials):
        t0 = 1000.0 + 100 * i
        vms = []
        for slot in range(n):
            sid = f"s{slot:03d}-{i:08x}"
            task_id = f"{tid}-slot{slot:03d}"
            vms.append({"session_id": sid, "slot": slot, "ready": True, "outcome": "completed",
                        "task": {"task_id": task_id, "ok": True, "failure_category": "ok", "task_ms": 900.0}})
            tasks.append({"run_id": "old", "trial_id": tid, "backend": "firecracker", "level_n": n, "repeat": r,
                          "session_id": sid, "slot": slot, "task_id": task_id, "task_ms": 900.0, "wall_ms": 950.0,
                          "ok": "true", "failure_category": "ok", "kind": kind or "", "request_count": 12})
            for k, name in enumerate(STEPS):
                steps.append({"run_id": "old", "trial_id": tid, "task_id": task_id, "step_index": k, "name": name,
                              "duration_ms": home_ms if name == "home" else 20.0})
            sessions.append({"run_id": "old", "trial_id": tid, "session_id": sid, "slot": slot,
                             "backend": "firecracker", "vcpus": 2, "mem_mib": 2048, "created_ts": t0,
                             "process_started_ts": t0 + 0.1, "startup_ms": 900, "cleanup_ms": 50,
                             "outcome": "completed"})
            for k in range(6):
                ts = t0 + k
                host.append({"ts": ts, "session_id": sid, "metric": "cpu_vcpu_usec", "value": k * 1e6})
                host.append({"ts": ts, "session_id": sid, "metric": "cpu_vmm_usec", "value": k * 1e5})
            guest.append({"ts": t0 + 2, "trial_id": tid, "session_id": sid, "task_id": task_id,
                          "metric": "cpu_total_ms", "value": 100})
        for k in range(6):
            host.append({"ts": t0 + k, "session_id": "host", "metric": "cpu_util", "value": 40.0})
        doc = {"run_id": "old", "trial_id": tid, "backend": "firecracker", "level_n": n, "repeat": r,
               "fault": None, "complete": True, "error": None, "phase": "done", "status": "ok",
               "level_passed": True, "actual_concurrency": n, "verify_clean": {"clean": True, "leftovers": []},
               "timestamps": {"create_start": t0, "all_ready": t0 + 1, "barrier_release": t0 + 1,
                              "last_task_return": t0 + 3, "verify_clean_pass": t0 + 4},
               "counts": {"sessions_requested": n, "sessions_created": n, "sessions_ready": n,
                          "sessions_failed_startup": 0, "sessions_by_outcome": {"completed": n},
                          "tasks_dispatched": n, "tasks_ok": n, "tasks_by_failure_category": {"ok": n}},
               "percentiles": {"task_ms": {"n": n, "p50": 900.0, "p95": 900.0}}, "sessions": vms}
        if kind:
            doc["kind"] = kind
        d = root / "trials" / tid
        d.mkdir(parents=True)
        (d / "trial.json").write_text(json.dumps(doc))
    case = root / "trials" / "t005-case-idle"
    case.mkdir()
    (case / "case.json").write_text(json.dumps({
        "trial_id": "t005-case-idle", "case": "idle", "session_id": "s000-cccccccc", "spec": {"count": 1},
        "expected_outcome": "idle_expired", "outcome": "idle_expired", "passed": True,
        "session": {"outcome": "idle_expired"}}))
    _csv(root / "tasks.csv", OLD_TASKS, tasks)
    _csv(root / "steps.csv", OLD_STEPS, steps)
    _csv(root / "sessions.csv", OLD_SESSIONS, sessions)
    _csv(root / "host_metrics.csv", ["ts", "session_id", "metric", "value"], host)
    _csv(root / "guest_metrics.csv", ["ts", "trial_id", "session_id", "task_id", "metric", "value"], guest)
    (root / "smoke-state.json").write_text(json.dumps({"consecutive_green": 1, "runs": [{"green": True}]}))
    (root / "run.json").write_text(json.dumps({
        "run_id": "old", "backend": "firecracker",
        "criteria": {"step_p50_target_ms": 100.0, "step_p95_target_ms": None, "task_p95_target_ms": None},
        "inputs": {"ladder": [1, 2, 4], "repeats": 1, "confirm_repeats": 1, "warmup": 1,
                   "options": {"n": "1,2,4", "repeats": 1, "confirm_repeats": 1},
                   "host_info": {"cpu_count": 16}, "guest_info": {"guestd_version": "old"}},
        "plan": {"ladder": [1, 2, 4], "repeats": 1, "confirm_repeats": 1, "stop_at_first_miss": True,
                 "levels_run": [
                     {"level": 1, "trials": ["t002-firecracker-n1-r1"], "ladder_trials": 1, "confirm_trials": 0,
                      "ladder_passed": True, "passed": True},
                     {"level": 2, "trials": ["t003-firecracker-n2-r1", "t004-firecracker-n2-r2"], "ladder_trials": 1,
                      "confirm_trials": 1, "ladder_passed": False, "passed": False}],
                 "levels_not_run": [4],
                 "confirmations": [{"level": 2, "trials": ["t004-firecracker-n2-r2"], "passed": True}],
                 "boundary": {"last_pass": 1, "first_miss": 2, "last_pass_trials": 1, "first_miss_trials": 2},
                 "stop_reason": "first_miss", "complete": True}}))
    return root


def test_an_old_run_directory_is_read_in_the_new_names(tmp_path):
    old = write_old_run(tmp_path / "old")
    before = _digest(old)
    rd = RunDir(old)
    assert rd.legacy

    # trial ids: counting trials numbered within their density, the rest labelled, execution order kept
    ids = {k: v["trial_id"] for k, v in rd.legacy_trial_ids().items()}
    assert ids == {"t001-warmup-n1-r1": "warmup", "t002-firecracker-n1-r1": "d1-t1",
                   "t003-firecracker-n2-r1": "d2-t1", "t004-firecracker-n2-r2": "d2-t2", "t005-case-idle": "case-idle"}
    trials = rd.list_trials()
    assert [(t["trial_id"], t["sequence"], t["trial_kind"], t["density"], t["trial_number"]) for t in trials] == [
        ("warmup", 1, "warmup", 1, None), ("d1-t1", 2, "ladder", 1, 1), ("d2-t1", 3, "ladder", 2, 1),
        ("d2-t2", 4, "boundary", 2, 2)]
    t = trials[2]
    assert t["legacy_trial_id"] == "t003-firecracker-n2-r1"
    assert t["counts"]["microvms_ready"] == 2 and t["percentiles"]["task_ms"]["count"] == 2
    assert t["microvms"][0]["microvm_id"] == "s000-00000002" and t["microvms"][0]["task"]["task_id"] == "d2-t1-slot000"
    # the old flag only said the protocol held; it is never read as a pass
    assert t["protocol_ok"] is True and t["passed"] is None
    old_root_keys = {k for k in ALIASES["trial_json"] if "." not in k and "[" not in k}
    assert not old_root_keys & set(t)

    tasks = rd.read_rows("tasks")
    assert {(r["trial_id"], r["density"], r["trial_number"], r["trial_kind"]) for r in tasks} == {
        ("warmup", "1", "", "warmup"), ("d1-t1", "1", "1", "ladder"), ("d2-t1", "2", "1", "ladder"),
        ("d2-t2", "2", "2", "boundary")}
    assert all(r["task_id"].startswith(r["trial_id"] + "-slot") and r["microvm_id"] for r in tasks)
    assert {r["trial_id"] for r in rd.read_rows("steps")} == {"warmup", "d1-t1", "d2-t1", "d2-t2"}
    assert rd.read_rows("microvms")[0]["microvm_id"] == "s000-00000000"
    assert {r["subject"] for r in rd.read_rows("host_metrics")} >= {"host", "s000-00000001"}
    assert "cpu_hypervisor_usec" in {r["metric"] for r in rd.read_rows("host_metrics")}
    assert rd.read_rows("guest_metrics")[0]["microvm_id"] and rd.read_rows("guest_metrics")[0]["trial_id"] == "warmup"

    run = rd.read_run_json()
    assert run["spec"]["densities"] == [1, 2, 4] and run["spec"]["boundary_trials"] == 1
    assert run["spec"]["options"] == {"densities": "1,2,4", "trials_per_density": 1, "boundary_trials": 1}
    assert run["observed"] == {"host_info": {"cpu_count": 16}, "guest_info": {"guestd_version": "old"}}
    plan = run["plan"]
    assert plan["densities"] == [1, 2, 4] and plan["densities_not_run"] == [4]
    assert plan["densities_run"][1] == {"density": 2, "trials": ["d2-t1", "d2-t2"], "ladder_trial_count": 1,
                                        "boundary_trial_count": 1, "ladder_passed": False, "passed": False}
    assert plan["boundary_checks"] == [{"density": 2, "trials": ["d2-t2"], "passed": True}]
    assert rd.read_smoke_state()["rounds"] == [{"green": True}]
    case = rd.list_cases()[0]
    assert (case["trial_id"], case["microvm_id"], case["create_request"], case["microvm"]["outcome"]) == (
        "case-idle", "s000-cccccccc", {"count": 1}, "idle_expired")

    # the report re-evaluates every trial against run.json's criteria, in the new names
    rep, md = run_report(str(old), out_dir=str(tmp_path / "report"), price_per_hour=1.0, instance=None,
                         step_target_ms=None, step_p95_ms=None, task_target_ms=None)
    assert rep["read_from_older_schema"] and "translated on read" in md
    h = rep["headline"]["firecracker"]
    assert h["highest_density_tested_successfully"] == 1 and h["density_per_host_vcpu"] == 1 / 16
    d = {x["density"]: x for x in rep["densities"]}
    assert d[1]["passed"] and not d[2]["passed"] and d[2]["trial_kinds"] == {"ladder": 1, "boundary": 1}
    assert [e["reasons"] for e in d[2]["trial_evaluations"]] == [["step home p50 150 ms > target 100 ms"], []]
    a = d[2]["attribution"][0]
    assert a["hypervisor_s"] == pytest.approx(0.4) and [m["microvm_id"] for m in a["microvms"]] == [
        "s000-00000002", "s001-00000002"]
    assert [t["trial_id"] for t in rep["excluded_trials"]] == ["warmup"]

    # nothing in the old directory changed
    assert _digest(old) == before


def test_the_driver_never_writes_into_an_old_run_directory(tmp_path):
    old = write_old_run(tmp_path / "old")
    before = _digest(old)
    dead = ["--host-url", "http://127.0.0.1:9", "--fixture-check-url", "http://127.0.0.1:9", "--quiet", "--no-lgtm"]
    assert run_cli("trial", "--backend", "docker", "--out", str(old), *dead, "--densities", "1") == 2
    assert run_cli("smoke", "--backend", "docker", "--out", str(old), *dead) == 2
    assert run_cli("bundle", "--run", str(old), "--quiet") == 2
    assert run_cli("report", "--run", str(old), "--quiet") == 2
    with pytest.raises(LegacyRunDirError):
        run_report(str(old), price_per_hour=None, instance=None, step_target_ms=None, step_p95_ms=None,
                   task_target_ms=None)
    with pytest.raises(LegacyRunDirError):
        RunDir(old).open_writers()
    assert _digest(old) == before
    assert run_cli("report", "--run", str(old), "--out", str(tmp_path / "rep"), "--quiet") == 0
    assert read_json(tmp_path / "rep" / "report.json")["densities"]
    assert _digest(old) == before


def test_trial_id_map_edge_cases():
    m = trial_id_map([
        ("t001-docker-n2-r1", {"level_n": 2, "kind": "ladder"}, False),
        ("t002-docker-n2-r1", {"level_n": 2, "kind": "ladder"}, False),  # a second invocation restarted r
        ("t003-smoke-n4", {"level_n": 4}, False),
        ("t004-fault-hang_step", {"level_n": 1, "fault": "hang_step"}, False),
        ("t005-docker-n1-r1", {"level_n": 1, "fault": "slow_step:2000"}, False),
        ("t006-warmup-n1-r1", {"level_n": 1, "kind": "warmup"}, False),
        ("t007-warmup-n1-r2", {"level_n": 1, "kind": "warmup"}, False),
        ("not-an-old-id", {}, False),
    ])
    assert {k: v["trial_id"] for k, v in m.items()} == {
        "t001-docker-n2-r1": "d2-t1", "t002-docker-n2-r1": "d2-t2", "t003-smoke-n4": "d4-t1",
        "t004-fault-hang_step": "fault-hang_step", "t005-docker-n1-r1": "fault-slow_step",
        "t006-warmup-n1-r1": "warmup", "t007-warmup-n1-r2": "warmup-2"}
    assert m["t003-smoke-n4"]["trial_kind"] == "smoke" and m["t005-docker-n1-r1"]["trial_number"] is None
    assert json.loads(dump_aliases())["values"]["trial_kind"] == {"confirm": "boundary"}


@pytest.mark.skipif(not BASELINE.exists(), reason="results/cap-baseline-1 is not in git; present on the machine that ran it")
def test_cap_baseline_1_reads_as_before(tmp_path):
    """cap-baseline-1 (m8i.4xlarge, nested, Firecracker, 2 vCPU / 2 GiB microVMs): densities 1, 2, 4 and 8
    passed, 12 failed 3 of 3 on the home step's p50 alone, 16 did not run."""
    src = tmp_path / "capacity"
    src.mkdir()
    for name in BASELINE_FILES:
        p = BASELINE / name
        if p.is_dir():
            shutil.copytree(p, src / name)
        elif p.exists():
            shutil.copy2(p, src / name)
    rd = RunDir(src)
    assert rd.legacy
    assert {k[:4]: v["trial_id"] for k, v in rd.legacy_trial_ids().items()} == {
        "t001": "warmup", "t002": "d1-t1", "t003": "d2-t1", "t004": "d4-t1", "t005": "d8-t1", "t006": "d12-t1",
        "t007": "d8-t2", "t008": "d8-t3", "t009": "d12-t2", "t010": "d12-t3", "t011": "illustration"}

    rep, md = run_report(str(src), out_dir=str(tmp_path / "report"), price_per_hour=0.84672, instance=None,
                         step_target_ms=None, step_p95_ms=None, task_target_ms=None)
    assert rep["inputs"]["criteria_source"] == "run.json" and rep["read_from_older_schema"]
    h = rep["headline"]["firecracker"]
    assert h["highest_density_tested_successfully"] == 8 and h["limit_found"]
    assert h["boundary"] == {"last_pass": 8, "last_pass_trials": 3, "first_miss": 12, "first_miss_trials": 3}
    assert h["host_vcpus"] == 16 and h["density_per_host_vcpu"] == 0.5
    assert h["cost_usd_per_1000_tasks"]["execution_only"] == pytest.approx(0.075, abs=0.002)
    assert h["cost_usd_per_1000_tasks"]["observed"] == pytest.approx(0.19, abs=0.005)
    d = {x["density"]: x for x in rep["densities"]}
    assert sorted(d) == [1, 2, 4, 8, 12]
    assert d[8]["trials"] == ["d8-t1", "d8-t2", "d8-t3"] and d[8]["trial_kinds"] == {"ladder": 1, "boundary": 2}
    assert d[8]["passed"] and d[8]["trials_passed"] == 3
    assert not d[12]["passed"] and d[12]["trials_passed"] == 0
    assert [e["reasons"] for e in d[12]["trial_evaluations"]] == [
        ["step home p50 1279 ms > target 1000 ms"], ["step home p50 1063 ms > target 1000 ms"],
        ["step home p50 1563 ms > target 1000 ms"]]
    assert all(a["hypervisor_s"] is not None and len(a["microvms"]) == 12 for a in d[12]["attribution"])
    assert "host_cpu" in d[12]["verdicts"]
    assert [t["trial_id"] for t in rep["excluded_trials"]] == ["warmup", "illustration"]
    assert [t["trial_id"] for t in rep["trials"]] == ["warmup", "d1-t1", "d2-t1", "d4-t1", "d8-t1", "d12-t1",
                                                      "d8-t2", "d8-t3", "d12-t2", "d12-t3", "illustration"]
    plan = rep["plan"]
    assert plan["densities"] == [1, 2, 4, 8, 12, 16] and plan["densities_not_run"] == [16]
    assert plan["boundary_checks"] == [{"density": 8, "trials": ["d8-t2", "d8-t3"], "passed": True},
                                       {"density": 12, "trials": ["d12-t2", "d12-t3"], "passed": False}]
    assert "### firecracker density 12 (3 trial(s): d12-t1, d12-t2, d12-t3)" in md

    # hostd's state records, in the old event names, still name every microVM of a trial
    t = next(x for x in rd.list_trials() if x["trial_id"] == "d8-t1")
    assert microvm_state_ids([src / "hostd-logs.jsonl"], t["trace_id"]) == {m["microvm_id"] for m in t["microvms"]}
    assert not (src / "report.json").exists()  # the report went to --out, not into the old directory
