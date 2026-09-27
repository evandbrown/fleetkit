"""OpenTelemetry bootstrap shared by the fleetkit host daemon and driver (design section 8)."""
from __future__ import annotations

from .attributes import (
    ATTR_BACKEND,
    ATTR_HOST_ID,
    ATTR_RUN_ID,
    ATTR_MICROVM_ID,
    ATTR_TASK_ID,
    ATTR_TRIAL_ID,
    CORRELATION_KEYS,
    EVENT_MICROVM_STATE,
    SERVICE_DRIVER,
    SERVICE_GUEST,
    SERVICE_HOSTD,
    correlation_attributes,
)
from .bootstrap import (
    DEFAULT_OTLP_ENDPOINT,
    DEFAULT_OTLP_TIMEOUT_S,
    LOGS_FILE,
    SPANS_FILE,
    Telemetry,
    TelemetryConfig,
    init_telemetry,
)
from .cli import add_telemetry_args, config_from_args
from .guest import GuestEmission, clock_offset_ns, emit_guest_logs, emit_guest_task
from .jsonl import (
    JsonlLogExporter,
    JsonlSpanExporter,
    JsonlWriter,
    filter_jsonl,
    iter_jsonl,
    line_carries_run_id,
)
from .logs import JsonFormatter, log_microvm_state
from .propagation import (
    CorrelationSpanProcessor,
    correlation,
    extract_context,
    get_correlation,
    inject_headers,
    server_span,
)

__all__ = [
    "ATTR_BACKEND", "ATTR_HOST_ID", "ATTR_MICROVM_ID", "ATTR_RUN_ID", "ATTR_TASK_ID", "ATTR_TRIAL_ID",
    "CORRELATION_KEYS", "EVENT_MICROVM_STATE", "SERVICE_DRIVER", "SERVICE_GUEST", "SERVICE_HOSTD",
    "correlation_attributes", "DEFAULT_OTLP_ENDPOINT", "DEFAULT_OTLP_TIMEOUT_S", "LOGS_FILE", "SPANS_FILE",
    "Telemetry", "TelemetryConfig", "init_telemetry", "add_telemetry_args", "config_from_args",
    "GuestEmission", "clock_offset_ns", "emit_guest_logs", "emit_guest_task",
    "JsonlLogExporter", "JsonlSpanExporter", "JsonlWriter", "filter_jsonl", "iter_jsonl", "line_carries_run_id",
    "JsonFormatter", "log_microvm_state",
    "CorrelationSpanProcessor", "correlation", "extract_context", "get_correlation", "inject_headers", "server_span",
]
