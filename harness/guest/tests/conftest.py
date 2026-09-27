"""Shared fixtures: the package is importable from the source tree; a free port helper."""

from __future__ import annotations

import os
import shutil
import socket
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

CHROMIUM_CANDIDATES = (
    "/usr/lib/chromium/chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


def find_chromium() -> str | None:
    env = os.environ.get("GUESTD_CHROMIUM")
    if env and os.path.exists(env):
        return env
    for path in CHROMIUM_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def chromium_binary() -> str:
    path = find_chromium()
    if path is None:
        pytest.skip("no Chromium/Chrome binary found (set GUESTD_CHROMIUM)")
    return path
