from __future__ import annotations

from driver.criteria import density_passed, evaluate_trial, has_targets, make_criteria, normalize
from driver.schemas import STEP_NAMES


def _trial(n=2, ready=None, dispatched=None, ok=None, clean=True, complete=True, error=None, tid="d2-t1"):
    return {"trial_id": tid, "density": n, "complete": complete, "error": error, "phase": "done",
            "counts": {"microvms_ready": n if ready is None else ready,
                       "tasks_dispatched": n if dispatched is None else dispatched,
                       "tasks_ok": n if ok is None else ok},
            "verify_clean": {"clean": clean, "leftovers": []}}


def _rows(durations_by_task: dict, task_ms: dict, ok: dict, tid="d2-t1", as_csv=False):
    tasks, steps = [], []
    for task_id, durs in durations_by_task.items():
        tasks.append({"trial_id": tid, "task_id": task_id, "ok": ok[task_id], "task_ms": task_ms[task_id]})
        for name, d in zip(STEP_NAMES, durs):
            steps.append({"trial_id": tid, "task_id": task_id, "name": name, "duration_ms": d})
    if as_csv:  # what read_csv gives the report
        tasks = [{k: ("true" if v is True else "false" if v is False else "" if v is None else str(v))
                  for k, v in r.items()} for r in tasks]
        steps = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in steps]
    return tasks, steps


def test_protocol_and_targets_pass():
    tasks, steps = _rows({"a": [100, 200, 300, 400, 500], "b": [120, 220, 320, 420, 520]},
                         {"a": 1500.0, "b": 1600.0}, {"a": True, "b": True})
    ev = evaluate_trial(_trial(), tasks, steps, make_criteria(1000, 2000, 5000))
    assert ev["passed"] and ev["protocol_ok"] and ev["targets_met"] and ev["reasons"] == []
    names = [c["name"] for c in ev["checks"]]
    assert names[:2] == ["step home p50", "step home p95"] and names[-1] == "task p95"
    assert len(names) == 2 * 5 + 1
    home50 = ev["checks"][0]
    assert home50["value"] == 110.0 and home50["target"] == 1000.0 and home50["met"]


def test_step_p50_miss_is_a_targets_miss():
    tasks, steps = _rows({"a": [100, 900, 300, 400, 500], "b": [120, 1100, 320, 420, 520]},
                         {"a": 1500.0, "b": 1600.0}, {"a": True, "b": True})
    ev = evaluate_trial(_trial(), tasks, steps, make_criteria(step_p50_target_ms=800))
    assert ev["protocol_ok"] and not ev["targets_met"] and not ev["passed"]
    assert [c["name"] for c in ev["checks"] if not c["met"]] == ["step search p50"]
    assert ev["reasons"] == ["step search p50 1000 ms > target 800 ms"]


def test_only_ok_tasks_count_toward_percentiles():
    tasks, steps = _rows({"a": [100, 200, 300, 400, 500], "b": [9000, 9000, 9000, 9000, 9000]},
                         {"a": 1500.0, "b": 45000.0}, {"a": True, "b": False})
    ev = evaluate_trial(_trial(ok=1), tasks, steps, make_criteria(1000, 1000, 2000))
    assert not ev["protocol_ok"] and "tasks ok 1/2" in ev["reasons"]
    assert ev["targets_met"]  # the failed task's slow steps and elapsed time are not in the percentiles
    assert not ev["passed"]


def test_csv_rows_and_typed_rows_agree():
    durs = {"a": [101.5, 202.25, 303.125, 404.0, 505.5], "b": [111.1, 222.2, 333.3, 444.4, 555.5]}
    crit = make_criteria(200, 500, 3000)
    typed = evaluate_trial(_trial(), *_rows(durs, {"a": 1516.4, "b": 1666.5}, {"a": True, "b": True}), crit)
    csvish = evaluate_trial(_trial(), *_rows(durs, {"a": 1516.4, "b": 1666.5}, {"a": True, "b": True}, as_csv=True), crit)
    assert typed == csvish


def test_protocol_rules():
    tasks, steps = _rows({"a": [1] * 5, "b": [1] * 5}, {"a": 5.0, "b": 5.0}, {"a": True, "b": True})
    assert evaluate_trial(_trial(), tasks, steps, None)["passed"]  # no criteria: protocol only
    for kw, reason in ((dict(clean=False), "verify-clean not clean"), (dict(ready=1), "microVMs ready 1/2"),
                       (dict(dispatched=1), "tasks dispatched 1/2"), (dict(complete=False), "trial not complete"),
                       (dict(error="fixture check failed"), "trial error: fixture check failed")):
        ev = evaluate_trial(_trial(**kw), tasks, steps, None)
        assert not ev["passed"] and not ev["protocol_ok"] and any(r.startswith(reason) for r in ev["reasons"]), kw


def test_step_targets_without_step_rows_fail_and_rows_of_other_trials_are_ignored():
    tasks, steps = _rows({"a": [1] * 5, "b": [1] * 5}, {"a": 5.0, "b": 5.0}, {"a": True, "b": True}, tid="d9-t9")
    ev = evaluate_trial(_trial(), tasks, steps, make_criteria(step_p95_target_ms=100, task_p95_target_ms=100))
    assert not ev["targets_met"]
    assert {c["name"] for c in ev["checks"]} == {"steps", "task p95"}
    assert "task p95: no data from ok tasks" in ev["reasons"]


def test_normalize_and_density_rule():
    assert normalize(None) == {"step_p50_target_ms": None, "step_p95_target_ms": None, "task_p95_target_ms": None}
    assert normalize({"step_p50_target_ms": "1000", "junk": 1})["step_p50_target_ms"] == 1000.0
    assert not has_targets(None) and has_targets({"task_p95_target_ms": 5})
    assert not density_passed([])
    assert density_passed([{"passed": True}, {"passed": True}])
    assert not density_passed([{"passed": True}, {"passed": False}])
    ev = evaluate_trial({"trial_id": "x", "complete": True, "counts": {}, "verify_clean": {"clean": True}}, [], [], None)
    assert "no density recorded" in ev["reasons"]
