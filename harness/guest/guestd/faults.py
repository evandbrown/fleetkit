"""Fault injection, from ``FLEETKIT_FAULT`` (containers: environment; microVMs: pushed by
init from ``fleetkit.fault=`` on the kernel command line).

The closed set from design section 4 and its expected results:

- ``crash_on_start``   the daemon exits non-zero at once (microVM ``startup_error``)
- ``never_ready``      ``/health`` answers 503 forever (microVM ``startup_timeout``)
- ``hang_task``        ``POST /task`` never answers (task ``guest_unreachable`` at the host's
                       proxy deadline; the microVM is destroyed)
- ``hang_step``        hangs inside the ``search`` step, under the step deadline
                       (task ``step_timeout``, ``failed_step`` = ``search``; microVM stays ready)
- ``slow_step:<ms>``   sleeps ``ms`` inside the ``search`` step, under the step deadline
                       (``step_timeout`` when ms exceeds the step timeout)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FAULT_NAMES = ("crash_on_start", "never_ready", "hang_task", "hang_step", "slow_step")

# The step that hang_step and slow_step act on. Chosen so that a completed step (home)
# precedes it in the answer and the failed step is neither the first nor the last.
FAULT_STEP = "search"


@dataclass(frozen=True)
class Fault:
    name: str
    ms: int = 0

    def __str__(self) -> str:
        return f"{self.name}:{self.ms}" if self.name == "slow_step" else self.name


def parse_fault(text: Optional[str]) -> Optional[Fault]:
    """Parse a ``FLEETKIT_FAULT`` value; None for empty. Raises ValueError on an unknown fault."""
    if text is None:
        return None
    text = text.strip()
    if not text or text == "none":
        return None
    name, _, arg = text.partition(":")
    name = name.strip()
    if name not in FAULT_NAMES:
        raise ValueError(f"unknown fault {text!r}; expected one of {', '.join(FAULT_NAMES)}")
    if name == "slow_step":
        try:
            ms = int(arg.strip())
        except ValueError:
            raise ValueError(f"slow_step needs an integer millisecond argument, got {text!r}") from None
        if ms < 0:
            raise ValueError("slow_step milliseconds must be non-negative")
        return Fault(name, ms)
    if arg:
        raise ValueError(f"fault {name} takes no argument, got {text!r}")
    return Fault(name)
