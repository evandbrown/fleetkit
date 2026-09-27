"""Build the tracer, meter and logs bridge for one process.

    tel = init_telemetry(TelemetryConfig(service_name="driver", jsonl_dir=run_dir))
    with tel.tracer.start_as_current_span("trial"), tel.correlation(run_id=..., trial_id=...):
        headers = tel.inject({"Content-Type": "application/json"})
        ...
    tel.shutdown()

Exports: the components' own ``spans.jsonl`` and ``logs.jsonl`` in ``jsonl_dir`` (always,
written synchronously so evidence never depends on the collector) and OTLP/HTTP to the
LGTM collector (best-effort, batched, ``otlp_timeout_s`` per export, off with ``lgtm=False``).
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, MutableMapping, Sequence, TextIO

from opentelemetry import _logs as otel_logs, metrics as otel_metrics, trace as otel_trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.trace import SpanKind

from . import attributes as A
from .guest import GuestEmission, emit_guest_logs, emit_guest_task
from .jsonl import JsonlLogExporter, JsonlSpanExporter, JsonlWriter
from .logs import (
    HANDLER_MARK,
    CorrelationLogFilter,
    DropOwnRecords,
    JsonFormatter,
    OtelBridgeHandler,
    RateLimitOtelNoise,
    log_session_state,
)
from .propagation import (
    PROPAGATOR,
    CorrelationSpanProcessor,
    correlation,
    extract_context,
    inject_headers,
    server_span,
)

SPANS_FILE = "spans.jsonl"
LOGS_FILE = "logs.jsonl"
DEFAULT_OTLP_ENDPOINT = "http://127.0.0.1:4318"
DEFAULT_OTLP_TIMEOUT_S = 2.0


@dataclass
class TelemetryConfig:
    service_name: str
    jsonl_dir: Path | str | None = None
    lgtm: bool = True
    otlp_endpoint: str = DEFAULT_OTLP_ENDPOINT
    otlp_timeout_s: float = DEFAULT_OTLP_TIMEOUT_S
    host_id: str | None = None
    resource_attributes: dict[str, Any] = field(default_factory=dict)
    log_level: int = logging.INFO
    log_stream: TextIO | None = None  # default: sys.stderr; pass False-y stream object to disable
    log_to_stream: bool = True
    log_file: Path | str | None = None
    metrics_interval_s: float = 5.0
    set_global: bool = True
    root_logger: bool = True


DEFAULT_HOST_ID = "local"


def default_host_id() -> str:
    """``FLEETKIT_HOST_ID`` or ``local``. Never the machine's hostname: it can carry the
    operator's name, and every span and log record in a shared bundle would repeat it."""
    return os.environ.get("FLEETKIT_HOST_ID") or DEFAULT_HOST_ID


class Telemetry:
    """Everything a component needs; build one per process with ``init_telemetry``."""

    def __init__(self, config: TelemetryConfig) -> None:
        self.config = config
        self.service_name = config.service_name
        self.host_id = config.host_id or default_host_id()
        self.jsonl_dir = Path(config.jsonl_dir) if config.jsonl_dir else None
        self.otlp_enabled = bool(config.lgtm and config.otlp_endpoint)
        endpoint = (config.otlp_endpoint or "").rstrip("/")
        timeout = float(config.otlp_timeout_s)

        base_attrs: dict[str, Any] = {
            "service.instance.id": f"{self.host_id}:{os.getpid()}",
            "host.name": self.host_id,  # derived from --host-id so the override covers every attribute
            "process.pid": os.getpid(),
            A.ATTR_HOST_ID: self.host_id,
        }
        base_attrs.update(config.resource_attributes)
        self.resource = Resource.create({**base_attrs, "service.name": config.service_name})
        guest_attrs = {k: v for k, v in base_attrs.items() if k != "process.pid"}
        guest_attrs["service.instance.id"] = self.host_id  # one guest-daemon "instance" per host
        self.guest_resource = Resource.create(
            {**guest_attrs, "service.name": A.SERVICE_GUEST, "fleetkit.emitted_by": config.service_name}
        )

        self.span_writer = JsonlWriter(self.jsonl_dir / SPANS_FILE) if self.jsonl_dir else None
        self.log_writer = JsonlWriter(self.jsonl_dir / LOGS_FILE) if self.jsonl_dir else None

        # Traces: one provider per resource (host component, guest daemon).
        self.tracer_provider = self._tracer_provider(self.resource, endpoint, timeout)
        self.guest_tracer_provider = self._tracer_provider(self.guest_resource, endpoint, timeout)
        self.tracer = self.tracer_provider.get_tracer("fleetkit." + config.service_name)
        self.guest_tracer = self.guest_tracer_provider.get_tracer("fleetkit.guestd")

        # Logs: same split.
        self.logger_provider = self._logger_provider(self.resource, endpoint, timeout)
        self.guest_logger_provider = self._logger_provider(self.guest_resource, endpoint, timeout)
        self.guest_log_emitter = self.guest_logger_provider.get_logger("fleetkit.guestd")

        # Metrics: OTLP only (host_metrics.csv is the durable copy); no reader without LGTM.
        readers = []
        if self.otlp_enabled:
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=timeout),
                    export_interval_millis=int(config.metrics_interval_s * 1000),
                    export_timeout_millis=int(timeout * 1000),
                )
            )
        self.meter_provider = MeterProvider(metric_readers=readers, resource=self.resource, shutdown_on_exit=False)
        self.meter = self.meter_provider.get_meter("fleetkit." + config.service_name)

        self._handlers: list[logging.Handler] = []
        self.logger = logging.getLogger(config.service_name)
        self._install_logging()

        if config.set_global:
            otel_trace.set_tracer_provider(self.tracer_provider)
            otel_metrics.set_meter_provider(self.meter_provider)
            otel_logs.set_logger_provider(self.logger_provider)
            set_global_textmap(PROPAGATOR)

    # --- construction ------------------------------------------------------------------

    def _tracer_provider(self, resource: Resource, endpoint: str, timeout: float) -> TracerProvider:
        provider = TracerProvider(resource=resource, shutdown_on_exit=False)
        provider.add_span_processor(CorrelationSpanProcessor())
        if self.span_writer is not None:
            provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(self.span_writer)))
        if self.otlp_enabled:
            exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=timeout)
            provider.add_span_processor(BatchSpanProcessor(exporter, export_timeout_millis=int(timeout * 1000)))
        return provider

    def _logger_provider(self, resource: Resource, endpoint: str, timeout: float) -> LoggerProvider:
        provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
        if self.log_writer is not None:
            provider.add_log_record_processor(SimpleLogRecordProcessor(JsonlLogExporter(self.log_writer)))
        if self.otlp_enabled:
            exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", timeout=timeout)
            provider.add_log_record_processor(BatchLogRecordProcessor(exporter, export_timeout_millis=int(timeout * 1000)))
        return provider

    def _install_logging(self) -> None:
        cfg = self.config
        root = logging.getLogger() if cfg.root_logger else self.logger
        for handler in list(root.handlers):
            if getattr(handler, HANDLER_MARK, False):
                root.removeHandler(handler)
                handler.close()
        correlation_filter = CorrelationLogFilter()
        noise_filter = RateLimitOtelNoise()
        formatter = JsonFormatter(cfg.service_name)

        if cfg.log_to_stream:
            stream = cfg.log_stream if cfg.log_stream is not None else sys.stderr
            handler = logging.StreamHandler(stream)
            handler.setFormatter(formatter)
            handler.addFilter(correlation_filter)
            handler.addFilter(noise_filter)
            self._handlers.append(handler)
        if cfg.log_file:
            path = Path(cfg.log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.addFilter(correlation_filter)
            file_handler.addFilter(noise_filter)
            self._handlers.append(file_handler)
        otel_handler = OtelBridgeHandler(level=cfg.log_level, logger_provider=self.logger_provider)
        otel_handler.addFilter(correlation_filter)
        otel_handler.addFilter(DropOwnRecords())
        self._handlers.append(otel_handler)

        for handler in self._handlers:
            setattr(handler, HANDLER_MARK, True)
            handler.setLevel(cfg.log_level)
            root.addHandler(handler)
        if root.level == logging.NOTSET or root.level > cfg.log_level:
            root.setLevel(cfg.log_level)
        self._root = root

    # --- propagation --------------------------------------------------------------------

    def inject(self, headers: MutableMapping[str, str] | None = None, context: Context | None = None):
        """Add W3C ``traceparent``/``tracestate``/``baggage`` headers for an outgoing request."""
        return inject_headers(headers, context)

    def extract(self, headers: Any) -> Context:
        return extract_context(headers)

    def server_span(self, name: str, headers: Any, attributes: Mapping[str, Any] | None = None, kind: SpanKind = SpanKind.SERVER):
        return server_span(self.tracer, name, headers, attributes=attributes, kind=kind)

    @contextmanager
    def correlation(self, **keys: Any) -> Iterator[Context]:
        with correlation(**keys) as ctx:
            yield ctx

    # --- guest --------------------------------------------------------------------------

    def emit_guest_task(self, response: Mapping[str, Any], **kwargs: Any) -> GuestEmission:
        """See ``fleetkit_telemetry.guest.emit_guest_task``; uses the guest-daemon tracer."""
        return emit_guest_task(self.guest_tracer, response, **kwargs)

    def emit_guest_logs(self, log_tail: Sequence[Any] | None, **kwargs: Any) -> int:
        return emit_guest_logs(self.guest_log_emitter, log_tail, **kwargs)

    def log_session_state(self, session_id: str, from_state: str | None, to_state: str, ts: float, outcome: str | None = None, **extra: Any) -> None:
        log_session_state(self.logger, session_id, from_state, to_state, ts, outcome, **extra)

    # --- lifecycle ----------------------------------------------------------------------

    def force_flush(self, timeout_s: float = 5.0) -> bool:
        millis = int(timeout_s * 1000)
        ok = True
        for provider in (self.tracer_provider, self.guest_tracer_provider):
            ok = provider.force_flush(millis) and ok
        for lp in (self.logger_provider, self.guest_logger_provider):
            ok = lp.force_flush(millis) and ok
        try:
            ok = self.meter_provider.force_flush(millis) and ok
        except Exception:  # noqa: BLE001 - metrics are best-effort
            ok = False
        return ok

    def shutdown(self, timeout_s: float = 5.0) -> None:
        self.force_flush(timeout_s)
        for handler in self._handlers:
            try:
                self._root.removeHandler(handler)
            except Exception:  # noqa: BLE001
                pass
        for provider in (self.tracer_provider, self.guest_tracer_provider):
            provider.shutdown()
        for lp in (self.logger_provider, self.guest_logger_provider):
            lp.shutdown()
        try:
            self.meter_provider.shutdown(timeout_millis=int(timeout_s * 1000))
        except Exception:  # noqa: BLE001
            pass
        for handler in self._handlers:
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass
        for writer in (self.span_writer, self.log_writer):
            if writer is not None:
                writer.close()


def init_telemetry(config: TelemetryConfig | None = None, **kwargs: Any) -> Telemetry:
    """Create the process-wide telemetry; ``init_telemetry(service_name="hostd", jsonl_dir=...)``."""
    if config is None:
        config = TelemetryConfig(**kwargs)
    return Telemetry(config)
