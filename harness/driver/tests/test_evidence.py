from __future__ import annotations

import json

from driver import legacy
from driver.evidence import iter_logs, logs_for_trace, microvm_state_ids

# A microvm.state record in the shape hostd/telemetry.py log() writes it.
HOSTD_LINE = (
    '{"ts":1790492796.38895,"severity":"INFO","service":"hostd","body":"microvm.state",'
    '"trace_id":"1419b0fbeb9e180e9b2845e92df459d4","span_id":"0b9a2acbe7d8fb61",'
    '"attributes":{"fleetkit.host_id":"mac-dev","fleetkit.backend":"docker","service.version":"0.1.0",'
    '"fleetkit.run_id":"smoke-trial","fleetkit.trial_id":"d2-t1","fleetkit.microvm_id":"s000-07dd22c0",'
    '"fleetkit.slot":0,"microvm_id":"s000-07dd22c0","from":null,"to":"creating","ts":1790492796.388923,'
    '"outcome":null,"event":"microvm.state"}}'
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
    assert first["service"] == "hostd" and first["trace_id"] == TRACE and first["message"] == "microvm.state"
    assert first["attributes"]["event"] == "microvm.state" and first["attributes"]["microvm_id"] == "s000-07dd22c0"
    assert recs[1]["trace_id"] == "" and recs[1]["message"].startswith("hostd 0.1.0 listening")
    assert logs_for_trace([p], TRACE) == {"hostd": 1}
    assert logs_for_trace([p], TRACE.upper()) == {"hostd": 1}


def _old_names(rec: dict) -> dict:
    """The same record as a hostd from before the glossary wrote it (event and attribute names from the alias map)."""
    old_event = {v: k for k, v in legacy.EVENT_ALIASES.items()}
    old_attr = {v: k for k, v in legacy.LOG_ATTRIBUTE_ALIASES.items()}
    rec = json.loads(json.dumps(rec))
    for key in ("body", "message"):
        if key in rec:
            rec[key] = old_event.get(rec[key], rec[key])
    attrs = rec.get("attributes") or {}
    rec["attributes"] = {old_attr.get(k, k): (old_event.get(v, v) if k == "event" else v) for k, v in attrs.items()}
    return rec


def test_microvm_state_ids_by_trace(tmp_path):
    p = tmp_path / "logs.jsonl"
    other = json.loads(HOSTD_LINE)
    other["trace_id"] = "ff" * 16
    other["attributes"]["microvm_id"] = other["attributes"]["fleetkit.microvm_id"] = "s001-other"
    plain = json.loads(HOSTD_LINE)
    plain["body"] = "task proxied"
    plain["attributes"].pop("event")
    plain["attributes"]["microvm_id"] = "s002-noevent"
    # the older flat shape (message + attributes) still counts
    flat = {"ts": 1.0, "severity": "INFO", "service": "hostd", "message": "microvm.state", "trace_id": TRACE,
            "span_id": "", "attributes": {"microvm_id": "s003-flat", "from": "ready", "to": "destroying"}}
    # and so do records written before the glossary, read through the alias map
    old = json.loads(HOSTD_LINE)
    old["attributes"]["microvm_id"] = old["attributes"]["fleetkit.microvm_id"] = "s004-old"
    old = _old_names(old)
    assert "microvm_id" not in old["attributes"] and old["body"] != "microvm.state"
    old_flat = _old_names({**flat, "attributes": {**flat["attributes"], "microvm_id": "s005-old-flat"}})
    p.write_text("\n".join(json.dumps(x) for x in (json.loads(HOSTD_LINE), other, plain, flat, old, old_flat)) + "\n")
    assert microvm_state_ids([p], TRACE) == {"s000-07dd22c0", "s003-flat", "s004-old", "s005-old-flat"}
    assert microvm_state_ids([p], "ff" * 16) == {"s001-other"}
    assert microvm_state_ids([p], "00" * 16) == set()


def test_iter_logs_skips_metrics_and_garbage(tmp_path):
    p = tmp_path / "logs.jsonl"
    p.write_text('{"resourceMetrics": []}\nnot json\n[1,2]\n' + HOSTD_LINE + "\n")
    assert len(list(iter_logs(p))) == 1
