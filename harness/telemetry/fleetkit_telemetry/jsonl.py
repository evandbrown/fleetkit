"""JSONL exporters and readers.

The files written here use the same OTLP-JSON encoding the collector's ``file`` exporter
writes into ``results/lgtm/otlp/*.jsonl`` (camelCase field names, hex trace and span ids,
uint64 timestamps as strings), one ``Export*ServiceRequest`` per line holding exactly one
span or one log record. The bundle can therefore filter the driver's own files and the
collector's files with the same code (``filter_jsonl``).
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from google.protobuf.json_format import MessageToDict
from opentelemetry.exporter.otlp.proto.common._log_encoder import encode_logs
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk._logs import ReadableLogRecord
from opentelemetry.sdk._logs.export import LogExporter, LogExportResult
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from .attributes import ATTR_RUN_ID

_ID_KEYS = frozenset({"traceId", "spanId", "parentSpanId"})


class JsonlWriter:
    """Append-only, line-flushed JSON writer shared by several exporters.

    Every line is flushed as soon as it is written, so the file is valid after any
    interruption (design section 4: every file is valid after any interruption).
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._fh = None
        self.lines_written = 0

    def write(self, obj: Mapping[str, Any]) -> None:
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        with self._lock:
            if self._fh is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self.path, "a", encoding="utf-8")
            self._fh.write(line)
            self._fh.write("\n")
            self._fh.flush()
            self.lines_written += 1

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def _hex_ids(node: Any) -> None:
    """Rewrite base64 ``traceId``/``spanId``/``parentSpanId`` fields (protobuf JSON) to hex.

    The collector's pdata JSON marshaler writes ids as lowercase hex; protobuf's own JSON
    mapping writes bytes as base64. Converting makes both files identical in shape.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _ID_KEYS and isinstance(value, str):
                try:
                    node[key] = base64.b64decode(value).hex()
                except (ValueError, TypeError):
                    pass
            else:
                _hex_ids(value)
    elif isinstance(node, list):
        for item in node:
            _hex_ids(item)


def span_to_otlp_json(span: ReadableSpan) -> dict[str, Any]:
    """One span as an ``ExportTraceServiceRequest`` in collector-compatible JSON."""
    obj = MessageToDict(encode_spans([span]))
    _hex_ids(obj)
    return obj


def log_to_otlp_json(record: ReadableLogRecord) -> dict[str, Any]:
    """One log record as an ``ExportLogsServiceRequest`` in collector-compatible JSON."""
    obj = MessageToDict(encode_logs([record]))
    _hex_ids(obj)
    return obj


class JsonlSpanExporter(SpanExporter):
    def __init__(self, writer: JsonlWriter) -> None:
        self._writer = writer

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            for span in spans:
                self._writer.write(span_to_otlp_json(span))
        except Exception:  # noqa: BLE001 - never let evidence writing crash the caller
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def shutdown(self) -> None:
        self._writer.close()


class JsonlLogExporter(LogExporter):
    def __init__(self, writer: JsonlWriter) -> None:
        self._writer = writer

    def export(self, batch: Sequence[ReadableLogRecord]) -> LogExportResult:
        try:
            for record in batch:
                self._writer.write(log_to_otlp_json(record))
        except Exception:  # noqa: BLE001
            return LogExportResult.FAILURE
        return LogExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def shutdown(self) -> None:
        self._writer.close()


# --- readers -----------------------------------------------------------------------------


def iter_jsonl(path: str | os.PathLike[str]) -> Iterator[dict[str, Any]]:
    """Yield parsed objects; a truncated last line (interrupted write) is skipped silently."""
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _attribute_matches(node: Any, key: str, value: str) -> bool:
    """True if an OTLP attribute ``{"key": key, "value": {"stringValue": value}}`` occurs anywhere."""
    if isinstance(node, dict):
        if node.get("key") == key:
            val = node.get("value")
            if isinstance(val, dict) and str(val.get("stringValue", val.get("intValue"))) == value:
                return True
        return any(_attribute_matches(v, key, value) for v in node.values())
    if isinstance(node, list):
        return any(_attribute_matches(item, key, value) for item in node)
    return False


def line_carries_run_id(obj: Mapping[str, Any], run_id: str) -> bool:
    """A line carries a run id if any resource, scope or record attribute names it."""
    return _attribute_matches(obj, ATTR_RUN_ID, str(run_id))


def filter_jsonl(src: str | os.PathLike[str], dst: str | os.PathLike[str], run_id: str) -> int:
    """Append every line of ``src`` carrying ``run_id`` to ``dst``; returns the line count.

    Used at bundle time for ``results/lgtm/otlp/*.jsonl`` and for the host daemon's own
    ``spans.jsonl``/``logs.jsonl``. Missing sources count as zero lines.
    """
    src_path = Path(src)
    if not src_path.exists():
        return 0
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(dst_path, "a", encoding="utf-8") as out:
        for obj in iter_jsonl(src_path):
            if line_carries_run_id(obj, run_id):
                out.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
                out.write("\n")
                count += 1
    return count
