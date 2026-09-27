from __future__ import annotations

import csv

import pytest

from driver import schemas
from driver.attribution import (attribute_trial, delta, index_host_metrics, sum_guest_metrics,
                                summarize_verdicts, value_at)
from driver.metrics import HostMetricsSampler
from driver.outputs import RunDir, read_csv
from driver.report import _headline

T0, T1 = 102.0, 106.0  # task window: 4 s
TRIAL = {"trial_id": "t001-firecracker-n2-r1", "timestamps": {"barrier_release": T0, "last_task_return": T1},
         "sessions": [{"session_id": "s1", "ready": True}, {"session_id": "s2", "ready": True},
                      {"session_id": "s3", "ready": False}]}


def host_rows(cpu=95.0, psi_cpu_rate=0.30, mem_frac=0.5, steal=1.0, psi_mem_rate=0.0, psi_io_rate=0.0,
              throttle_rate=0.20, sessions=("s1", "s2")):
    """5 Hz samples from t=100 to t=110 of counters growing at fixed rates."""
    rows = []
    for i in range(51):
        ts = 100.0 + i * 0.2
        up = ts - 100.0
        add = lambda sid, m, v: rows.append({"ts": ts, "session_id": sid, "metric": m, "value": v})  # noqa: E731
        add("host", "cpu_util", cpu)
        add("host", "steal", steal)
        add("host", "cpu_count", 16)
        add("host", "mem_total", 64e9)
        add("host", "mem_available", 64e9 * mem_frac)
        add("host", "psi_cpu_some_total", 1e9 + up * psi_cpu_rate * 1e6)
        add("host", "psi_memory_some_total", up * psi_mem_rate * 1e6)
        add("host", "psi_io_some_total", up * psi_io_rate * 1e6)
        add("host", "hostd_cpu_usec", 5e6 + up * 0.1e6)
        add("driver", "driver_cpu_usec", 2e6 + up * 0.05e6)
        for sid in sessions:
            add(sid, "cpu_vcpu_usec", up * 1.5e6)
            add(sid, "cpu_vmm_usec", up * 0.1e6)
            add(sid, "cpu_throttled_usec", up * throttle_rate * 1e6)
            add(sid, "cpu_pressure_some_total_us", up * 0.25e6)
            add(sid, "cgroup_memory_peak", 1.8e9 + (1e6 if sid == "s2" else 0))
    return rows


def guest_rows():
    rows = []
    for task in ("a", "b"):
        for k in range(5):
            base = {"ts": T0 + k * 0.2, "trial_id": TRIAL["trial_id"], "session_id": "s1", "task_id": task}
            rows += [{**base, "metric": "cpu_total_ms", "value": 100}, {**base, "metric": "cpu_idle_ms", "value": 300},
                     {**base, "metric": "cpu_ms.renderer", "value": 60}, {**base, "metric": "cpu_ms.browser", "value": 20},
                     {**base, "metric": "rss_bytes.renderer", "value": 1e8}]
    return rows


def test_attribution_numbers_and_verdicts():
    a = attribute_trial(TRIAL, index_host_metrics(host_rows()), sum_guest_metrics(guest_rows())[TRIAL["trial_id"]])
    assert a["labels"] == {"numbers": "measured", "verdict": "rule-based, from measured signals"}
    assert a["window"]["seconds"] == pytest.approx(4.0)
    h = a["host"]
    assert h["cpu_util_mean_pct"] == pytest.approx(95.0) and h["cpu_util_max_pct"] == pytest.approx(95.0)
    assert h["psi_cpu_some_pct"] == pytest.approx(30.0) and h["psi_memory_some_pct"] == pytest.approx(0.0)
    assert h["mem_available_min_fraction"] == pytest.approx(0.5) and h["cpu_count"] == 16
    assert h["busy_cpu_s"] == pytest.approx(0.95 * 16 * 4)
    # only the ready sessions; s3 never became ready
    assert [s["session_id"] for s in a["sessions"]] == ["s1", "s2"]
    s1 = a["sessions"][0]
    assert s1["vcpu_s"] == pytest.approx(6.0) and s1["vmm_s"] == pytest.approx(0.4) and s1["cpu_s"] == pytest.approx(6.4)
    assert s1["throttled_fraction"] == pytest.approx(0.20) and s1["cgroup_cpu_pressure_some_pct"] == pytest.approx(25.0)
    assert a["sessions"][1]["cgroup_memory_peak_bytes"] == pytest.approx(1.801e9)
    assert a["vcpu_s"] == pytest.approx(12.0) and a["vmm_s"] == pytest.approx(0.8)
    assert a["hostd_cpu_s"] == pytest.approx(0.4) and a["driver_cpu_s"] == pytest.approx(0.2)
    assert a["unattributed_cpu_s"] == pytest.approx(60.8 - 12.8 - 0.4 - 0.2)
    g = a["guest"]
    assert g["busy_cpu_s"] == pytest.approx(1.0) and g["idle_cpu_s"] == pytest.approx(3.0) and g["samples"] == 10
    assert g["top_group"] == "renderer" and g["top_group_share"] == pytest.approx(0.6)
    assert g["groups"]["browser"] == {"cpu_s": pytest.approx(0.2), "share": pytest.approx(0.2)}
    assert list(g["groups"]) == ["renderer", "browser"]  # largest first
    # most severe first
    assert a["verdicts"] == ["host_cpu", "vm_cpu_quota"] and a["verdict"] == "host_cpu" and a["missing"] == []


@pytest.mark.parametrize("kw,expected", [
    (dict(cpu=50, psi_cpu_rate=0.05, throttle_rate=0.01), ["none"]),
    (dict(cpu=50, psi_cpu_rate=0.25, throttle_rate=0.01), ["host_cpu"]),
    (dict(cpu=50, psi_cpu_rate=0.05, throttle_rate=0.15), ["vm_cpu_quota"]),
    (dict(cpu=50, psi_cpu_rate=0.05, throttle_rate=0.01, mem_frac=0.05), ["host_memory"]),
    (dict(cpu=50, psi_cpu_rate=0.05, throttle_rate=0.01, psi_mem_rate=0.06), ["host_memory"]),
    (dict(cpu=50, psi_cpu_rate=0.05, throttle_rate=0.01, psi_io_rate=0.2), ["io"]),
    (dict(cpu=50, psi_cpu_rate=0.05, throttle_rate=0.01, steal=7), ["steal"]),
    (dict(cpu=92, psi_cpu_rate=0.05, throttle_rate=0.01, steal=7, psi_io_rate=0.2), ["host_cpu", "io", "steal"]),
])
def test_verdict_rules(kw, expected):
    a = attribute_trial(TRIAL, index_host_metrics(host_rows(**kw)))
    assert a["verdicts"] == expected


def test_missing_data_gives_nulls_and_unknown():
    # docker-like: no per-VM counters, no steal, no PSI
    rows = [r for r in host_rows(cpu=50) if r["session_id"] == "host"
            and r["metric"] in ("cpu_util", "mem_available", "mem_total")]
    a = attribute_trial(TRIAL, index_host_metrics(rows))
    assert a["verdicts"] == ["unknown"]
    assert a["host"]["psi_cpu_some_pct"] is None and a["sessions"][0]["vcpu_s"] is None
    assert a["sessions_cpu_s"] is None and a["unattributed_cpu_s"] is None and a["host"]["busy_cpu_s"] is None
    assert "steal_mean_pct" in a["missing"] and "vm_throttled_fraction_mean" in a["missing"]
    assert a["guest"]["top_group"] is None
    # a rule that fires is still reported when other signals are missing
    rows = [r for r in host_rows(cpu=97) if r["session_id"] == "host" and r["metric"] == "cpu_util"]
    assert attribute_trial(TRIAL, index_host_metrics(rows))["verdicts"] == ["host_cpu"]
    # no task window at all (an old or aborted trial)
    a = attribute_trial({"timestamps": {"barrier_release": None}}, {})
    assert a["verdict"] == "unknown" and a["window"]["seconds"] is None
    assert summarize_verdicts([a, {"verdicts": ["steal"]}, {"verdicts": ["host_cpu"]}]) == ["host_cpu", "steal"]
    assert summarize_verdicts([{"verdicts": ["none"]}]) == ["none"]
    assert summarize_verdicts([a]) == ["unknown"]


def test_counter_edges_interpolate_and_clamp():
    s = [(10.0, 100.0), (11.0, 200.0), (12.0, 400.0)]
    assert value_at(s, 10.5) == pytest.approx(150.0) and value_at(s, 11.5) == pytest.approx(300.0)
    assert value_at(s, 9.0) == 100.0 and value_at(s, 13.5) == 400.0  # within the 2 s clamp
    assert value_at(s, 5.0) is None and value_at(s, 20.0) is None and value_at([], 1.0) is None
    assert delta(s, 10.5, 11.5) == pytest.approx(150.0)
    assert delta([(1.0, 500.0), (2.0, 10.0)], 1.0, 2.0) is None  # counter reset


def test_headline_is_the_highest_n_with_every_lower_level_passing():
    rows = [{"backend": "firecracker", "level_n": n, "passed": p, "repeats": r}
            for n, p, r in ((1, True, 1), (2, True, 3), (4, False, 3), (8, True, 1))]
    h = _headline(rows)["firecracker"]
    assert h["highest_n_tested_successfully"] == 2 and h["limit_found"]
    assert h["boundary"] == {"last_pass": 2, "last_pass_trials": 3, "first_miss": 4, "first_miss_trials": 3}
    h = _headline([{"backend": "docker", "level_n": 1, "passed": False, "repeats": 2}])["docker"]
    assert h["highest_n_tested_successfully"] == 0 and h["boundary"]["last_pass"] is None and h["limit_found"]
    h = _headline([{"backend": "docker", "level_n": 4, "passed": True, "repeats": 1}])["docker"]
    assert h["highest_n_tested_successfully"] == 4 and not h["limit_found"]


class _FakeClient:
    def __init__(self, ts_seq):
        self.ts_seq = list(ts_seq)

    def host_metrics(self, timeout_s=1.0):
        ts = self.ts_seq.pop(0)
        return {"ts": ts, "cpu_util": 10.0, "cpu_count": 4, "hostd_cpu_usec": 5, "hostd_rss_bytes": 7, "steal": None,
                "sessions": [{"id": "s1", "cpu_vcpu_usec": 3, "cpu_throttled_usec": None, "rss_bytes": 1}]}


class _ListWriter:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def append_many(self, rows):
        self.rows.extend(rows)


def test_sampler_skips_repeated_samples_and_writes_its_own_figures():
    w = _ListWriter()
    smp = HostMetricsSampler(_FakeClient([1.0, 1.0, 1.2, 1.2, 1.4]), w, interval_s=0.2)
    for _ in range(5):
        smp.tick()
    host_ts = sorted({r["ts"] for r in w.rows if r["session_id"] == "host"})
    assert host_ts == [1.0, 1.2, 1.4] and smp.samples == 3 and smp.repeats == 2
    assert sum(1 for r in w.rows if r["session_id"] == "host" and r["metric"] == "cpu_util") == 3
    metrics = {(r["session_id"], r["metric"]) for r in w.rows}
    assert {("host", "cpu_count"), ("host", "hostd_cpu_usec"), ("host", "hostd_rss_bytes"), ("s1", "cpu_vcpu_usec"),
            ("s1", "rss_bytes")} <= metrics
    assert ("s1", "cpu_throttled_usec") not in metrics and ("host", "steal") not in metrics  # nulls skipped
    driver = [r for r in w.rows if r["session_id"] == "driver"]
    assert {r["metric"] for r in driver} == {"driver_cpu_usec", "driver_rss_bytes"} and len(driver) == 10
    assert all(r["value"] > 0 for r in driver)
    assert smp.cpu_util_between(0, 1e12) == [10.0, 10.0, 10.0]


def test_appending_to_an_old_run_dir_keeps_its_header(tmp_path):
    old = [c for c in schemas.TASKS_COLUMNS if c not in ("timing_valid", "guestd_cpu_ms", "kind")]
    rd = RunDir(tmp_path / "old-run")
    with open(rd.tasks_csv, "w", newline="") as fh:
        csv.writer(fh).writerow(old)
    w = rd.open_writers()
    w.tasks.append({"run_id": "old-run", "trial_id": "t001", "task_id": "x", "ok": True, "kind": "ladder"})
    w.close()
    rows = read_csv(rd.tasks_csv)
    assert list(rows[0]) == old and rows[0]["ok"] == "true" and rows[0]["task_id"] == "x"
    assert (tmp_path / "old-run" / "guest_metrics.csv").read_text().strip() == ",".join(schemas.GUEST_METRICS_COLUMNS)
