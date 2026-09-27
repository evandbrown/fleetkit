"""MicroVM backends: `docker` (development), `firecracker` and `cloud-hypervisor` (measurement).

The two hypervisor backends share hypervisor.py; each takes the spec's hypervisor section per
microVM (MicroVM.hypervisor) and says what it can carry out in `hypervisor_options`.
"""
from __future__ import annotations

from typing import Any

from ..runner import Runner
from .base import Backend, BackendError
from .cloud_hypervisor import DEFAULT_CLOUD_HYPERVISOR, CloudHypervisorBackend
from .docker import DockerBackend
from .firecracker import DEFAULT_FIRECRACKER, FirecrackerBackend
from .hypervisor import HypervisorBackend

BACKENDS = {"docker": DockerBackend, "firecracker": FirecrackerBackend, "cloud-hypervisor": CloudHypervisorBackend}
# The spec's hypervisor.name -> backend.
HYPERVISORS = {"firecracker": FirecrackerBackend, "cloud-hypervisor": CloudHypervisorBackend}


def make_hypervisor_backend(name: str, runner: Runner, log_dir: str, **kwargs: Any) -> HypervisorBackend:
    """The backend for a spec's hypervisor.name. kwargs: binary, kernel, rootfs, run_root, ...
    (hypervisor.HypervisorBackend); binary defaults to the hypervisor's own path."""
    try:
        cls = HYPERVISORS[name]
    except KeyError:
        raise BackendError("no hypervisor backend %r (one of %s)" % (name, ", ".join(sorted(HYPERVISORS))))
    return cls(runner, log_dir, **kwargs)


__all__ = ["Backend", "BackendError", "DockerBackend", "FirecrackerBackend", "CloudHypervisorBackend",
           "HypervisorBackend", "BACKENDS", "HYPERVISORS", "make_hypervisor_backend", "DEFAULT_FIRECRACKER",
           "DEFAULT_CLOUD_HYPERVISOR"]
