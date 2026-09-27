from __future__ import annotations

import pytest

import fleetkit_telemetry as ft


@pytest.fixture
def make_tel(tmp_path):
    """Build isolated Telemetry instances (no globals, no stderr, no root handlers)."""
    created = []

    def _make(service_name="hostd", jsonl=True, **overrides):
        cfg = ft.TelemetryConfig(
            service_name=service_name,
            jsonl_dir=(tmp_path / service_name) if jsonl else None,
            lgtm=False,
            set_global=False,
            root_logger=False,
            log_to_stream=False,
        )
        for key, value in overrides.items():
            setattr(cfg, key, value)
        tel = ft.init_telemetry(cfg)
        created.append(tel)
        return tel

    yield _make
    for tel in created:
        tel.shutdown()
