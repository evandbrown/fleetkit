"""Structured JSON logging plus the OpenTelemetry logs bridge."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk._logs import LoggingHandler
from opentelemetry.trace import format_span_id, format_trace_id

from .attributes import ATTR_SESSION_ID, EVENT_SESSION_STATE
from .propagation import get_correlation

_STANDARD_RECORD_KEYS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "taskName"}

# Marker set on handlers this package installs, so re-initialisation replaces them.
HANDLER_MARK = "_fleetkit_telemetry"


def record_extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_RECORD_KEYS}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts (Unix seconds), level, logger, msg, trace ids, extras."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "msg": record.getMessage(),
        }
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            out["trace_id"] = format_trace_id(span_context.trace_id)
            out["span_id"] = format_span_id(span_context.span_id)
        out.update(record_extras(record))
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


class CorrelationLogFilter(logging.Filter):
    """Stamps the current ``fleetkit.*`` baggage onto the record (explicit extras win)."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in get_correlation().items():
            if key not in record.__dict__:
                record.__dict__[key] = value
        return True


class DropOwnRecords(logging.Filter):
    """Keeps the OpenTelemetry SDK's own records (export failures) out of the OTLP bridge."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry")


class RateLimitOtelNoise(logging.Filter):
    """Lets one SDK export-failure record per logger through per ``interval_s``.

    A collector that is down produces a warning and an error per batch; best-effort export
    should be visible once, not on every batch.
    """

    def __init__(self, interval_s: float = 60.0) -> None:
        super().__init__()
        self.interval_s = interval_s
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("opentelemetry"):
            return True
        now = time.monotonic()
        with self._lock:
            last = self._last.get(record.name)
            if last is not None and now - last < self.interval_s:
                return False
            self._last[record.name] = now
        return True


def log_session_state(
    logger: logging.Logger,
    session_id: str,
    from_state: str | None,
    to_state: str,
    ts: float,
    outcome: str | None = None,
    **extra: Any,
) -> None:
    """Emit the ``session.state`` record of design section 4 with trace context.

    Field names are namespaced (``session.from``, ``session.to``, ``session.ts``,
    ``session.outcome``) so they never collide with the record's own ``ts``.
    """
    fields: dict[str, Any] = {
        "event.name": EVENT_SESSION_STATE,
        ATTR_SESSION_ID: session_id,
        "session.from": from_state if from_state is not None else "",
        "session.to": to_state,
        "session.ts": float(ts),
    }
    if outcome is not None:
        fields["session.outcome"] = outcome
    fields.update(extra)
    logger.info("%s %s %s -> %s", EVENT_SESSION_STATE, session_id, from_state, to_state, extra=fields)


class OtelBridgeHandler(LoggingHandler):
    """The SDK's stdlib bridge, made safe for ``logging.shutdown`` at interpreter exit.

    The SDK handler's ``flush`` starts a thread to force-flush the provider; after our own
    shutdown, or once the interpreter is finalising, that raises ``RuntimeError``. Closing
    the handler makes ``flush`` a no-op and deregisters it from logging's handler list.
    """

    def __init__(self, level: int, logger_provider: Any) -> None:
        super().__init__(level=level, logger_provider=logger_provider)
        self._closed = False

    def flush(self) -> None:
        if self._closed:
            return
        try:
            super().flush()
        except RuntimeError:
            pass

    def close(self) -> None:
        self._closed = True
        try:
            super().close()
        finally:
            logging.Handler.close(self)
