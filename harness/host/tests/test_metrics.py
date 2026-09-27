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
    # the GET /host/metrics shape stays exactly section 4's
    assert set(sample["sessions"][0]) == {"id", "rss_bytes", "cgroup_memory_current", "cgroup_memory_peak", "cpu_usage_usec"}
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
