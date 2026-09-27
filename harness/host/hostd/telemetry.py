"""Telemetry for the host daemon: JSONL on disk always, OTLP/HTTP best-effort.

Section 8 of the design: the daemon writes its own spans and logs as JSONL into
its log directory so the evidence bundle is complete without the collector, and
exports the same records over OTLP/HTTP (JSON encoding) with a two-second
timeout when an endpoint is configured. Guest spans are emitted after the fact
under the `guest-daemon` service with explicit start and end times.

No SDK dependency: the OTLP JSON payloads are small enough to build by hand and
this keeps the daemon runnable from a stock python3 on the host. If the shared
harness/telemetry package grows an equivalent interface this module is the seam
to swap.

Correlation attributes on every record: fleetkit.run_id, trial_id, session_id,
task_id, backend, host_id (whichever are known).
"""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from .model import TraceContext

_TRACEPARENT_RE = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")

SEVERITY = {"DEBUG": 5, "INFO": 9, "WARN": 13, "ERROR": 17}


def parse_traceparent(header: Optional[str]) -> Optional[TraceContext]:
    if not header:
        return None
    m = _TRACEPARENT_RE.match(header.strip().lower())
    if not m:
        return None
    version, trace_id, span_id, flags = m.groups()
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return TraceContext(trace_id=trace_id, span_id=span_id, sampled=bool(int(flags, 16) & 1))


def parse_baggage(header: Optional[str]) -> Dict[str, str]:
    """W3C baggage: `k=v,k2=v2` (values URL-encoded). Used for fleetkit.run_id / trial_id."""
    out: Dict[str, str] = {}
    if not header:
        return out
    from urllib.parse import unquote
    for item in header.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, _, v = item.partition("=")
        v = v.split(";", 1)[0]
        out[k.strip()] = unquote(v.strip())
    return out


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def _otlp_value(v: Any) -> Dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_otlp_value(x) for x in v]}}
    return {"stringValue": str(v)}


def _otlp_attrs(attrs: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"key": k, "value": _otlp_value(v)} for k, v in attrs.items() if v is not None]


class Span:
    def __init__(self, tel: "Telemetry", name: str, ctx: TraceContext, parent: Optional[TraceContext],
                 start_ns: int, attrs: Dict[str, Any], service: str):
        self.tel = tel
        self.name = name
        self.ctx = ctx
        self.parent = parent
        self.start_ns = start_ns
        self.attrs = dict(attrs)
        self.service = service
        self.status = "ok"
        self.ended = False

    def set(self, **attrs: Any) -> None:
        self.attrs.update(attrs)

    def end(self, end_ns: Optional[int] = None, status: Optional[str] = None, error: Optional[str] = None) -> None:
        if self.ended:
            return
        self.ended = True
        if error:
            self.status = "error"
            self.attrs.setdefault("error", error)
        elif status:
            self.status = status
        self.tel._emit_span(self, end_ns or time.time_ns())


class Telemetry:
    """Spans, logs and gauges to JSONL files, stderr, and (best-effort) OTLP/HTTP."""

    def __init__(self, service: str, log_dir: Optional[str], otlp_endpoint: Optional[str] = None,
                 resource: Optional[Dict[str, Any]] = None, otlp_timeout_s: float = 2.0,
                 stderr: bool = True):
        self.service = service
        self.log_dir = log_dir
        self.resource = dict(resource or {})
        self.otlp_endpoint = otlp_endpoint.rstrip("/") if otlp_endpoint else None
        self.otlp_timeout_s = otlp_timeout_s
        self.stderr = stderr
        self._files: Dict[str, Any] = {}
        self._file_lock = threading.Lock()
        self._q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)
        self._closed = False
        self._backoff_until = 0.0
        self._backoff_s = 1.0
        self.otlp_failures = 0
        self.otlp_batches_sent = 0
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        if self.otlp_endpoint:
            self._exporter = threading.Thread(target=self._export_loop, name="otlp-export", daemon=True)
            self._exporter.start()
        else:
            self._exporter = None

    # ----- files -----------------------------------------------------------
    def _write_line(self, filename: str, obj: Dict[str, Any]) -> None:
        if not self.log_dir:
            return
        with self._file_lock:
            f = self._files.get(filename)
            if f is None:
                f = open(os.path.join(self.log_dir, filename), "a", buffering=1)
                self._files[filename] = f
            f.write(json.dumps(obj, separators=(",", ":"), default=str) + "\n")

    def _write_text(self, filename: str, line: str) -> None:
        if not self.log_dir:
            return
        with self._file_lock:
            f = self._files.get(filename)
            if f is None:
                f = open(os.path.join(self.log_dir, filename), "a", buffering=1)
                self._files[filename] = f
            f.write(line + "\n")

    # ----- spans -----------------------------------------------------------
    def start_span(self, name: str, parent: Optional[TraceContext] = None, attrs: Optional[Dict[str, Any]] = None,
                   start_ns: Optional[int] = None, service: Optional[str] = None) -> Span:
        ctx = TraceContext(trace_id=parent.trace_id if parent else new_trace_id(), span_id=new_span_id())
        return Span(self, name, ctx, parent, start_ns or time.time_ns(), attrs or {}, service or self.service)

    def record_span(self, name: str, parent: Optional[TraceContext], start_ns: int, end_ns: int,
                    attrs: Optional[Dict[str, Any]] = None, service: Optional[str] = None,
                    status: str = "ok", error: Optional[str] = None) -> TraceContext:
        """A span with explicit times, e.g. a guest step reconstructed after the fact."""
        span = self.start_span(name, parent, attrs, start_ns, service)
        span.end(end_ns, status=status, error=error)
        return span.ctx

    def _emit_span(self, span: Span, end_ns: int) -> None:
        rec = {
            "name": span.name,
            "service": span.service,
            "trace_id": span.ctx.trace_id,
            "span_id": span.ctx.span_id,
            "parent_span_id": span.parent.span_id if span.parent else None,
            "start_ns": span.start_ns,
            "end_ns": end_ns,
            "duration_ms": (end_ns - span.start_ns) / 1e6,
            "status": span.status,
            "attributes": {**self.resource, **span.attrs},
        }
        self._write_line("spans.jsonl", rec)
        self._enqueue("span", rec)

    # ----- logs ------------------------------------------------------------
    def log(self, body: str, severity: str = "INFO", ctx: Optional[TraceContext] = None,
            attrs: Optional[Dict[str, Any]] = None, service: Optional[str] = None,
            ts_ns: Optional[int] = None, event: Optional[str] = None) -> None:
        ts_ns = ts_ns or time.time_ns()
        attributes = {**self.resource, **(attrs or {})}
        if event:
            attributes["event"] = event
        rec = {
            "ts": ts_ns / 1e9,
            "severity": severity,
            "service": service or self.service,
            "body": body,
            "trace_id": ctx.trace_id if ctx else None,
            "span_id": ctx.span_id if ctx else None,
            "attributes": attributes,
        }
        self._write_line("logs.jsonl", rec)
        human = "%s %-5s %s%s" % (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts_ns / 1e9)) + (".%03dZ" % ((ts_ns // 1_000_000) % 1000)),
            severity, ("[%s] " % (service or self.service)) if service and service != self.service else "", body)
        if event or attrs:
            extras = {k: v for k, v in (attrs or {}).items() if v is not None}
            if event:
                extras = {"event": event, **extras}
            human += " " + json.dumps(extras, separators=(",", ":"), default=str)
        self._write_text("hostd.log", human)
        if self.stderr:
            try:
                sys.stderr.write(human + "\n")
                sys.stderr.flush()
            except Exception:
                pass
        self._enqueue("log", rec)

    def event(self, name: str, ctx: Optional[TraceContext] = None, severity: str = "INFO", **fields: Any) -> None:
        """A structured event record, e.g. `session.state {session_id, from, to, ts, outcome}`."""
        self.log(name, severity=severity, ctx=ctx, attrs=fields, event=name)

    # ----- metrics ---------------------------------------------------------
    def gauges(self, points: List[Dict[str, Any]], ts_ns: Optional[int] = None) -> None:
        """points: [{name, value, attributes}] sampled at ts_ns. JSONL copy under host_metrics.jsonl."""
        ts_ns = ts_ns or time.time_ns()
        rec = {"ts": ts_ns / 1e9, "points": points}
        self._write_line("host_metrics.jsonl", rec)
        self._enqueue("metric", {"ts_ns": ts_ns, "points": points})

    # ----- OTLP export -----------------------------------------------------
    def _enqueue(self, kind: str, rec: Dict[str, Any]) -> None:
        if not self._exporter or self._closed:
            return
        try:
            self._q.put_nowait((kind, rec))
        except queue.Full:
            self.otlp_failures += 1

    def _export_loop(self) -> None:
        while not self._closed or not self._q.empty():
            batch: List[tuple] = []
            try:
                batch.append(self._q.get(timeout=0.5))
            except queue.Empty:
                continue
            deadline = time.monotonic() + 0.5
            while len(batch) < 500 and time.monotonic() < deadline:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    time.sleep(0.05)
            if time.monotonic() < self._backoff_until:
                self.otlp_failures += len(batch)
                continue
            ok = True
            for kind, path, build in (("span", "/v1/traces", self._build_traces),
                                      ("log", "/v1/logs", self._build_logs),
                                      ("metric", "/v1/metrics", self._build_metrics)):
                items = [r for k, r in batch if k == kind]
                if items and not self._post(path, build(items)):
                    ok = False
            if ok:
                self._backoff_s = 1.0
                self.otlp_batches_sent += 1
            else:
                self.otlp_failures += 1
                self._backoff_until = time.monotonic() + self._backoff_s
                self._backoff_s = min(self._backoff_s * 2, 30.0)

    def _post(self, path: str, payload: Dict[str, Any]) -> bool:
        data = json.dumps(payload, default=str).encode()
        req = urllib.request.Request(self.otlp_endpoint + path, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.otlp_timeout_s) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _resource(self, service: str) -> Dict[str, Any]:
        attrs = {"service.name": service, **self.resource}
        return {"attributes": _otlp_attrs(attrs)}

    def _build_traces(self, recs: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_service: Dict[str, List[Dict[str, Any]]] = {}
        for r in recs:
            span = {
                "traceId": r["trace_id"], "spanId": r["span_id"], "name": r["name"], "kind": 1,
                "startTimeUnixNano": str(r["start_ns"]), "endTimeUnixNano": str(r["end_ns"]),
                "attributes": _otlp_attrs({k: v for k, v in r["attributes"].items() if k not in self.resource}),
                "status": {"code": 2 if r["status"] == "error" else 1},
            }
            if r.get("parent_span_id"):
                span["parentSpanId"] = r["parent_span_id"]
            by_service.setdefault(r["service"], []).append(span)
        return {"resourceSpans": [
            {"resource": self._resource(svc), "scopeSpans": [{"scope": {"name": "hostd"}, "spans": spans}]}
            for svc, spans in by_service.items()]}

    def _build_logs(self, recs: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_service: Dict[str, List[Dict[str, Any]]] = {}
        for r in recs:
            lr = {
                "timeUnixNano": str(int(r["ts"] * 1e9)),
                "severityNumber": SEVERITY.get(r["severity"], 9), "severityText": r["severity"],
                "body": {"stringValue": r["body"]},
                "attributes": _otlp_attrs({k: v for k, v in r["attributes"].items() if k not in self.resource}),
            }
            if r.get("trace_id"):
                lr["traceId"] = r["trace_id"]
                lr["spanId"] = r["span_id"]
            by_service.setdefault(r["service"], []).append(lr)
        return {"resourceLogs": [
            {"resource": self._resource(svc), "scopeLogs": [{"scope": {"name": "hostd"}, "logRecords": lrs}]}
            for svc, lrs in by_service.items()]}

    def _build_metrics(self, recs: List[Dict[str, Any]]) -> Dict[str, Any]:
        metrics: Dict[str, List[Dict[str, Any]]] = {}
        for r in recs:
            for p in r["points"]:
                if p.get("value") is None:
                    continue
                metrics.setdefault(p["name"], []).append({
                    "timeUnixNano": str(r["ts_ns"]), "asDouble": float(p["value"]),
                    "attributes": _otlp_attrs(p.get("attributes") or {}),
                })
        return {"resourceMetrics": [{
            "resource": self._resource(self.service),
            "scopeMetrics": [{"scope": {"name": "hostd"},
                              "metrics": [{"name": n, "gauge": {"dataPoints": pts}} for n, pts in metrics.items()]}],
        }]}

    def close(self, timeout: float = 3.0) -> None:
        self._closed = True
        if self._exporter:
            self._exporter.join(timeout)
        with self._file_lock:
            for f in self._files.values():
                try:
                    f.close()
                except Exception:
                    pass
            self._files.clear()
