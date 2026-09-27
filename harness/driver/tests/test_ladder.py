from __future__ import annotations

from driver.ladder import LadderPlanner, Next, Outcome


def drive(planner: LadderPlanner, verdicts) -> list[tuple[str, int, bool]]:
    """Run the planner to completion; ``verdicts(kind, density, trial_number)`` decides each trial."""
    history: list[Outcome] = []
    seen: dict[int, int] = {}
    for _ in range(100):
        nxt = planner.next(history)
        if nxt is None:
            break
        seen[nxt.density] = seen.get(nxt.density, 0) + 1
        passed = verdicts(nxt.trial_kind, nxt.density, seen[nxt.density])
        history.append(Outcome(nxt.trial_kind, nxt.density, passed, f"d{nxt.density}-t{seen[nxt.density]}"))
    else:
        raise AssertionError("planner did not finish")
    return history


def seq(history):
    return [(o.trial_kind, o.density, o.passed) for o in history]


def upto(limit):
    """Ladder trials pass up to and including ``limit``; boundary trials follow the same rule."""
    return lambda kind, density, i: density <= limit


def test_without_stop_every_density_runs_in_order_with_its_trials():
    p = LadderPlanner([2, 4, 2], trials_per_density=2)
    h = drive(p, lambda *a: False)  # misses do not stop anything without --stop-at-first-miss
    assert [(o.trial_kind, o.density) for o in h] == [("ladder", 2)] * 2 + [("ladder", 4)] * 2 + [("ladder", 2)] * 2
    assert p.summary(h)["stop_reason"] == "ladder_complete"


def test_stop_at_first_miss():
    p = LadderPlanner([1, 2, 4, 8, 12, 16], stop_at_first_miss=True)
    h = drive(p, upto(2))
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, False)]
    s = p.summary(h)
    assert s["stop_reason"] == "first_miss" and s["complete"]
    assert s["boundary"] == {"last_pass": 2, "first_miss": 4, "last_pass_trials": 1, "first_miss_trials": 1}
    assert [(d["density"], d["passed"]) for d in s["densities_run"]] == [(1, True), (2, True), (4, False)]
    assert s["densities_not_run"] == [8, 12, 16] and s["boundary_checks"] == []


def test_a_density_needs_every_trial_to_pass():
    p = LadderPlanner([1, 2, 4], trials_per_density=2, stop_at_first_miss=True)
    h = drive(p, lambda kind, density, i: not (density == 2 and i == 2))
    assert seq(h) == [("ladder", 1, True), ("ladder", 1, True), ("ladder", 2, True), ("ladder", 2, False)]
    assert p.summary(h)["boundary"]["first_miss"] == 2


def test_boundary_trials_at_both_boundary_densities():
    p = LadderPlanner([1, 2, 4, 8], boundary_trials=2, stop_at_first_miss=True)
    # the first miss's boundary trials pass, yet it stays a miss
    h = drive(p, lambda kind, density, i: density <= 2 or kind == "boundary")
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, False),
                      ("boundary", 2, True), ("boundary", 2, True), ("boundary", 4, True), ("boundary", 4, True)]
    s = p.summary(h)
    assert s["boundary"] == {"last_pass": 2, "first_miss": 4, "last_pass_trials": 3, "first_miss_trials": 3}
    assert [(c["density"], len(c["trials"]), c["passed"]) for c in s["boundary_checks"]] == [(2, 2, True), (4, 2, True)]
    d4 = next(d for d in s["densities_run"] if d["density"] == 4)
    assert d4 == {"density": 4, "trials": ["d4-t1", "d4-t2", "d4-t3"], "ladder_trial_count": 1,
                  "boundary_trial_count": 2, "ladder_passed": False, "passed": False}


def test_walk_down_when_a_boundary_trial_at_the_last_pass_fails():
    p = LadderPlanner([1, 2, 4, 8], boundary_trials=2, stop_at_first_miss=True)
    # 4 passes its ladder trial, then one of its boundary trials fails; 2 holds
    h = drive(p, lambda kind, density, i: density <= 2 or (density == 4 and kind == "ladder") or (density == 4 and i == 2))
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, True), ("ladder", 8, False),
                      ("boundary", 4, True), ("boundary", 4, False), ("boundary", 8, False), ("boundary", 8, False),
                      ("boundary", 2, True), ("boundary", 2, True)]
    s = p.summary(h)
    assert s["boundary"]["last_pass"] == 2 and s["boundary"]["first_miss"] == 4
    assert [(d["density"], d["ladder_passed"], d["passed"]) for d in s["densities_run"]] == [
        (1, True, True), (2, True, True), (4, True, False), (8, False, False)]


def test_walk_down_continues_until_a_density_holds_or_none_is_left():
    p = LadderPlanner([1, 2, 4], boundary_trials=1, stop_at_first_miss=True)
    h = drive(p, lambda kind, density, i: kind == "ladder" and density <= 2)
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, False),
                      ("boundary", 2, False), ("boundary", 4, False), ("boundary", 1, False)]
    s = p.summary(h)
    assert s["boundary"] == {"last_pass": None, "first_miss": 1, "last_pass_trials": 0, "first_miss_trials": 2}

    h = drive(p, lambda kind, density, i: density == 1 or (kind == "ladder" and density <= 2))
    assert seq(h)[-1] == ("boundary", 1, True)
    assert p.summary(h)["boundary"]["last_pass"] == 1 and p.summary(h)["boundary"]["first_miss"] == 2


def test_no_first_miss():
    p = LadderPlanner([1, 2, 4], boundary_trials=1, stop_at_first_miss=True)
    h = drive(p, lambda *a: True)
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, True), ("boundary", 4, True)]
    s = p.summary(h)
    assert s["stop_reason"] == "ladder_complete" and s["densities_not_run"] == []
    assert s["boundary"] == {"last_pass": 4, "first_miss": None, "last_pass_trials": 2, "first_miss_trials": 0}
    # the top density's boundary trial fails: it becomes the first miss and the walk-down checks 2
    h = drive(p, lambda kind, density, i: kind == "ladder" or density < 4)
    assert seq(h)[3:] == [("boundary", 4, False), ("boundary", 2, True)]
    assert p.summary(h)["boundary"]["last_pass"] == 2 and p.summary(h)["boundary"]["first_miss"] == 4


def test_density_one_fails():
    p = LadderPlanner([1, 2, 4], boundary_trials=2, stop_at_first_miss=True)
    h = drive(p, lambda *a: False)
    assert seq(h) == [("ladder", 1, False), ("boundary", 1, False), ("boundary", 1, False)]
    s = p.summary(h)
    assert s["boundary"] == {"last_pass": None, "first_miss": 1, "last_pass_trials": 0, "first_miss_trials": 3}
    assert s["densities_not_run"] == [2, 4] and s["stop_reason"] == "first_miss"


def test_without_stop_the_boundary_is_the_first_miss_and_the_pass_below_it():
    p = LadderPlanner([1, 2, 4], boundary_trials=1)
    h = drive(p, lambda kind, density, i: density != 2)
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, False), ("ladder", 4, True),
                      ("boundary", 1, True), ("boundary", 2, False)]
    assert p.summary(h)["boundary"]["last_pass"] == 1


def test_summary_mid_run_and_interrupted():
    p = LadderPlanner([1, 2], stop_at_first_miss=True)
    assert p.next([]) == Next("ladder", 1)
    s = p.summary([])
    assert s["stop_reason"] is None and not s["complete"] and s["densities_run"] == []
    s = p.summary([Outcome("ladder", 1, True, "d1-t1")], interrupted=True)
    assert s["stop_reason"] == "interrupted" and not s["complete"]
