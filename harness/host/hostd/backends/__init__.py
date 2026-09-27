"""MicroVM backends: `docker` (development) and `firecracker` (measurement)."""
from __future__ import annotations

from .base import Backend, BackendError
from .docker import DockerBackend
from .firecracker import FirecrackerBackend

BACKENDS = {"docker": DockerBackend, "firecracker": FirecrackerBackend}

__all__ = ["Backend", "BackendError", "DockerBackend", "FirecrackerBackend", "BACKENDS"]
