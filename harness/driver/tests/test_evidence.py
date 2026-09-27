from __future__ import annotations

import json

from driver.evidence import iter_logs, logs_for_trace, session_state_ids

# A session.state record exactly as hostd/telemetry.py log() writes it (taken from a local smoke run).
HOSTD_LINE = (
    '{"ts":1790492796.38895,"severity":"INFO","service":"hostd","body":"session.state",'
    '"trace_id":"1419b0fbeb9e180e9b2845e92df459d4","span_id":"0b9a2acbe7d8fb61",'
    '"attributes":{"fleetkit.host_id":"mac-dev","fleetkit.backend":"docker","service.version":"0.1.0",'
    '"fleetkit.run_id":"smoke-trial","fleetkit.trial_id":"t001-docker-n2-r1","fleetkit.session_id":"s000-07dd22c0",'
    '"fleetkit.slot":0,"session_id":"s000-07dd22c0","from":null,"to":"creating","ts":1790492796.388923,'
    '"outcome":null,"event":"session.state"}}'
)
# hostd's startup line: no trace context at all
HOSTD_NO_TRACE = (
    '{"ts":1790492790.270432,"severity":"INFO","service":"hostd","body":"hostd 0.1.0 listening on '
    'http://127.0.0.1:8090 backend=docker","trace_id":null,"span_id":null,'
    '"attributes":{"fleetkit.host_id":"mac-dev","fleetkit.backend":"docker","service.version":"0.1.0"}}'
)
TRACE = "1419b0fbeb9e180e9b2845e92df459d4"


def test_iter_logs_reads_hostd_shape(tmp_path):
    p = tmp_path / "logs.jsonl"
    p.write_text(HOSTD_LINE + "\n" + HOSTD_NO_TRACE + "\n")
    recs = list(iter_logs(p))
    assert len(recs) == 2
    first = recs[0]
    assert first["service"] == "hostd" and first["trace_id"] == TRACE and first["message"] == "session.state"
    assert first["attributes"]["event"] == "session.state" and first["attributes"]["session_id"] == "s000-07dd22c0"
    assert recs[1]["trace_id"] == "" and recs[1]["message"].startswith("hostd 0.1.0 listening")
    assert logs_for_trace([p], TRACE) == {"hostd": 1}
    assert logs_for_trace([p], TRACE.upper()) == {"hostd": 1}


def test_session_state_ids_by_trace(tmp_path):
    p = tmp_path / "logs.jsonl"
    other = json.loads(HOSTD_LINE)
    other["trace_id"] = "ff" * 16
    other["attributes"]["session_id"] = other["attributes"]["fleetkit.session_id"] = "s001-other"
    plain = json.loads(HOSTD_LINE)
    plain["body"] = "task proxied"
    plain["attributes"].pop("event")
    plain["attributes"]["session_id"] = "s002-noevent"
    # the older flat shape (message + attributes) still counts
    legacy = {"ts": 1.0, "level": "INFO", "service": "hostd", "message": "session.state", "trace_id": TRACE,
              "span_id": "", "attributes": {"session_id": "s003-legacy", "from": "ready", "to": "destroying"}}
    p.write_text("\n".join(json.dumps(x) for x in (json.loads(HOSTD_LINE), other, plain, legacy)) + "\n")
    assert session_state_ids([p], TRACE) == {"s000-07dd22c0", "s003-legacy"}
    assert session_state_ids([p], "ff" * 16) == {"s001-other"}
    assert session_state_ids([p], "00" * 16) == set()


def test_iter_logs_skips_metrics_and_garbage(tmp_path):
    p = tmp_path / "logs.jsonl"
    p.write_text('{"resourceMetrics": []}\nnot json\n[1,2]\n' + HOSTD_LINE + "\n")
    assert len(list(iter_logs(p))) == 1
