from __future__ import annotations

from driver.ladder import LadderPlanner, Next, Outcome


def drive(planner: LadderPlanner, verdicts) -> list[tuple[str, int, bool]]:
    """Run the planner to completion; ``verdicts(kind, level, index_at_level)`` decides each trial."""
    history: list[Outcome] = []
    seen: dict[int, int] = {}
    for _ in range(100):
        nxt = planner.next(history)
        if nxt is None:
            break
        seen[nxt.level] = seen.get(nxt.level, 0) + 1
        passed = verdicts(nxt.kind, nxt.level, seen[nxt.level])
        history.append(Outcome(nxt.kind, nxt.level, passed, f"t{len(history) + 1:03d}-n{nxt.level}"))
    else:
        raise AssertionError("planner did not finish")
    return history


def seq(history):
    return [(o.kind, o.level, o.passed) for o in history]


def upto(limit):
    """Ladder trials pass up to and including ``limit``; confirmations follow the same rule."""
    return lambda kind, level, i: level <= limit


def test_legacy_runs_every_level_in_order_with_repeats():
    p = LadderPlanner([2, 4, 2], repeats=2)
    h = drive(p, lambda *a: False)  # misses do not stop anything without --stop-at-first-miss
    assert [(o.kind, o.level) for o in h] == [("ladder", 2)] * 2 + [("ladder", 4)] * 2 + [("ladder", 2)] * 2
    assert p.summary(h)["stop_reason"] == "ladder_complete"


def test_stop_at_first_miss():
    p = LadderPlanner([1, 2, 4, 8, 12, 16], stop_at_first_miss=True)
    h = drive(p, upto(2))
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, False)]
    s = p.summary(h)
    assert s["stop_reason"] == "first_miss" and s["complete"]
    assert s["boundary"] == {"last_pass": 2, "first_miss": 4, "last_pass_trials": 1, "first_miss_trials": 1}
    assert [(l["level"], l["passed"]) for l in s["levels_run"]] == [(1, True), (2, True), (4, False)]
    assert s["levels_not_run"] == [8, 12, 16] and s["confirmations"] == []


def test_a_level_needs_every_repeat_to_pass():
    p = LadderPlanner([1, 2, 4], repeats=2, stop_at_first_miss=True)
    h = drive(p, lambda kind, level, i: not (level == 2 and i == 2))
    assert seq(h) == [("ladder", 1, True), ("ladder", 1, True), ("ladder", 2, True), ("ladder", 2, False)]
    assert p.summary(h)["boundary"]["first_miss"] == 2


def test_confirm_both_boundary_levels():
    p = LadderPlanner([1, 2, 4, 8], confirm_repeats=2, stop_at_first_miss=True)
    # the first miss's confirmations pass, yet it stays a miss
    h = drive(p, lambda kind, level, i: level <= 2 or kind == "confirm")
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, False),
                      ("confirm", 2, True), ("confirm", 2, True), ("confirm", 4, True), ("confirm", 4, True)]
    s = p.summary(h)
    assert s["boundary"] == {"last_pass": 2, "first_miss": 4, "last_pass_trials": 3, "first_miss_trials": 3}
    assert [(c["level"], len(c["trials"]), c["passed"]) for c in s["confirmations"]] == [(2, 2, True), (4, 2, True)]
    lv4 = next(l for l in s["levels_run"] if l["level"] == 4)
    assert lv4 == {"level": 4, "trials": ["t003-n4", "t006-n4", "t007-n4"], "ladder_trials": 1, "confirm_trials": 2,
                   "ladder_passed": False, "passed": False}


def test_walk_down_when_a_confirmation_of_the_last_pass_fails():
    p = LadderPlanner([1, 2, 4, 8], confirm_repeats=2, stop_at_first_miss=True)
    # 4 passes its ladder trial, then one of its confirmations fails; 2 holds
    h = drive(p, lambda kind, level, i: level <= 2 or (level == 4 and kind == "ladder") or (level == 4 and i == 2))
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, True), ("ladder", 8, False),
                      ("confirm", 4, True), ("confirm", 4, False), ("confirm", 8, False), ("confirm", 8, False),
                      ("confirm", 2, True), ("confirm", 2, True)]
    s = p.summary(h)
    assert s["boundary"]["last_pass"] == 2 and s["boundary"]["first_miss"] == 4
    assert [(l["level"], l["ladder_passed"], l["passed"]) for l in s["levels_run"]] == [
        (1, True, True), (2, True, True), (4, True, False), (8, False, False)]


def test_walk_down_continues_until_a_level_holds_or_none_is_left():
    p = LadderPlanner([1, 2, 4], confirm_repeats=1, stop_at_first_miss=True)
    h = drive(p, lambda kind, level, i: kind == "ladder" and level <= 2)
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, False),
                      ("confirm", 2, False), ("confirm", 4, False), ("confirm", 1, False)]
    s = p.summary(h)
    assert s["boundary"] == {"last_pass": None, "first_miss": 1, "last_pass_trials": 0, "first_miss_trials": 2}

    h = drive(p, lambda kind, level, i: level == 1 or (kind == "ladder" and level <= 2))
    assert seq(h)[-1] == ("confirm", 1, True)
    assert p.summary(h)["boundary"]["last_pass"] == 1 and p.summary(h)["boundary"]["first_miss"] == 2


def test_no_first_miss():
    p = LadderPlanner([1, 2, 4], confirm_repeats=1, stop_at_first_miss=True)
    h = drive(p, lambda *a: True)
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, True), ("ladder", 4, True), ("confirm", 4, True)]
    s = p.summary(h)
    assert s["stop_reason"] == "ladder_complete" and s["levels_not_run"] == []
    assert s["boundary"] == {"last_pass": 4, "first_miss": None, "last_pass_trials": 2, "first_miss_trials": 0}
    # the top level's confirmation fails: it becomes the first miss and the walk-down confirms 2
    h = drive(p, lambda kind, level, i: kind == "ladder" or level < 4)
    assert seq(h)[3:] == [("confirm", 4, False), ("confirm", 2, True)]
    assert p.summary(h)["boundary"]["last_pass"] == 2 and p.summary(h)["boundary"]["first_miss"] == 4


def test_level_one_fails():
    p = LadderPlanner([1, 2, 4], confirm_repeats=2, stop_at_first_miss=True)
    h = drive(p, lambda *a: False)
    assert seq(h) == [("ladder", 1, False), ("confirm", 1, False), ("confirm", 1, False)]
    s = p.summary(h)
    assert s["boundary"] == {"last_pass": None, "first_miss": 1, "last_pass_trials": 0, "first_miss_trials": 3}
    assert s["levels_not_run"] == [2, 4] and s["stop_reason"] == "first_miss"


def test_without_stop_the_boundary_is_the_first_miss_and_the_pass_below_it():
    p = LadderPlanner([1, 2, 4], confirm_repeats=1)
    h = drive(p, lambda kind, level, i: level != 2)
    assert seq(h) == [("ladder", 1, True), ("ladder", 2, False), ("ladder", 4, True),
                      ("confirm", 1, True), ("confirm", 2, False)]
    assert p.summary(h)["boundary"]["last_pass"] == 1


def test_summary_mid_run_and_interrupted():
    p = LadderPlanner([1, 2], stop_at_first_miss=True)
    assert p.next([]) == Next("ladder", 1)
    s = p.summary([])
    assert s["stop_reason"] is None and not s["complete"] and s["levels_run"] == []
    s = p.summary([Outcome("ladder", 1, True, "t1")], interrupted=True)
    assert s["stop_reason"] == "interrupted" and not s["complete"]
