"""The rules in DATA.md, as the builder applies them."""
import rules as R


def t(density, passed, role="ladder"):
    return {"density": density, "passed": passed, "counts": role in R.COUNTING}


def test_rule_1_a_trial_passes_only_on_every_criterion():
    ok = [{"met": True}, {"met": True}]
    assert R.trial_passes(True, 8, 8, ok, True)
    assert not R.trial_passes(False, 8, 8, ok, True)
    assert not R.trial_passes(True, 7, 8, ok, True)
    assert not R.trial_passes(True, 8, 8, [{"met": True}, {"met": False}], True)
    assert not R.trial_passes(True, 8, 8, ok, False)


def test_rule_3_and_4_with_boundary_trials():
    trials = [t(1, True), t(2, True), t(4, True), t(8, True), t(12, False),
              t(8, True, "boundary"), t(8, True, "boundary"), t(12, False, "boundary"), t(12, False, "boundary"),
              t(1, None, "warmup")]
    briefs = R.density_briefs([1, 2, 4, 8, 12, 16], trials)
    assert [(b["density"], b["result"], b["passed"], b["trials"]) for b in briefs] == [
        (1, "passed", 1, 1), (2, "passed", 1, 1), (4, "passed", 1, 1), (8, "passed", 3, 3), (12, "failed", 0, 3),
        (16, "not_tested", 0, 0)]
    assert R.result_core(briefs) == {"tested_successfully": 8, "first_failed": 12, "gap": [9, 11],
                                     "not_tested": [16]}


def test_rule_4_walks_down_when_a_boundary_trial_fails():
    """One boundary trial fails at 4, so 4 fails and the run's result is 3."""
    trials = [t(1, True), t(2, True), t(3, True), t(4, True), t(6, False),
              t(4, False, "boundary"), t(3, True, "boundary"), t(3, True, "boundary")]
    core = R.result_core(R.density_briefs([1, 2, 3, 4, 6], trials))
    assert core == {"tested_successfully": 3, "first_failed": 4, "gap": None, "not_tested": []}


def test_rule_4_when_the_lowest_density_fails_or_nothing_fails():
    assert R.result_core(R.density_briefs([1, 2], [t(1, False)]))["tested_successfully"] is None
    core = R.result_core(R.density_briefs([1, 2], [t(1, True), t(2, True)]))
    assert core["first_failed"] is None and core["gap"] is None


def test_rule_5_cost():
    # 12 tasks sharing 3.8078 s of an m8i.4xlarge at $0.84672/h: $0.0746 per 1,000 tasks
    assert round(R.cost_per_1000(0.84672, 3.807783842086792, 12), 4) == 0.0746


def test_verdicts_follow_the_rule_and_severity():
    rds = [{"key": "host_cpu_pressure_pct", "verdict": "host_cpu", "op": ">=", "threshold": 20},
           {"key": "host_mem_available_fraction", "verdict": "host_memory", "op": "<", "threshold": 0.1},
           {"key": "host_steal_pct", "verdict": "steal", "op": ">=", "threshold": 5}]
    assert R.verdicts_from_rules(rds, {"host_cpu_pressure_pct": 45, "host_mem_available_fraction": 0.05,
                                       "host_steal_pct": 0}) == ["host_cpu", "host_memory"]
    assert R.verdicts_from_rules(rds, {"host_cpu_pressure_pct": 5, "host_mem_available_fraction": 0.8,
                                       "host_steal_pct": 0}) == ["none"]
    assert R.verdicts_from_rules(rds, {"host_cpu_pressure_pct": 5, "host_mem_available_fraction": 0.8}) == ["unknown"]
    assert R.union_verdicts([["none"], ["host_cpu"], ["steal", "host_cpu"]]) == ["host_cpu", "steal"]


def test_the_separating_rule_needs_values_that_dont_overlap_around_its_threshold():
    rds = [{"key": "host_cpu_util_pct", "verdict": "host_cpu", "op": ">=", "threshold": 90},
           {"key": "host_cpu_pressure_pct", "verdict": "host_cpu", "op": ">=", "threshold": 20}]
    passing = {"host_cpu_util_pct": [96.0, 78.8, 96.2], "host_cpu_pressure_pct": [5.5, 10.6, 9.0]}
    failing = {"host_cpu_util_pct": [95.4, 89.98, 95.5], "host_cpu_pressure_pct": [45.5, 34.5, 46.5]}
    assert R.separating_rule(rds, passing, failing)["key"] == "host_cpu_pressure_pct"


def test_precision():
    assert R.r4(89.98374) == 89.9837 and R.r4(1000.0) == 1000 and R.r4(None) is None
    assert R.sig3(0.000123456) == 0.000123 and R.sig3(1234.5) == 1230 and R.sig3(0.0) == 0


def test_rule_6_midpoints_d60():
    """A replica's span runs from its result to its first failure, per host vCPU; the spec's midpoint is the mean."""
    assert R.midpoint(8, 12, 16) == 0.625
    assert R.midpoint(None, 1, 8) == 0.0625        # nothing passed: the span starts at 0
    assert R.midpoint(16, None, 16) is None         # no failure: "at least 16", no midpoint
    assert R.mean_midpoint([0.625, 0.5625]) == 0.59375
    assert R.mean_midpoint([0.625, None]) is None and R.mean_midpoint([]) is None


def test_rule_10_full_size_screenshots_at_the_last_pass_the_first_failure_and_the_illustration():
    def tr(density, number, passed, role="ladder"):
        tid = f"d{density}-t{number}" if role in R.COUNTING else role
        return {"id": tid, "density": density, "number": number, "role": role, "counts": role in R.COUNTING,
                "passed": passed if role in R.COUNTING else None}
    # walked down: trial 1 at 4 passed, trial 3 failed; the result is 3, the first failure trial 3 at 4
    walked_down = [tr(1, 1, True), tr(2, 1, True), tr(3, 1, True), tr(4, 1, True), tr(6, 1, False),
                   tr(4, 2, True, "boundary"), tr(4, 3, False, "boundary"), tr(6, 2, False, "boundary"),
                   tr(6, 3, False, "boundary"), tr(3, 2, True, "boundary"), tr(3, 3, True, "boundary")]
    warmup, illustration = tr(1, None, None, "warmup"), tr(1, None, None, "illustration")
    assert R.full_size_trials([warmup, *walked_down, illustration], 3, 4) == {"d3-t1", "d4-t3", "illustration"}
    assert R.full_size_trials([tr(1, 1, True), tr(2, 1, True)], 2, None) == {"d2-t1"}
    assert R.full_size_trials([warmup, tr(1, 1, False)], None, 1) == {"d1-t1"}
