"""Host metrics sampler: the exported points carry the section 8 correlation keys."""
from __future__ import annotations

import json

from hostd.manager import RequestContext
from hostd.metrics import HostSampler
from hostd.telemetry import parse_baggage


def test_metric_points_carry_correlation_keys(manager, guest, tel, ctx):
    guest.ready.add("guest-0")
    rctx = RequestContext(baggage=parse_baggage("fleetkit.run_id=run-9,fleetkit.trial_id=trial-3"))
    sid = manager.create_sessions({"count": 1}, rctx)[0]["id"]
    sampler = HostSampler(manager, tel, host_id="test-host")
    sample = sampler.sample_once()
    # the GET /host/metrics shape keeps section 4's keys and adds the capacity-experiment ones
    assert set(sample["sessions"][0]) == {"id", "rss_bytes", "cgroup_memory_current", "cgroup_memory_peak", "cpu_usage_usec",
                                          "cpu_vcpu_usec", "cpu_vmm_usec", "cpu_throttled_usec", "cpu_nr_throttled",
                                          "cpu_pressure_some_total_us", "cpu_pressure_full_total_us",
                                          "memory_pressure_some_total_us"}
    tel.close()
    recs = [json.loads(l) for l in open(tel.log_dir + "/host_metrics.jsonl")]
    points = [p for r in recs for p in r["points"]]
    host_points = [p for p in points if p["name"].startswith("fleetkit.host.")]
    assert host_points, "no host gauges exported"
    for p in host_points:
        assert p["attributes"]["fleetkit.host_id"] == "test-host"
        assert p["attributes"]["fleetkit.backend"] == "docker"
        assert "host_id" not in p["attributes"] and "session_id" not in p["attributes"]
    # the fake backend reports no per-session figures (all None -> dropped), so drive the
    # export with figures to check the session keys
    sample["sessions"][0].update({"rss_bytes": 1, "cgroup_memory_current": 2, "cgroup_memory_peak": 3, "cpu_usage_usec": 4})
    tel2_points = []
    sampler.telemetry = type("T", (), {"gauges": lambda self, pts, ts_ns=None: tel2_points.extend(pts)})()
    sampler._export(sample, {sid: {"fleetkit.run_id": "run-9", "fleetkit.trial_id": "trial-3", "fleetkit.backend": "docker"}})
    session_points = [p for p in tel2_points if p["name"].startswith("fleetkit.session.")]
    assert [p["name"] for p in session_points] == ["fleetkit.session.rss_bytes", "fleetkit.session.cgroup_memory_current",
                                                   "fleetkit.session.cgroup_memory_peak", "fleetkit.session.cpu_usage_usec"]
    for p in session_points:
        assert p["attributes"] == {"fleetkit.host_id": "test-host", "fleetkit.backend": "docker",
                                   "fleetkit.session_id": sid, "fleetkit.run_id": "run-9", "fleetkit.trial_id": "trial-3"}


def test_sample_once_passes_session_keys_from_the_session(manager, guest, tel):
    guest.ready.add("guest-0")
    rctx = RequestContext(baggage=parse_baggage("fleetkit.run_id=run-1,fleetkit.trial_id=trial-1"))
    sid = manager.create_sessions({"count": 1}, rctx)[0]["id"]
    seen = {}

    class Recorder(HostSampler):
        def _export(self, sample, session_keys=None):
            seen.update(session_keys or {})

    Recorder(manager, tel, host_id="h").sample_once()
    assert seen == {sid: {"fleetkit.run_id": "run-1", "fleetkit.trial_id": "trial-1", "fleetkit.backend": "docker"}}


def _write(path, text):
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _fake_proc(root):
    _write(root + "/meminfo", "MemTotal:       16000000 kB\nMemFree:  1 kB\nMemAvailable:   12000000 kB\n")
    _write(root + "/stat", "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 100 0 50 800 50 0 0 0 0 0\n")
    _write(root + "/pressure/cpu", "some avg10=1.00 avg60=0.00 avg300=0.00 total=1000\n"
                                   "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    _write(root + "/pressure/memory", "some avg10=0.00 avg60=0.00 avg300=0.00 total=20\n"
                                      "full avg10=0.00 avg60=0.00 avg300=0.00 total=10\n")
    _write(root + "/pressure/io", "some avg10=0.00 avg60=0.00 avg300=0.00 total=30\n"
                                  "full avg10=0.00 avg60=0.00 avg300=0.00 total=15\n")
    _write(root + "/self/status", "Name:\tpython\nVmRSS:\t   40960 kB\n")


def test_host_fields_from_a_fake_proc_root(manager, tel, tmp_path):
    import os
    root = str(tmp_path / "proc")
    _fake_proc(root)
    sampler = HostSampler(manager, tel, host_id="h", proc_root=root)
    first = sampler.sample_once()
    assert first["mem_total"] == 16000000 * 1024 and first["mem_available"] == 12000000 * 1024
    assert first["cpu_util"] is None                     # the first sample only primes the delta
    assert first["psi"]["cpu"]["some_total"] == 1000.0 and first["psi"]["io"]["full_total"] == 15.0
    assert first["cpu_count"] == os.cpu_count()
    assert isinstance(first["hostd_cpu_usec"], int) and first["hostd_cpu_usec"] > 0
    assert first["hostd_rss_bytes"] == 40960 * 1024
    # 200 more jiffies, 100 of them idle -> 50 % busy
    _write(root + "/stat", "cpu  200 0 50 900 50 0 0 0 0 0\n")
    second = sampler.sample_once()
    assert second["cpu_util"] == 50.0 and second["steal"] == 0.0
    assert second["hostd_cpu_usec"] >= first["hostd_cpu_usec"]


def test_new_host_and_session_values_are_exported_as_gauges(manager, guest, tel):
    from hostd.backends.base import SESSION_FIGURES
    guest.ready.add("guest-0")
    sid = manager.create_sessions({"count": 1}, RequestContext())[0]["id"]
    figures = {k: i + 1 for i, k in enumerate(SESSION_FIGURES)}
    manager.backend.sample = lambda s: dict(figures)
    points = []
    sampler = HostSampler(manager, None, host_id="h")
    sampler.telemetry = type("T", (), {"gauges": lambda self, pts, ts_ns=None: points.extend(pts)})()
    sample = sampler.sample_once()
    assert sample["sessions"] == [{"id": sid, **figures}]
    names = [p["name"] for p in points]
    for k in SESSION_FIGURES:
        assert "fleetkit.session.%s" % k in names, k
    for k in ("cpu_count", "hostd_cpu_usec", "hostd_rss_bytes"):
        if sample[k] is not None:
            assert "fleetkit.host.%s" % k in names, k
    vcpu = next(p for p in points if p["name"] == "fleetkit.session.cpu_vcpu_usec")
    assert vcpu["value"] == figures["cpu_vcpu_usec"] and vcpu["attributes"]["fleetkit.session_id"] == sid


def test_a_backend_that_omits_keys_or_fails_still_gives_the_full_shape(manager, guest, tel):
    from hostd.backends.base import SESSION_FIGURES
    guest.ready.add("guest-0")
    sid = manager.create_sessions({"count": 1}, RequestContext())[0]["id"]
    manager.backend.sample = lambda s: {"rss_bytes": 5}
    sample = HostSampler(manager, None, host_id="h").sample_once()
    assert sample["sessions"] == [{"id": sid, **{k: None for k in SESSION_FIGURES}, "rss_bytes": 5}]

    def boom(s):
        raise OSError("cgroup gone")

    manager.backend.sample = boom
    sample = HostSampler(manager, None, host_id="h").sample_once()
    assert sample["sessions"] == [{"id": sid, **{k: None for k in SESSION_FIGURES}}]


def test_metrics_period_flag():
    import pytest
    from hostd.__main__ import build_parser, main
    assert build_parser().parse_args(["--backend", "docker"]).metrics_period == 1.0
    assert build_parser().parse_args(["--backend", "docker", "--metrics-period", "0.2"]).metrics_period == 0.2
    for bad in ("0", "-1"):
        with pytest.raises(SystemExit) as e:
            main(["--backend", "docker", "--dry-run", "--metrics-period", bad])
        assert e.value.code == 2
    sampler = HostSampler(None, None, period=0.2)
    assert sampler.period == 0.2
