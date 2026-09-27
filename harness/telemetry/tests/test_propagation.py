from __future__ import annotations

import email.message

from opentelemetry import trace

import fleetkit_telemetry as ft


def test_inject_extract_roundtrip_with_case_insensitive_headers(make_tel):
    tel = make_tel()
    with tel.tracer.start_as_current_span("client") as client_span, tel.correlation(run_id="r1", trial_id="t1"):
        headers = tel.inject({"Content-Type": "application/json"})
    assert headers["traceparent"].startswith("00-")
    assert "fleetkit.run_id=r1" in headers["baggage"]

    # http.server hands the daemon an email.message.Message; keys may differ in case.
    msg = email.message.Message()
    msg["Traceparent"] = headers["traceparent"]
    msg["Baggage"] = headers["baggage"]
    ctx = tel.extract(msg)
    parent = trace.get_current_span(ctx).get_span_context()
    assert parent.trace_id == client_span.get_span_context().trace_id
    assert parent.span_id == client_span.get_span_context().span_id
    assert ft.get_correlation(ctx) == {ft.ATTR_RUN_ID: "r1", ft.ATTR_TRIAL_ID: "t1"}


def test_server_span_is_child_and_carries_baggage(make_tel):
    driver = make_tel(service_name="driver")
    hostd = make_tel(service_name="hostd")
    with driver.tracer.start_as_current_span("trial") as trial, driver.correlation(run_id="r2", backend="docker"):
        headers = driver.inject()
    with hostd.server_span("POST /sessions", headers, attributes={"http.request.method": "POST"}) as span:
        with hostd.correlation(session_id="s7"):
            with hostd.tracer.start_as_current_span("session.create") as child:
                assert ft.get_correlation()[ft.ATTR_SESSION_ID] == "s7"
    lines = list(ft.iter_jsonl(hostd.span_writer.path))
    spans = {s["name"]: s for l in lines for s in l["resourceSpans"][0]["scopeSpans"][0]["spans"]}
    server = spans["POST /sessions"]
    assert server["traceId"] == format(trial.get_span_context().trace_id, "032x")
    assert server["parentSpanId"] == format(trial.get_span_context().span_id, "016x")
    assert server["kind"] == "SPAN_KIND_SERVER"
    attrs = {a["key"]: a["value"]["stringValue"] for a in server["attributes"] if "stringValue" in a["value"]}
    assert attrs[ft.ATTR_RUN_ID] == "r2" and attrs[ft.ATTR_BACKEND] == "docker"
    child_attrs = {a["key"]: a["value"]["stringValue"] for a in spans["session.create"]["attributes"] if "stringValue" in a["value"]}
    assert child_attrs[ft.ATTR_SESSION_ID] == "s7" and child_attrs[ft.ATTR_RUN_ID] == "r2"


def test_explicit_attribute_wins_over_baggage(make_tel):
    tel = make_tel()
    with tel.correlation(session_id="from-baggage"):
        with tel.tracer.start_as_current_span("x", attributes={ft.ATTR_SESSION_ID: "explicit"}):
            pass
    span = list(ft.iter_jsonl(tel.span_writer.path))[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in span["attributes"]}
    assert attrs[ft.ATTR_SESSION_ID] == "explicit"


def test_correlation_attributes_normalises_names():
    assert ft.correlation_attributes(run_id="a", trial_id=None, **{"fleetkit.task_id": 5}) == {
        ft.ATTR_RUN_ID: "a",
        ft.ATTR_TASK_ID: "5",
    }
