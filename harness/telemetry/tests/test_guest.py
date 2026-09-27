from __future__ import annotations

import time

import fleetkit_telemetry as ft


def _spans(tel):
    out = []
    for line in ft.iter_jsonl(tel.span_writer.path):
        for rs in line["resourceSpans"]:
            service = next(a["value"]["stringValue"] for a in rs["resource"]["attributes"] if a["key"] == "service.name")
            for ss in rs["scopeSpans"]:
                for s in ss["spans"]:
                    s["_service"] = service
                    out.append(s)
    return out


def _attrs(span):
    out = {}
    for a in span.get("attributes", []):
        v = a["value"]
        out[a["key"]] = v.get("stringValue", v.get("intValue", v.get("boolValue", v.get("doubleValue"))))
    return out


def test_clock_offset_formula():
    assert ft.clock_offset_ns(host_send_ns=1_000_000, rtt_ns=400, guest_clock_ns=250_000) == 1_000_000 + 200 - 250_000


def test_guest_task_spans_under_echoed_traceparent_and_shifted_clock(make_tel):
    tel = make_tel()
    send_ns = time.time_ns()
    guest_clock = send_ns - 5_000_000_000  # guest clock five seconds behind
    with tel.tracer.start_as_current_span("proxy") as proxy, tel.correlation(run_id="r3", trial_id="t3"):
        headers = tel.inject()
        response = {
            "ok": True,
            "failure_category": "ok",
            "steps": [
                {"name": "home", "dispatch_ns": 0, "settle_ns": 200_000_000, "duration_ms": 200},
                {"name": "search", "dispatch_ns": 200_000_000, "settle_ns": 500_000_000, "duration_ms": 300},
            ],
            "task_ms": 500,
            "bytes_received": 10,
            "request_count": 2,
            "guest_clock_ns": guest_clock,
            "traceparent": headers["traceparent"],
        }
        em = tel.emit_guest_task(response, session_id="s1", task_id="k1", host_send_ns=send_ns, rtt_ns=2_000_000)
    assert em.clock_offset_ns == 5_000_000_000 + 1_000_000
    assert em.steps_emitted == 2
    assert em.task_start_ns == guest_clock + em.clock_offset_ns

    spans = _spans(tel)
    task = next(s for s in spans if s["name"] == "task")
    assert task["_service"] == "guest-daemon"
    assert task["parentSpanId"] == format(proxy.get_span_context().span_id, "016x")
    assert task["traceId"] == format(proxy.get_span_context().trace_id, "032x")
    assert int(task["startTimeUnixNano"]) == em.task_start_ns
    assert int(task["endTimeUnixNano"]) - int(task["startTimeUnixNano"]) == 500_000_000
    attrs = _attrs(task)
    assert attrs[ft.ATTR_RUN_ID] == "r3" and attrs[ft.ATTR_TRIAL_ID] == "t3"
    assert attrs[ft.ATTR_SESSION_ID] == "s1" and attrs[ft.ATTR_TASK_ID] == "k1"
    assert attrs["fleetkit.clock_offset_ns"] == str(em.clock_offset_ns)
    assert "status" not in task or task["status"] == {}

    search = next(s for s in spans if s["name"] == "search")
    assert search["parentSpanId"] == task["spanId"]
    assert int(search["startTimeUnixNano"]) == em.task_start_ns + 200_000_000
    assert int(search["endTimeUnixNano"]) == em.task_start_ns + 500_000_000
    assert _attrs(search)["fleetkit.step_index"] == "1"


def test_failed_task_marks_task_and_failed_step(make_tel):
    tel = make_tel()
    send_ns = time.time_ns()
    response = {
        "ok": False,
        "failure_category": "step_timeout",
        "failed_step": "search",
        "error": "deadline",
        "steps": [
            {"name": "home", "dispatch_ns": 0, "settle_ns": 100_000_000},
            {"name": "search", "dispatch_ns": 100_000_000, "duration_ms": 10000},
        ],
        "guest_clock_ns": send_ns,
    }
    em = tel.emit_guest_task(response, session_id="s2", task_id="k2", host_send_ns=send_ns, rtt_ns=0)
    spans = _spans(tel)
    task = next(s for s in spans if s["name"] == "task")
    assert task["status"]["code"] == "STATUS_CODE_ERROR"
    assert _attrs(task)["fleetkit.failed_step"] == "search"
    assert int(task["endTimeUnixNano"]) == em.task_start_ns + 100_000_000 + 10_000_000_000  # no task_ms: last step end
    search = next(s for s in spans if s["name"] == "search")
    assert search["status"]["code"] == "STATUS_CODE_ERROR"
    home = next(s for s in spans if s["name"] == "home")
    assert "status" not in home or home["status"].get("code") != "STATUS_CODE_ERROR"


def test_guest_logs_forwarded_with_shifted_timestamps(make_tel):
    tel = make_tel()
    guest_ts = 1_700_000_000.25
    n = tel.emit_guest_logs(
        [{"seq": 1, "ts": guest_ts, "level": "WARN", "msg": "slow"}, "plain"],
        session_id="s3",
        task_id="k3",
        clock_offset_ns=1_000_000_000,
        trace_id="0af7651916cd43dd8448eb211c80319c",
        span_id="b7ad6b7169203331",
    )
    assert n == 2
    recs = [r for l in ft.iter_jsonl(tel.log_writer.path) for rl in l["resourceLogs"] for sl in rl["scopeLogs"] for r in sl["logRecords"]]
    assert len(recs) == 2
    first, second = recs
    assert int(first["timeUnixNano"]) == int(guest_ts * 1e9) + 1_000_000_000
    assert first["severityText"] == "WARN" and first["body"] == {"stringValue": "slow"}
    assert first["traceId"] == "0af7651916cd43dd8448eb211c80319c"
    assert {a["key"]: a["value"] for a in first["attributes"]}["guest.seq"] == {"intValue": "1"}
    assert second["body"] == {"stringValue": "plain"}
    service = next(a["value"]["stringValue"] for a in list(ft.iter_jsonl(tel.log_writer.path))[0]["resourceLogs"][0]["resource"]["attributes"] if a["key"] == "service.name")
    assert service == "guest-daemon"
