"""W3C trace context and baggage over plain ``http.client``/``urllib`` calls.

There is no framework instrumentation in the harness: the caller injects headers into its
outgoing request and the server extracts them from the request it received. Correlation
keys travel as W3C baggage next to ``traceparent`` and are stamped onto every span and log
record by ``CorrelationSpanProcessor`` and ``CorrelationLogFilter``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping, MutableMapping

from opentelemetry import baggage, context as otel_context, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.trace import Span, SpanProcessor
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .attributes import CORRELATION_PREFIX, correlation_attributes

PROPAGATOR = CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])


def inject_headers(
    headers: MutableMapping[str, str] | None = None, context: Context | None = None
) -> MutableMapping[str, str]:
    """Add ``traceparent``, ``tracestate`` and ``baggage`` for the current (or given) context."""
    carrier: MutableMapping[str, str] = headers if headers is not None else {}
    PROPAGATOR.inject(carrier, context=context)
    return carrier


def _lower_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else headers
    out: dict[str, str] = {}
    for key, value in items:
        if value is None:
            continue
        out[str(key).lower()] = value if isinstance(value, str) else str(value)
    return out


def extract_context(headers: Any, context: Context | None = None) -> Context:
    """Build a context from request headers (dict, ``http.client.HTTPMessage`` or item pairs)."""
    return PROPAGATOR.extract(_lower_headers(headers), context=context)


def get_correlation(context: Context | None = None) -> dict[str, str]:
    """The ``fleetkit.*`` baggage entries in the given (default: current) context."""
    entries = baggage.get_all(context)
    return {k: str(v) for k, v in entries.items() if k.startswith(CORRELATION_PREFIX)}


@contextmanager
def correlation(context: Context | None = None, **keys: Any) -> Iterator[Context]:
    """Attach correlation keys as baggage for the duration of the block.

    ``with tel.correlation(run_id=run, trial_id=trial):`` makes every span started and
    every log record emitted inside the block carry those attributes, and every request
    injected inside it carry them as baggage.
    """
    ctx = context if context is not None else otel_context.get_current()
    for name, value in correlation_attributes(**keys).items():
        ctx = baggage.set_baggage(name, value, context=ctx)
    token = otel_context.attach(ctx)
    try:
        yield ctx
    finally:
        otel_context.detach(token)


@contextmanager
def server_span(
    tracer: trace.Tracer,
    name: str,
    headers: Any,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.SERVER,
) -> Iterator[Span]:
    """Extract the caller's context from ``headers``, attach it (span and baggage) and open a span."""
    ctx = extract_context(headers)
    token = otel_context.attach(ctx)
    try:
        with tracer.start_as_current_span(name, kind=kind, attributes=attributes) as span:
            yield span
    finally:
        otel_context.detach(token)


class CorrelationSpanProcessor(SpanProcessor):
    """Copies ``fleetkit.*`` baggage onto every span at start (explicit attributes win)."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        existing = span.attributes or {}
        for key, value in get_correlation(parent_context).items():
            if key not in existing:
                span.set_attribute(key, value)

    def on_end(self, span: Any) -> None:  # pragma: no cover - nothing to do
        return None

    def shutdown(self) -> None:  # pragma: no cover
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # pragma: no cover
        return True
