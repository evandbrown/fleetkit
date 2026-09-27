"""Readers for span and log files in both shapes the bundle can hold:

* the collector's file exporter: one OTLP ExportTrace/LogsServiceRequest JSON object per line
  (``resourceSpans`` / ``resourceLogs``), and
* the components' own flat JSONL: spans as ``{"service", "trace_id", "span_id", ...}``; log
  records in the host daemon's shape (hostd/telemetry.py ``log()``: ``{"ts", "severity",
  "service", "body", "trace_id", "span_id", "attributes"}``, with ``attributes.event`` naming a
  structured event such as ``microvm.state``) or the older ``{"message" | "msg" | "event", ...}``
  shape.

Records written before the glossary (D56) use older event and attribute names; they are read
through the alias map in driver/legacy.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import legacy


def _attr_map(attrs) -> dict:
    out = {}
    for a in attrs or []:
        if not isinstance(a, dict) or "key" not in a:
            continue
        v = a.get("value") or {}
        if isinstance(v, dict):
            for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
                if k in v:
                    out[a["key"]] = v[k]
                    break
        else:
            out[a["key"]] = v
    return out


def iter_lines(path: Path):
    path = Path(path)
    if not path.exists():
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line), line
            except ValueError:
                continue


def iter_spans(path: Path):
    """Yields dicts: {service, trace_id, span_id, name, attributes}."""
    for obj, _ in iter_lines(path):
        if not isinstance(obj, dict):
            continue
        if "resourceSpans" in obj:
            for rs in obj.get("resourceSpans") or []:
                res = _attr_map((rs.get("resource") or {}).get("attributes"))
                service = str(res.get("service.name", ""))
                for ss in rs.get("scopeSpans") or []:
                    for sp in ss.get("spans") or []:
                        attrs = dict(res)
                        attrs.update(_attr_map(sp.get("attributes")))
                        yield {"service": service, "trace_id": str(sp.get("traceId", "")).lower(),
                               "span_id": str(sp.get("spanId", "")), "name": sp.get("name", ""),
                               "attributes": attrs}
        elif "trace_id" in obj and "span_id" in obj:
            attrs = obj.get("attributes") or {}
            service = str(obj.get("service") or attrs.get("service.name") or "")
            yield {"service": service, "trace_id": str(obj["trace_id"]).lower(),
                   "span_id": str(obj["span_id"]), "name": obj.get("name", ""), "attributes": attrs}


def iter_logs(path: Path):
    """Yields dicts: {service, trace_id, message, attributes}."""
    for obj, _ in iter_lines(path):
        if not isinstance(obj, dict):
            continue
        if "resourceLogs" in obj:
            for rl in obj.get("resourceLogs") or []:
                res = _attr_map((rl.get("resource") or {}).get("attributes"))
                service = str(res.get("service.name", ""))
                for sl in rl.get("scopeLogs") or []:
                    for lr in sl.get("logRecords") or []:
                        attrs = dict(res)
                        attrs.update(_attr_map(lr.get("attributes")))
                        body = lr.get("body") or {}
                        yield {"service": service, "trace_id": str(lr.get("traceId", "")).lower(),
                               "message": body.get("stringValue", "") if isinstance(body, dict) else str(body),
                               "attributes": attrs}
        elif "resourceMetrics" in obj:
            continue
        elif "body" in obj and ("severity" in obj or "trace_id" in obj):
            # hostd's own logs.jsonl (hostd/telemetry.py log()): the event name, e.g. microvm.state,
            # is the body and is echoed as attributes.event
            attrs = obj.get("attributes") or {}
            service = str(obj.get("service") or attrs.get("service.name") or "")
            body = obj.get("body")
            yield {"service": service, "trace_id": str(obj.get("trace_id") or "").lower(),
                   "message": body if isinstance(body, str) else json.dumps(body, default=str),
                   "attributes": attrs}
        elif "message" in obj or "msg" in obj or "event" in obj:
            attrs = obj.get("attributes") or {}
            service = str(obj.get("service") or attrs.get("service.name") or "")
            yield {"service": service, "trace_id": str(obj.get("trace_id") or "").lower(),
                   "message": str(obj.get("message") or obj.get("msg") or obj.get("event") or ""),
                   "attributes": attrs}


def line_carries_run_id(obj, run_id: str) -> bool:
    """True if any resource, span, log or datapoint attribute in an OTLP line is fleetkit.run_id == run_id,
    or the flat record's attributes carry it."""
    found = False

    def walk(x):
        nonlocal found
        if found:
            return
        if isinstance(x, dict):
            if x.get("key") == "fleetkit.run_id":
                v = x.get("value")
                if isinstance(v, dict):
                    v = v.get("stringValue")
                if str(v) == run_id:
                    found = True
                    return
            if x.get("fleetkit.run_id") == run_id:
                found = True
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return found


def services_for_trace(span_files: list[Path], trace_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in span_files:
        for sp in iter_spans(f):
            if sp["trace_id"] == trace_id.lower():
                out[sp["service"]] = out.get(sp["service"], 0) + 1
    return out


def logs_for_trace(log_files: list[Path], trace_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in log_files:
        for lr in iter_logs(f):
            if lr["trace_id"] == trace_id.lower():
                out[lr["service"]] = out.get(lr["service"], 0) + 1
    return out


MICROVM_STATE = "microvm.state"


def is_microvm_state(lr: dict) -> bool:
    """True for a ``microvm.state`` record (design section 4) in either log shape, old names included."""
    attrs = lr.get("attributes") or {}
    return MICROVM_STATE in (legacy.event_name(lr.get("message")), legacy.event_name(attrs.get("event")))


def microvm_state_ids(log_files: list[Path], trace_id: str) -> set[str]:
    """MicroVM ids that have at least one ``microvm.state`` record carrying ``trace_id``."""
    out: set[str] = set()
    for f in log_files:
        for lr in iter_logs(f):
            if lr["trace_id"] != trace_id.lower() or not is_microvm_state(lr):
                continue
            attrs = legacy.log_attributes(lr.get("attributes"))
            mid = attrs.get("microvm_id") or attrs.get("fleetkit.microvm_id")
            if mid:
                out.add(str(mid))
    return out
