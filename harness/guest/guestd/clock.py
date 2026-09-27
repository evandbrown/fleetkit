"""Deadlines: the one mechanism behind step timeouts, task timeouts and DevTools waits.

Section 6 of the design: ``step_timeout_ms`` wraps every step and ``task_timeout_ms``
wraps the task, using the same deadline mechanism as the DevTools waits. A deadline is an
absolute point on the monotonic clock; awaiting through it never sleeps, it only bounds
how long an await may take.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Iterable, TypeVar

T = TypeVar("T")


class DeadlineExpired(Exception):
    """Raised by :meth:`Deadline.wait` when the awaited operation outlives the deadline."""

    def __init__(self, deadline: "Deadline", what: str = "") -> None:
        self.deadline = deadline
        self.what = what
        label = deadline.label
        ms = int(round(deadline.budget_s * 1000))
        super().__init__(f"{label} deadline of {ms} ms expired" + (f" while {what}" if what else ""))


class Deadline:
    """An absolute monotonic deadline with a label (``"step"`` or ``"task"``)."""

    __slots__ = ("at", "label", "budget_s")

    def __init__(self, at: float, label: str, budget_s: float) -> None:
        self.at = at
        self.label = label
        self.budget_s = budget_s

    @classmethod
    def after(cls, seconds: float, label: str, start: float = None) -> "Deadline":  # type: ignore[assignment]
        base = time.monotonic() if start is None else start
        return cls(base + seconds, label, seconds)

    @classmethod
    def after_ms(cls, ms: float, label: str, start: float = None) -> "Deadline":  # type: ignore[assignment]
        return cls.after(ms / 1000.0, label, start)

    @staticmethod
    def earliest(deadlines: Iterable["Deadline"]) -> "Deadline":
        """The deadline that fires first; on a tie the task's own deadline wins."""
        best = None
        for d in deadlines:
            if best is None or d.at < best.at or (d.at == best.at and d.label == "task"):
                best = d
        if best is None:
            raise ValueError("no deadlines")
        return best

    def remaining(self) -> float:
        return max(0.0, self.at - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= self.at

    async def wait(self, aw: Awaitable[T], what: str = "") -> T:
        """Await ``aw`` but give up (cancelling it) when the deadline passes."""
        try:
            return await asyncio.wait_for(aw, timeout=self.remaining())
        except asyncio.TimeoutError:
            raise DeadlineExpired(self, what) from None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Deadline({self.label}, remaining={self.remaining():.3f}s)"


def monotonic_ns() -> int:
    return time.monotonic_ns()


def elapsed_ms(start_ns: int, end_ns: int = None) -> float:  # type: ignore[assignment]
    end = time.monotonic_ns() if end_ns is None else end_ns
    return (end - start_ns) / 1_000_000.0


async def never() -> Any:
    """An await that never completes; only ever used under a deadline (fault injection)."""
    await asyncio.Event().wait()
