"""argparse helpers so the host daemon and the driver take identical telemetry switches."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from .bootstrap import DEFAULT_OTLP_ENDPOINT, DEFAULT_OTLP_TIMEOUT_S, TelemetryConfig


def add_telemetry_args(parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    group = parser.add_argument_group("telemetry")
    group.add_argument(
        "--no-lgtm",
        dest="lgtm",
        action="store_false",
        default=True,
        help="do not export to the LGTM collector; spans and logs still go to the JSONL files",
    )
    group.add_argument(
        "--otlp-endpoint",
        default=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT),
        help="OTLP/HTTP base URL of the collector (default: %(default)s)",
    )
    group.add_argument(
        "--otlp-timeout",
        type=float,
        default=DEFAULT_OTLP_TIMEOUT_S,
        help="seconds per best-effort OTLP export (default: %(default)s)",
    )
    group.add_argument(
        "--host-id",
        default=os.environ.get("FLEETKIT_HOST_ID"),
        help="fleetkit.host_id and host.name resource attributes (default: $FLEETKIT_HOST_ID, else 'local'; "
        "pass the instance id on AWS, never something identifying)",
    )
    return group


def config_from_args(
    args: argparse.Namespace,
    service_name: str,
    jsonl_dir: Path | str | None = None,
    log_file: Path | str | None = None,
    **overrides: Any,
) -> TelemetryConfig:
    cfg = TelemetryConfig(
        service_name=service_name,
        jsonl_dir=jsonl_dir,
        lgtm=getattr(args, "lgtm", True),
        otlp_endpoint=getattr(args, "otlp_endpoint", DEFAULT_OTLP_ENDPOINT),
        otlp_timeout_s=getattr(args, "otlp_timeout", DEFAULT_OTLP_TIMEOUT_S),
        host_id=getattr(args, "host_id", None),
        log_file=log_file,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg
