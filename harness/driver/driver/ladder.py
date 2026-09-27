"""The ladder: which trial runs next, decided from the outcomes so far. Pure; no HTTP, no clock.

Rules (``driver trial``):

* Ladder: ``repeats`` trials per level, in the order given. With ``stop_at_first_miss``, after all
  repeats of a level the level is evaluated, and if it did not pass no higher level runs.
* Confirmations (``confirm_repeats`` K > 0), after the ladder: K more trials at the last passing
  level, then K more at the first failing level, each only if it exists.
* Walk-down: if any confirmation of the last passing level fails, that level is now a miss; K more
  trials run at the next lower level that passed its ladder trials, and so on, until a level passes
  all of its trials or none is left. The first failing level stays a miss whatever its
  confirmations do: a level passes only if every one of its trials passes.

The boundary is read with the report's headline rule: ``last_pass`` is the highest level that passed
with every lower level run also passing; ``first_miss`` is the lowest level run that did not pass.
Confirmations and the walk-down need levels in strictly ascending order (the CLI checks this).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    kind: str  # "ladder" | "confirm"
    level: int
    passed: bool
    trial_id: str = ""


@dataclass(frozen=True)
class Next:
    kind: str  # "ladder" | "confirm"
    level: int


class LadderPlanner:
    def __init__(self, ladder, repeats: int = 1, confirm_repeats: int = 0, stop_at_first_miss: bool = False):
        self.ladder = [int(x) for x in ladder]
        self.repeats = max(0, int(repeats))
        self.confirm_repeats = max(0, int(confirm_repeats))
        self.stop_at_first_miss = bool(stop_at_first_miss)

    # ---- ladder phase ------------------------------------------------------------------------
    def _ladder_state(self, history) -> tuple[list[list[Outcome]], Next | None, bool]:
        """-> (outcomes per ladder position, the next ladder trial or None, stopped at a miss).
        Ladder trials fill positions in order, so a level listed twice is two positions."""
        runs = [o for o in history if o.kind == "ladder"]
        per_pos: list[list[Outcome]] = []
        r = self.repeats
        for i, level in enumerate(self.ladder):
            got = runs[i * r:(i + 1) * r] if r else []
            per_pos.append(got)
            if len(got) < r:
                return per_pos, Next("ladder", level), False
            if self.stop_at_first_miss and got and not all(o.passed for o in got):
                return per_pos, None, i < len(self.ladder) - 1
        return per_pos, None, False

    def _ladder_pass(self, per_pos) -> dict[int, bool]:
        out: dict[int, bool] = {}
        for level, got in zip(self.ladder, per_pos):
            if got:
                out[level] = out.get(level, True) and all(o.passed for o in got)
        return out

    def _run_levels(self, per_pos) -> list[int]:
        seen: list[int] = []
        for level, got in zip(self.ladder, per_pos):
            if got and level not in seen:
                seen.append(level)
        return seen

    @staticmethod
    def _boundary(levels: list[int], passed: dict[int, bool]) -> tuple[int | None, int | None]:
        last_pass = first_miss = None
        for level in levels:
            if passed.get(level):
                if first_miss is None:
                    last_pass = level
            elif first_miss is None:
                first_miss = level
        return last_pass, first_miss

    # ---- next --------------------------------------------------------------------------------
    def next(self, history) -> Next | None:
        per_pos, nxt, _ = self._ladder_state(history)
        if nxt is not None:
            return nxt
        k = self.confirm_repeats
        if not k:
            return None
        levels = self._run_levels(per_pos)
        lpass = self._ladder_pass(per_pos)
        last_pass, first_miss = self._boundary(levels, lpass)
        confirms: dict[int, list[bool]] = {}
        for o in history:
            if o.kind == "confirm":
                confirms.setdefault(o.level, []).append(o.passed)
        for target in (last_pass, first_miss):
            if target is not None and len(confirms.get(target, [])) < k:
                return Next("confirm", target)
        cur = last_pass
        while cur is not None and not all(confirms.get(cur, [])):
            lower = [lv for lv in levels if lv < cur and lpass.get(lv)]
            if not lower:
                break
            cur = max(lower)
            if len(confirms.get(cur, [])) < k:
                return Next("confirm", cur)
        return None

    # ---- summary (run.json plan) ------------------------------------------------------------
    def summary(self, history, interrupted: bool = False) -> dict:
        per_pos, nxt, stopped = self._ladder_state(history)
        levels = self._run_levels(per_pos)
        lpass = self._ladder_pass(per_pos)
        by_level: dict[int, list[Outcome]] = {}
        for o in history:
            by_level.setdefault(o.level, []).append(o)
        passed = {lv: all(o.passed for o in by_level.get(lv, [])) and bool(by_level.get(lv)) for lv in levels}
        last_pass, first_miss = self._boundary(levels, passed)
        confirmations: list[dict] = []
        for o in history:
            if o.kind != "confirm":
                continue
            entry = next((c for c in confirmations if c["level"] == o.level), None)
            if entry is None:
                entry = {"level": o.level, "trials": [], "passed": True}
                confirmations.append(entry)
            entry["trials"].append(o.trial_id)
            entry["passed"] = entry["passed"] and o.passed
        done = self.next(history) is None
        if interrupted:
            stop_reason = "interrupted"
        elif not done:
            stop_reason = None  # still running
        elif stopped:
            stop_reason = "first_miss"
        else:
            stop_reason = "ladder_complete"
        return {
            "ladder": list(self.ladder), "repeats": self.repeats, "confirm_repeats": self.confirm_repeats,
            "stop_at_first_miss": self.stop_at_first_miss,
            "levels_run": [{
                "level": lv,
                "trials": [o.trial_id for o in by_level.get(lv, [])],
                "ladder_trials": sum(1 for o in by_level.get(lv, []) if o.kind == "ladder"),
                "confirm_trials": sum(1 for o in by_level.get(lv, []) if o.kind == "confirm"),
                "ladder_passed": lpass.get(lv, False),
                "passed": passed[lv],
            } for lv in levels],
            "levels_not_run": [lv for lv in self.ladder if lv not in levels],
            "stop_reason": stop_reason,
            "complete": done and not interrupted,
            "boundary": {
                "last_pass": last_pass, "first_miss": first_miss,
                "last_pass_trials": len(by_level.get(last_pass, [])) if last_pass is not None else 0,
                "first_miss_trials": len(by_level.get(first_miss, [])) if first_miss is not None else 0,
            },
            "confirmations": confirmations,
        }
