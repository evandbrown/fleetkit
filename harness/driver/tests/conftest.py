from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from driver.cli import main as driver_main  # noqa: E402
from tests.stub_fixture import StubFixture  # noqa: E402
from tests.stub_hostd import StubServer  # noqa: E402

# Small timeouts so the suite runs in seconds; the driver's protocol is timeout-agnostic.
FAST = ["--step-timeout-ms", "300", "--task-timeout-ms", "1000", "--ready-timeout-s", "3",
        "--proxy-margin-ms", "300", "--client-margin-ms", "300", "--no-lgtm"]


@pytest.fixture
def fixture_site():
    with StubFixture() as f:
        yield f


@pytest.fixture
def hostd(tmp_path):
    with StubServer(telemetry_dir=str(tmp_path / "hostd"), proxy_margin_ms=300, startup_ms=60, step_ms=10) as s:
        s.telemetry_dir = tmp_path / "hostd"
        yield s


def run_cli(*args: str) -> int:
    return driver_main([str(a) for a in args])


def common(hostd, fixture_site, out: Path, backend: str = "docker") -> list[str]:
    return ["--backend", backend, "--host-url", hostd.url, "--fixture-check-url", fixture_site.url,
            "--out", str(out), "--quiet", *FAST]
