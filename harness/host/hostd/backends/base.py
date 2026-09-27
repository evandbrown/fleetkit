"""The backend interface the session manager drives.

A backend owns the per-session resources of one isolation technology. It never
touches session state: the manager transitions states and records timestamps;
the backend creates, checks, samples and destroys the thing behind a slot.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..model import Session
from ..runner import Runner


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

    def render(self, session: Session) -> str:
        """Everything create() would do, as a shell-like transcript, without doing it."""
        saved = self.runner
        self.runner = Runner(dry_run=True)
        try:
            self.create(session)
            self.destroy(session)
            return self.runner.render_text()
        finally:
            self.runner = saved

    # --- lifecycle -------------------------------------------------------
    def create(self, session: Session) -> None:
        """Allocate and start. Raises BackendError on failure (-> startup_error).

        Must fill session.handle with whatever destroy() needs and set
        session.console_log to the path the console/log stream is written to.
        """
        raise NotImplementedError

    def alive(self, session: Session) -> bool:
        """False once the container/VMM process is gone (-> startup_error during boot)."""
        raise NotImplementedError

    def destroy(self, session: Session) -> None:
        """Remove every per-session leftover. Idempotent; tolerates a partial create()."""
        raise NotImplementedError

    def exit_info(self, session: Session) -> Optional[str]:
        """Short description of why the process is gone, for the error field."""
        return None

    # --- sampling --------------------------------------------------------
    def sample(self, session: Session) -> Dict[str, Optional[int]]:
        """{rss_bytes, cgroup_memory_current, cgroup_memory_peak, cpu_usage_usec}; None where unknown."""
        return {"rss_bytes": None, "cgroup_memory_current": None, "cgroup_memory_peak": None, "cpu_usage_usec": None}

    def verify_clean(self) -> List[str]:
        """Leftovers this backend can see on the host; empty means clean."""
        raise NotImplementedError

    def check_host(self) -> List[str]:
        """Problems that would stop this backend from working here (empty = fine)."""
        return []
