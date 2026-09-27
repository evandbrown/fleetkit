"""The backend interface the microVM manager drives.

A backend owns the per-microVM resources of one isolation technology. It never
touches microVM state: the manager transitions states and records timestamps;
the backend creates, checks, samples and destroys the thing behind a slot.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..model import MicroVM
from ..runner import Runner

# Per-microVM figures in GET /host/metrics, in export order. The first four are section 4's;
# the rest are the Firecracker CPU split, cgroup throttling and cgroup pressure (null elsewhere).
MICROVM_FIGURES = ("rss_bytes", "cgroup_memory_current", "cgroup_memory_peak", "cpu_usage_usec",
                   "cpu_vcpu_usec", "cpu_hypervisor_usec", "cpu_throttled_usec", "cpu_nr_throttled",
                   "cpu_pressure_some_total_us", "cpu_pressure_full_total_us", "memory_pressure_some_total_us")


def empty_sample() -> Dict[str, Optional[int]]:
    """Every per-microVM figure, unknown."""
    return {k: None for k in MICROVM_FIGURES}


def public_path(path: str) -> str:
    """`path` with the operator's home directory shown as `~`, for records that get bundled."""
    home = os.path.expanduser("~")
    if home and home != os.sep and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


class BackendError(Exception):
    pass


class Backend:
    name: str = "base"
    max_slots: int = 0
    fixture_base_url: str = ""

    def __init__(self, runner: Runner, log_dir: str):
        self.runner = runner
        self.log_dir = log_dir

    # --- planning --------------------------------------------------------
    def address(self, slot: int) -> str:
        """`host:port` the guest daemon answers on for this slot."""
        raise NotImplementedError

    def slot_usable(self, slot: int) -> bool:
        """False when something outside this daemon holds the slot's resources (e.g. its host port)."""
        return True

    def render(self, microvm: MicroVM) -> str:
        """Everything create() would do, as a shell-like transcript, without doing it."""
        saved = self.runner
        self.runner = Runner(dry_run=True)
        try:
            self.create(microvm)
            self.destroy(microvm)
            return self.runner.render_text()
        finally:
            self.runner = saved

    # --- lifecycle -------------------------------------------------------
    def create(self, microvm: MicroVM) -> None:
        """Allocate and start. Raises BackendError on failure (-> startup_error).

        Must fill microvm.handle with whatever destroy() needs and set
        microvm.console_log to the path the console/log stream is written to.
        """
        raise NotImplementedError

    def alive(self, microvm: MicroVM) -> bool:
        """False once the container or hypervisor process is gone (-> startup_error during boot)."""
        raise NotImplementedError

    def destroy(self, microvm: MicroVM) -> None:
        """Remove every per-microVM leftover. Idempotent; tolerates a partial create()."""
        raise NotImplementedError

    def exit_info(self, microvm: MicroVM) -> Optional[str]:
        """Short description of why the process is gone, for the error field."""
        return None

    # --- sampling --------------------------------------------------------
    def sample(self, microvm: MicroVM) -> Dict[str, Optional[int]]:
        """Every key of MICROVM_FIGURES; None where unknown."""
        return empty_sample()

    def info(self) -> Optional[Dict[str, Any]]:
        """Static facts about this backend's setup for GET /host/info (None when there are none)."""
        return None

    def verify_clean(self) -> List[str]:
        """Leftovers this backend can see on the host; empty means clean."""
        raise NotImplementedError

    def check_host(self) -> List[str]:
        """Problems that would stop this backend from working here (empty = fine)."""
        return []
