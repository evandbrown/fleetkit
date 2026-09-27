"""After-the-fact guest telemetry.

The guest daemon has no SDK (design section 1). It echoes the ``traceparent`` it received
and returns ``guest_clock_ns`` (CLOCK_REALTIME at receipt of ``POST /task``) plus per-step
monotonic offsets. The host daemon turns those into spans with explicit timestamps under
the ``guest-daemon`` service, shifted by the clock offset so they line up with host spans.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from opentelemetry import context as otel_context, trace
from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry._logs import Logger as OtelLogger
from opentelemetry.trace import SpanKind, Status, StatusCode, format_span_id, format_trace_id

from . import attributes as A
from .propagation import extract_context, get_correlation

_SEVERITY = {
    "TRACE": SeverityNumber.TRACE,
    "DEBUG": SeverityNumber.DEBUG,
    "INFO": SeverityNumber.INFO,
    "WARN": SeverityNumber.WARN,
    "WARNING": SeverityNumber.WARN,
    "ERROR": SeverityNumber.ERROR,
    "CRITICAL": SeverityNumber.FATAL,
    "FATAL": SeverityNumber.FATAL,
}


def clock_offset_ns(host_send_ns: int, rtt_ns: int, guest_clock_ns: int) -> int:
    """``clock_offset_ns = host_send_ts + rtt/2 - guest_clock_ns`` (design section 8).

    ``rtt_ns`` must be a short round trip measured against the same guest (the readiness
    ``/health`` poll or the ``/metrics`` fetch), not the task's own wall time: the guest
    stamps ``guest_clock_ns`` at request receipt, so only the one-way latency separates it
    from ``host_send_ns``. Adding the offset to a guest timestamp gives host time.
    """
    return int(host_send_ns) + int(rtt_ns) // 2 - int(guest_clock_ns)


@dataclass
class GuestEmission:
    clock_offset_ns: int
    trace_id: str
    task_span_id: str
    task_start_ns: int
    task_end_ns: int
    steps_emitted: int


def _parent_context(response: Mapping[str, Any], parent_headers: Mapping[str, str] | None):
    traceparent = response.get("traceparent")
    if isinstance(traceparent, str) and traceparent:
        headers: dict[str, str] = {"traceparent": traceparent}
        if parent_headers:
            headers.update({k.lower(): v for k, v in parent_headers.items()})
        ctx = extract_context(headers, context=otel_context.get_current())
        if trace.get_current_span(ctx).get_span_context().is_valid:
            return ctx
    if parent_headers:
        return extract_context(parent_headers, context=otel_context.get_current())
    return otel_context.get_current()


def _step_bounds(step: Mapping[str, Any], task_start_ns: int, base_ns: int) -> tuple[int, int] | None:
    dispatch = step.get("dispatch_ns")
    if dispatch is None:
        return None
    start = task_start_ns + int(dispatch) - base_ns
    settle = step.get("settle_ns")
    if settle is not None:
        end = task_start_ns + int(settle) - base_ns
    elif step.get("duration_ms") is not None:
        end = start + int(float(step["duration_ms"]) * 1_000_000)
    else:
        end = start
    return start, max(start, end)


def emit_guest_task(
    tracer: trace.Tracer,
    response: Mapping[str, Any],
    *,
    session_id: str,
    task_id: str,
    host_send_ns: int,
    rtt_ns: int,
    guest_clock_ns: int | None = None,
    dispatch_base_ns: int = 0,
    parent_headers: Mapping[str, str] | None = None,
    extra_attributes: Mapping[str, Any] | None = None,
) -> GuestEmission:
    """Emit ``task`` and one child span per step for a guest ``POST /task`` response.

    ``tracer`` must come from the guest-daemon tracer provider (``Telemetry.guest_tracer``).
    ``host_send_ns``: host CLOCK_REALTIME when the proxied request was sent. ``rtt_ns``: a
    short round trip to the guest (see ``clock_offset_ns``). Step offsets are taken relative
    to request receipt; pass ``dispatch_base_ns`` if the guest reports absolute monotonic
    readings and the receipt reading is known. Correlation keys come from the current
    baggage; ``session_id`` and ``task_id`` are always set explicitly.
    """
    guest_clock = int(guest_clock_ns if guest_clock_ns is not None else response["guest_clock_ns"])
    offset = clock_offset_ns(host_send_ns, rtt_ns, guest_clock)
    task_start = guest_clock + offset

    steps: Sequence[Mapping[str, Any]] = response.get("steps") or []
    task_ms = response.get("task_ms")
    if task_ms is not None:
        task_end = task_start + int(float(task_ms) * 1_000_000)
    else:
        ends = [b[1] for b in (_step_bounds(s, task_start, dispatch_base_ns) for s in steps) if b]
        task_end = max(ends) if ends else task_start
    task_end = max(task_start, task_end)

    ok = bool(response.get("ok", False))
    failure_category = response.get("failure_category") or ("ok" if ok else "unknown")
    failed_step = response.get("failed_step")
    error = response.get("error")

    parent_ctx = _parent_context(response, parent_headers)
    attrs: dict[str, Any] = dict(get_correlation(parent_ctx))
    attrs.update(
        {
            A.ATTR_SESSION_ID: session_id,
            A.ATTR_TASK_ID: task_id,
            A.ATTR_OK: ok,
            A.ATTR_FAILURE_CATEGORY: failure_category,
            A.ATTR_CLOCK_OFFSET_NS: offset,
            A.ATTR_GUEST_CLOCK_NS: guest_clock,
        }
    )
    for key, name in (
        ("task_ms", A.ATTR_TASK_MS),
        ("bytes_received", A.ATTR_BYTES_RECEIVED),
        ("request_count", A.ATTR_REQUEST_COUNT),
    ):
        if response.get(key) is not None:
            attrs[name] = response[key]
    if failed_step:
        attrs[A.ATTR_FAILED_STEP] = str(failed_step)
    if extra_attributes:
        attrs.update(extra_attributes)

    task_span = tracer.start_span("task", context=parent_ctx, kind=SpanKind.SERVER, attributes=attrs, start_time=task_start)
    if not ok:
        task_span.set_status(Status(StatusCode.ERROR, str(error or failure_category)))
    task_ctx = trace.set_span_in_context(task_span, parent_ctx)

    emitted = 0
    for index, step in enumerate(steps):
        bounds = _step_bounds(step, task_start, dispatch_base_ns)
        if bounds is None:
            continue
        start, end = bounds
        name = str(step.get("name") or f"step{index}")
        step_attrs: dict[str, Any] = {
            A.ATTR_SESSION_ID: session_id,
            A.ATTR_TASK_ID: task_id,
            A.ATTR_STEP: name,
            A.ATTR_STEP_INDEX: index,
        }
        if step.get("duration_ms") is not None:
            step_attrs[A.ATTR_DURATION_MS] = step["duration_ms"]
        if step.get("error"):
            step_attrs["fleetkit.error"] = str(step["error"])
        span = tracer.start_span(name, context=task_ctx, kind=SpanKind.INTERNAL, attributes=step_attrs, start_time=start)
        if failed_step and name == failed_step:
            span.set_status(Status(StatusCode.ERROR, str(error or failure_category)))
        span.end(end_time=end)
        emitted += 1

    task_span.end(end_time=task_end)
    sc = task_span.get_span_context()
    return GuestEmission(
        clock_offset_ns=offset,
        trace_id=format_trace_id(sc.trace_id),
        task_span_id=format_span_id(sc.span_id),
        task_start_ns=task_start,
        task_end_ns=task_end,
        steps_emitted=emitted,
    )


def _guest_ts_ns(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1e14:  # already nanoseconds
        return int(number)
    if number > 1e11:  # milliseconds
        return int(number * 1_000_000)
    return int(number * 1_000_000_000)  # seconds


def emit_guest_logs(
    logger: OtelLogger,
    log_tail: Sequence[Any] | None,
    *,
    session_id: str,
    task_id: str | None,
    clock_offset_ns: int,
    trace_id: str | None = None,
    span_id: str | None = None,
    now_ns: int | None = None,
) -> int:
    """Forward the guest's ``log_tail`` as OTLP log records under the guest-daemon resource.

    Accepts structured lines (``{ts|time|timestamp, level|severity, msg|message|body, ...}``)
    or plain strings. Guest timestamps are shifted by ``clock_offset_ns`` into host time;
    lines without a timestamp get ``now_ns``. Returns the number of records emitted.
    """
    if not log_tail:
        return 0
    import time as _time

    observed = now_ns if now_ns is not None else _time.time_ns()
    tid = int(trace_id, 16) if trace_id else None
    sid = int(span_id, 16) if span_id else None
    count = 0
    for line in log_tail:
        attrs: dict[str, Any] = {A.ATTR_SESSION_ID: session_id}
        if task_id:
            attrs[A.ATTR_TASK_ID] = task_id
        ts: int | None = None
        level = "INFO"
        if isinstance(line, Mapping):
            body = line.get("msg", line.get("message", line.get("body")))
            level = str(line.get("level", line.get("severity", "INFO"))).upper()
            ts = _guest_ts_ns(line.get("ts", line.get("time", line.get("timestamp"))))
            for key, value in line.items():
                if key in ("msg", "message", "body", "level", "severity", "ts", "time", "timestamp"):
                    continue
                if isinstance(value, (str, int, float, bool)):
                    attrs[f"guest.{key}"] = value
            if body is None:
                body = str(dict(line))
        else:
            body = str(line)
        record = LogRecord(
            timestamp=(ts + clock_offset_ns) if ts is not None else observed,
            observed_timestamp=observed,
            trace_id=tid,
            span_id=sid,
            trace_flags=trace.TraceFlags(0x01) if tid else None,
            severity_text=level,
            severity_number=_SEVERITY.get(level, SeverityNumber.INFO),
            body=body,
            attributes=attrs,
        )
        logger.emit(record)
        count += 1
    return count
