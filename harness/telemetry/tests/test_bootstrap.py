from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

import fleetkit_telemetry as ft


class _Collector:
    """A stand-in for the LGTM collector: accepts OTLP/HTTP protobuf on /v1/*."""

    def __init__(self):
        self.received = {"traces": [], "logs": [], "metrics": []}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                signal = self.path.rsplit("/", 1)[-1]
                outer.received.setdefault(signal, []).append(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def collector():
    c = _Collector()
    yield c
    c.close()


def test_no_lgtm_still_writes_files_and_stamps_context(make_tel, tmp_path):
    tel = make_tel(log_file=tmp_path / "hostd.log")
    with tel.tracer.start_as_current_span("work") as span, tel.correlation(run_id="r5"):
        tel.logger.info("hello", extra={"n": 1})
    tel.force_flush()
    assert tel.span_writer.path.exists() and tel.log_writer.path.exists()
    text_line = json.loads((tmp_path / "hostd.log").read_text().splitlines()[-1])
    assert text_line["msg"] == "hello" and text_line["n"] == 1
    assert text_line["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert text_line[ft.ATTR_RUN_ID] == "r5" and text_line["service"] == "hostd"


def test_otlp_export_reaches_collector(make_tel, collector):
    tel = make_tel(lgtm=True, otlp_endpoint=collector.endpoint, metrics_interval_s=0.2)
    with tel.tracer.start_as_current_span("exported"):
        tel.logger.info("shipped")
    counter = tel.meter.create_counter("fleetkit.test.count")
    counter.add(1)
    assert tel.force_flush(5.0)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (collector.received["traces"] and collector.received["logs"]):
        time.sleep(0.05)
    req = ExportTraceServiceRequest()
    req.ParseFromString(collector.received["traces"][0])
    names = [s.name for rs in req.resource_spans for ss in rs.scope_spans for s in ss.spans]
    assert "exported" in names
    logs = ExportLogsServiceRequest()
    logs.ParseFromString(collector.received["logs"][0])
    bodies = [r.body.string_value for rl in logs.resource_logs for sl in rl.scope_logs for r in sl.log_records]
    assert "shipped" in bodies
    assert collector.received["metrics"], "periodic metric export should have fired"


def test_dead_collector_is_best_effort_and_bounded(make_tel):
    tel = make_tel(lgtm=True, otlp_endpoint="http://127.0.0.1:9", otlp_timeout_s=2.0)
    with tel.tracer.start_as_current_span("lost"):
        pass
    t0 = time.monotonic()
    tel.force_flush(10.0)
    assert time.monotonic() - t0 < 6.0
    assert tel.span_writer.path.exists()  # the JSONL copy is written regardless
    t0 = time.monotonic()
    tel.shutdown()
    assert time.monotonic() - t0 < 6.0


def test_cli_switches():
    parser = argparse.ArgumentParser()
    ft.add_telemetry_args(parser)
    args = parser.parse_args(["--no-lgtm", "--otlp-endpoint", "http://x:1", "--host-id", "h1"])
    cfg = ft.config_from_args(args, "driver", jsonl_dir="/tmp/run", log_file="/tmp/run/driver.log")
    assert cfg.lgtm is False and cfg.otlp_endpoint == "http://x:1" and cfg.host_id == "h1"
    assert cfg.service_name == "driver"
    default = ft.config_from_args(parser.parse_args([]), "hostd")
    assert default.lgtm is True and default.otlp_timeout_s == 2.0


def test_microvm_state_record(make_tel):
    tel = make_tel()
    tel.log_microvm_state("s000-aaaaaaaa", "booting", "ready", 1700000000.5)
    tel.log_microvm_state("s000-aaaaaaaa", "ready", "destroyed", 1700000010.0, outcome="completed")
    recs = [r for l in ft.iter_jsonl(tel.log_writer.path) for rl in l["resourceLogs"] for sl in rl["scopeLogs"] for r in sl["logRecords"]]
    attrs = {a["key"]: a["value"] for a in recs[1]["attributes"]}
    assert attrs["event.name"] == {"stringValue": "microvm.state"}
    assert attrs["microvm.from"] == {"stringValue": "ready"}
    assert attrs["microvm.to"] == {"stringValue": "destroyed"}
    assert attrs["microvm.outcome"] == {"stringValue": "completed"}
    assert attrs[ft.ATTR_MICROVM_ID] == {"stringValue": "s000-aaaaaaaa"}
    # the design's (and hostd's) names too; the transition time as state_ts
    assert attrs["microvm_id"] == {"stringValue": "s000-aaaaaaaa"}
    assert attrs["from"] == {"stringValue": "ready"}
    assert attrs["to"] == {"stringValue": "destroyed"}
    assert attrs["outcome"] == {"stringValue": "completed"}
    assert attrs["state_ts"] == {"doubleValue": 1700000010.0}
    first = {a["key"]: a["value"] for a in recs[0]["attributes"]}
    assert first["from"] == {"stringValue": "booting"} and first["state_ts"] == {"doubleValue": 1700000000.5}
    assert "outcome" not in first and "microvm.outcome" not in first


def test_microvm_state_json_line_matches_hostd_names(make_tel):
    """The JSON formatter flattens extras into the line: from/to/outcome as hostd writes them,
    state_ts for the transition time, and the line's own ts left alone."""
    import io
    import json
    import logging

    tel = make_tel()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ft.JsonFormatter("hostd"))
    tel.logger.addHandler(handler)
    try:
        tel.log_microvm_state("s001-bbbbbbbb", None, "creating", 1700000000.25)
    finally:
        tel.logger.removeHandler(handler)
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["microvm_id"] == "s001-bbbbbbbb" and line["from"] == "" and line["to"] == "creating"
    assert line["state_ts"] == 1700000000.25 and line["ts"] != 1700000000.25
    assert "outcome" not in line
    assert line["severity"] == "INFO" and line["event.name"] == "microvm.state"


def test_host_id_covers_every_resource_attribute(make_tel, monkeypatch):
    """--host-id / FLEETKIT_HOST_ID must reach every resource attribute: the hostname can carry
    the operator's name, and a sanitized host id is what a shared bundle is allowed to contain."""
    import socket

    hostname = socket.gethostname()
    tel = make_tel(host_id="mac-dev")
    for resource in (tel.resource, tel.guest_resource):
        attrs = resource.attributes
        assert attrs["host.name"] == "mac-dev"
        assert attrs[ft.ATTR_HOST_ID] == "mac-dev"
        assert attrs["service.instance.id"].startswith("mac-dev")
        assert not any(hostname in str(v) for v in attrs.values()), attrs
    monkeypatch.delenv("FLEETKIT_HOST_ID", raising=False)
    assert ft.bootstrap.default_host_id() == "local"
    monkeypatch.setenv("FLEETKIT_HOST_ID", "i-0123")
    assert ft.bootstrap.default_host_id() == "i-0123"
    default = make_tel()
    assert default.resource.attributes["host.name"] == "i-0123"
