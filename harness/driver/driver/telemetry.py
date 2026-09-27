"""The driver's own telemetry: spans and structured logs as JSONL in the run directory,
plus best-effort OTLP/HTTP export (two-second timeout) to the collector.

Design section 8: one trace per trial; ``traceparent`` flows to the host daemon over HTTP.
Correlation keys on every span and log: fleetkit.run_id, trial_id, session_id, task_id, backend,
host_id. This module is the single integration point for the shared ``harness/telemetry``
package once the driver is switched to the OpenTelemetry SDK; the file formats stay the same.

spans.jsonl and logs.jsonl use the collector's own OTLP-JSON line shape, the same one the host
daemon and ``fleetkit_telemetry`` write: one ``ExportTraceServiceRequest`` (``resourceSpans``) or
``ExportLogsServiceRequest`` (``resourceLogs``) JSON object per line holding exactly one span or
one log record, camelCase field names, hex ids, uint64 timestamps as strings. The bundle and the
smoke suite filter ``results/lgtm/otlp/*.jsonl`` and the components' files with the same reader
(driver/evidence.py), which also accepts a flat ``{"service", "trace_id", ...}`` record per line.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVICE_NAME = "driver"
OTLP_TIMEOUT_S = 2.0


def _hex(nbytes: int) -> str:
    return os.urandom(nbytes).hex()


def now_ns() -> int:
    return time.time_ns()


class Span:
    def __init__(self, tracer: "Tracer", name: str, trace_id: str, parent_span_id: str | None,
                 attributes: dict | None = None, start_ns: int | None = None):
        self.tracer = tracer
        self.name = name
        self.trace_id = trace_id
        self.span_id = _hex(8)
        self.parent_span_id = parent_span_id or ""
        self.attributes = dict(tracer.resource_attributes)
        self.attributes.update(attributes or {})
        self.start_ns = start_ns if start_ns is not None else now_ns()
        self.end_ns: int | None = None
        self.status = "ok"
        self.status_message = ""

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-01"

    def set(self, **attrs) -> None:
        for k, v in attrs.items():
            if v is not None:
                self.attributes[k] = v

    def error(self, message: str) -> None:
        self.status = "error"
        self.status_message = str(message)

    def end(self, end_ns: int | None = None) -> None:
        if self.end_ns is not None:
            return
        self.end_ns = end_ns if end_ns is not None else now_ns()
        self.tracer._record(self)

    def __enter__(self) -> "Span":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None and self.status == "ok":
            self.error(f"{exc_type.__name__}: {exc}")
        self.end()


class Tracer:
    """Writes spans.jsonl / logs.jsonl and ships OTLP JSON best-effort."""

    def __init__(self, spans_path: Path, logs_path: Path, text_log_path: Path | None = None,
                 otlp_endpoint: str | None = None, resource_attributes: dict | None = None,
                 stdout: bool = True):
        self.spans_path = Path(spans_path)
        self.logs_path = Path(logs_path)
        self.text_log_path = Path(text_log_path) if text_log_path else None
        self.resource_attributes = {"service.name": SERVICE_NAME}
        self.resource_attributes.update(resource_attributes or {})
        self._lock = threading.Lock()
        self._stdout = stdout
        self.spans_path.parent.mkdir(parents=True, exist_ok=True)
        self._spans_fh = open(self.spans_path, "a", encoding="utf-8")
        self._logs_fh = open(self.logs_path, "a", encoding="utf-8")
        self._text_fh = open(self.text_log_path, "a", encoding="utf-8") if self.text_log_path else None
        self._otlp = OtlpExporter(otlp_endpoint, self.resource_attributes) if otlp_endpoint else None
        self._current = threading.local()

    # ---- spans -------------------------------------------------------------------------
    def start_span(self, name: str, parent: Span | None = None, attributes: dict | None = None,
                   trace_id: str | None = None, start_ns: int | None = None) -> Span:
        if parent is not None:
            tid, pid = parent.trace_id, parent.span_id
        else:
            tid, pid = (trace_id or _hex(16)), None
        return Span(self, name, tid, pid, attributes, start_ns)

    def _record(self, span: Span) -> None:
        rec = {
            "service": SERVICE_NAME,
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "name": span.name,
            "start_ns": span.start_ns,
            "end_ns": span.end_ns,
            "status": span.status,
            "status_message": span.status_message,
            "attributes": _plain(span.attributes),
        }
        line = json.dumps(span_record_to_otlp(rec, self.resource_attributes), separators=(",", ":"))
        with self._lock:
            self._spans_fh.write(line + "\n")
            self._spans_fh.flush()
        if self._otlp:
            self._otlp.submit_span(rec)

    # ---- logs --------------------------------------------------------------------------
    def log(self, level: str, message: str, span: Span | None = None, **attributes) -> None:
        ts = time.time()
        rec = {
            "ts": ts,
            "level": level.upper(),
            "service": SERVICE_NAME,
            "message": message,
            "trace_id": span.trace_id if span else "",
            "span_id": span.span_id if span else "",
            "attributes": _plain({**{k: v for k, v in self.resource_attributes.items() if k != "service.name"},
                                  **attributes}),
        }
        line = json.dumps(log_record_to_otlp(rec, self.resource_attributes), separators=(",", ":"))
        text = "{} {:5s} {}{}".format(
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int((ts % 1) * 1000):03d}Z",
            rec["level"], message,
            (" " + " ".join(f"{k}={v}" for k, v in attributes.items())) if attributes else "")
        with self._lock:
            self._logs_fh.write(line + "\n")
            self._logs_fh.flush()
            if self._text_fh:
                self._text_fh.write(text + "\n")
                self._text_fh.flush()
        if self._stdout:
            print(text, flush=True)
        if self._otlp:
            self._otlp.submit_log(rec)

    def info(self, message: str, span: Span | None = None, **attrs) -> None:
        self.log("INFO", message, span, **attrs)

    def warn(self, message: str, span: Span | None = None, **attrs) -> None:
        self.log("WARN", message, span, **attrs)

    def error(self, message: str, span: Span | None = None, **attrs) -> None:
        self.log("ERROR", message, span, **attrs)

    def close(self) -> None:
        if self._otlp:
            self._otlp.close()
        with self._lock:
            for fh in (self._spans_fh, self._logs_fh, self._text_fh):
                if fh:
                    try:
                        fh.close()
                    except Exception:
                        pass


def _plain(attrs: dict) -> dict:
    out = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


# ---- OTLP/HTTP JSON, best effort --------------------------------------------------------

def _otlp_value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def otlp_attributes(attrs: dict) -> list[dict]:
    return [{"key": k, "value": _otlp_value(v)} for k, v in attrs.items()]


def span_record_to_otlp(rec: dict, resource_attributes: dict) -> dict:
    attrs = {k: v for k, v in rec.get("attributes", {}).items() if k != "service.name"}
    span = {
        "traceId": rec["trace_id"],
        "spanId": rec["span_id"],
        "parentSpanId": rec.get("parent_span_id") or "",
        "name": rec["name"],
        "kind": 1,
        "startTimeUnixNano": str(rec["start_ns"]),
        "endTimeUnixNano": str(rec["end_ns"]),
        "attributes": otlp_attributes(attrs),
        "status": {"code": 2 if rec.get("status") == "error" else 1,
                   "message": rec.get("status_message", "")},
    }
    return {"resourceSpans": [{
        "resource": {"attributes": otlp_attributes(resource_attributes)},
        "scopeSpans": [{"scope": {"name": "fleetkit.driver"}, "spans": [span]}],
    }]}


_SEVERITY = {"DEBUG": 5, "INFO": 9, "WARN": 13, "WARNING": 13, "ERROR": 17}


def log_record_to_otlp(rec: dict, resource_attributes: dict) -> dict:
    lr = {
        "timeUnixNano": str(int(rec["ts"] * 1e9)),
        "severityNumber": _SEVERITY.get(rec["level"], 9),
        "severityText": rec["level"],
        "body": {"stringValue": rec["message"]},
        "attributes": otlp_attributes(rec.get("attributes", {})),
    }
    if rec.get("trace_id"):
        lr["traceId"] = rec["trace_id"]
        lr["spanId"] = rec.get("span_id", "")
    return {"resourceLogs": [{
        "resource": {"attributes": otlp_attributes(resource_attributes)},
        "scopeLogs": [{"scope": {"name": "fleetkit.driver"}, "logRecords": [lr]}],
    }]}


class OtlpExporter:
    """Ships each record on a background thread; failures are counted, never raised."""

    def __init__(self, endpoint: str, resource_attributes: dict):
        self.endpoint = endpoint.rstrip("/")
        self.resource_attributes = dict(resource_attributes)
        self._q: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
        self.failures = 0
        self.sent = 0
        self._warned = False
        self._thread = threading.Thread(target=self._run, name="otlp-export", daemon=True)
        self._thread.start()

    def submit_span(self, rec: dict) -> None:
        self._q.put(("/v1/traces", span_record_to_otlp(rec, self.resource_attributes)))

    def submit_log(self, rec: dict) -> None:
        self._q.put(("/v1/logs", log_record_to_otlp(rec, self.resource_attributes)))

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            path, payload = item
            try:
                req = urllib.request.Request(
                    self.endpoint + path, data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=OTLP_TIMEOUT_S) as resp:
                    resp.read()
                self.sent += 1
            except Exception as exc:  # best effort by design
                self.failures += 1
                if not self._warned:
                    self._warned = True
                    print(f"otlp export unavailable ({exc.__class__.__name__}); continuing without it",
                          flush=True)

    def close(self, timeout: float = 5.0) -> None:
        self._q.put(None)
        self._thread.join(timeout)
