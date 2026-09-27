from __future__ import annotations

import json

import fleetkit_telemetry as ft


def _span_line(tel):
    with tel.tracer.start_as_current_span("hello"):
        pass
    lines = list(ft.iter_jsonl(tel.span_writer.path))
    assert len(lines) == 1
    return lines[0]


def test_span_line_matches_collector_json_shape(make_tel):
    line = _span_line(make_tel())
    rs = line["resourceSpans"]
    assert len(rs) == 1
    span = rs[0]["scopeSpans"][0]["spans"][0]
    assert span["name"] == "hello"
    assert len(span["traceId"]) == 32 and int(span["traceId"], 16) >= 0
    assert len(span["spanId"]) == 16
    assert "parentSpanId" not in span  # root span: empty bytes are omitted, like pdata JSON
    assert span["startTimeUnixNano"].isdigit()  # uint64 as string, like the collector
    keys = {a["key"]: a["value"] for a in rs[0]["resource"]["attributes"]}
    assert keys["service.name"] == {"stringValue": "hostd"}
    assert ft.ATTR_HOST_ID in keys


def test_log_line_has_hex_ids_and_attributes(make_tel):
    tel = make_tel()
    with tel.tracer.start_as_current_span("op"), tel.correlation(run_id="r9"):
        tel.logger.warning("careful", extra={"count": 3})
    tel.force_flush()
    lines = list(ft.iter_jsonl(tel.log_writer.path))
    rec = lines[-1]["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    assert rec["body"] == {"stringValue": "careful"}
    assert rec["severityText"] == "WARN"  # the SDK maps logging.WARNING to WARN
    assert len(rec["traceId"]) == 32 and len(rec["spanId"]) == 16
    attrs = {a["key"]: a["value"] for a in rec["attributes"]}
    assert attrs["count"] == {"intValue": "3"}
    assert attrs[ft.ATTR_RUN_ID] == {"stringValue": "r9"}


def test_iter_jsonl_skips_truncated_last_line(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"a":1}\n{"b":2}\n{"c":')
    assert list(ft.iter_jsonl(path)) == [{"a": 1}, {"b": 2}]


def test_filter_jsonl_by_run_id_on_resource_or_record(make_tel, tmp_path):
    tel = make_tel(resource_attributes={ft.ATTR_RUN_ID: "run-A"})
    other = make_tel(service_name="driver")
    with tel.tracer.start_as_current_span("in-resource"):
        pass
    with other.tracer.start_as_current_span("in-attr", attributes={ft.ATTR_RUN_ID: "run-A"}):
        pass
    with other.tracer.start_as_current_span("unrelated"):
        pass
    out = tmp_path / "out.jsonl"
    assert ft.filter_jsonl(tel.span_writer.path, out, "run-A") == 1
    assert ft.filter_jsonl(other.span_writer.path, out, "run-A") == 1
    assert ft.filter_jsonl(tmp_path / "missing.jsonl", out, "run-A") == 0
    names = [l["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] for l in ft.iter_jsonl(out)]
    assert names == ["in-resource", "in-attr"]


def test_writer_flushes_every_line(tmp_path):
    w = ft.JsonlWriter(tmp_path / "w.jsonl")
    w.write({"k": "v"})
    assert json.loads((tmp_path / "w.jsonl").read_text().strip()) == {"k": "v"}
    w.close()
