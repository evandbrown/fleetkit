"""The ladder: which trial runs next, decided from the outcomes so far. Pure; no HTTP, no clock.

Rules (``driver trial``), over the spec's densities in the order given:

* Ladder: ``trials_per_density`` trials at each density. With ``stop_at_first_miss``, after all of a
  density's ladder trials the density is evaluated, and if it did not pass no higher density runs.
* Boundary trials (``boundary_trials`` K > 0), after the ladder: K more trials at the last passing
  density, then K more at the first failing density, each only if it exists.
* Walk-down: if any boundary trial at the last passing density fails, that density is now a miss;
  K more trials run at the next lower density that passed its ladder trials, and so on, until a
  density passes all of its trials or none is left. The first failing density stays a miss whatever
  its boundary trials do: a density passes only if every one of its trials passes.

The boundary is read with the report's rule: ``last_pass`` is the highest density that passed with
every lower density run also passing (the run's result, "tested successfully"); ``first_miss`` is the
lowest density run that did not pass. Boundary trials and the walk-down need densities in strictly
ascending order (the CLI checks this).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    trial_kind: str  # "ladder" | "boundary"
    density: int
    passed: bool
    trial_id: str = ""


@dataclass(frozen=True)
class Next:
    trial_kind: str  # "ladder" | "boundary"
    density: int


class LadderPlanner:
    def __init__(self, densities, trials_per_density: int = 1, boundary_trials: int = 0,
                 stop_at_first_miss: bool = False):
        self.densities = [int(x) for x in densities]
        self.trials_per_density = max(0, int(trials_per_density))
        self.boundary_trials = max(0, int(boundary_trials))
        self.stop_at_first_miss = bool(stop_at_first_miss)

    # ---- ladder phase ------------------------------------------------------------------------
    def _ladder_state(self, history) -> tuple[list[list[Outcome]], Next | None, bool]:
        """-> (outcomes per ladder position, the next ladder trial or None, stopped at a miss).
        Ladder trials fill positions in order, so a density listed twice is two positions."""
        runs = [o for o in history if o.trial_kind == "ladder"]
        per_pos: list[list[Outcome]] = []
        r = self.trials_per_density
        for i, density in enumerate(self.densities):
            got = runs[i * r:(i + 1) * r] if r else []
            per_pos.append(got)
            if len(got) < r:
                return per_pos, Next("ladder", density), False
            if self.stop_at_first_miss and got and not all(o.passed for o in got):
                return per_pos, None, i < len(self.densities) - 1
        return per_pos, None, False

    def _ladder_pass(self, per_pos) -> dict[int, bool]:
        out: dict[int, bool] = {}
        for density, got in zip(self.densities, per_pos):
            if got:
                out[density] = out.get(density, True) and all(o.passed for o in got)
        return out

    def _densities_run(self, per_pos) -> list[int]:
        seen: list[int] = []
        for density, got in zip(self.densities, per_pos):
            if got and density not in seen:
                seen.append(density)
        return seen

    @staticmethod
    def _boundary(densities: list[int], passed: dict[int, bool]) -> tuple[int | None, int | None]:
        last_pass = first_miss = None
        for density in densities:
            if passed.get(density):
                if first_miss is None:
                    last_pass = density
            elif first_miss is None:
                first_miss = density
        return last_pass, first_miss

    # ---- next --------------------------------------------------------------------------------
    def next(self, history) -> Next | None:
        per_pos, nxt, _ = self._ladder_state(history)
        if nxt is not None:
            return nxt
        k = self.boundary_trials
        if not k:
            return None
        run = self._densities_run(per_pos)
        lpass = self._ladder_pass(per_pos)
        last_pass, first_miss = self._boundary(run, lpass)
        checks: dict[int, list[bool]] = {}
        for o in history:
            if o.trial_kind == "boundary":
                checks.setdefault(o.density, []).append(o.passed)
        for target in (last_pass, first_miss):
            if target is not None and len(checks.get(target, [])) < k:
                return Next("boundary", target)
        cur = last_pass
        while cur is not None and not all(checks.get(cur, [])):
            lower = [d for d in run if d < cur and lpass.get(d)]
            if not lower:
                break
            cur = max(lower)
            if len(checks.get(cur, [])) < k:
                return Next("boundary", cur)
        return None

    # ---- summary (run.json plan) ------------------------------------------------------------
    def summary(self, history, interrupted: bool = False) -> dict:
        per_pos, nxt, stopped = self._ladder_state(history)
        run = self._densities_run(per_pos)
        lpass = self._ladder_pass(per_pos)
        by_density: dict[int, list[Outcome]] = {}
        for o in history:
            by_density.setdefault(o.density, []).append(o)
        passed = {d: all(o.passed for o in by_density.get(d, [])) and bool(by_density.get(d)) for d in run}
        last_pass, first_miss = self._boundary(run, passed)
        boundary_checks: list[dict] = []
        for o in history:
            if o.trial_kind != "boundary":
                continue
            entry = next((c for c in boundary_checks if c["density"] == o.density), None)
            if entry is None:
                entry = {"density": o.density, "trials": [], "passed": True}
                boundary_checks.append(entry)
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
            "densities": list(self.densities), "trials_per_density": self.trials_per_density,
            "boundary_trials": self.boundary_trials, "stop_at_first_miss": self.stop_at_first_miss,
            "densities_run": [{
                "density": d,
                "trials": [o.trial_id for o in by_density.get(d, [])],
                "ladder_trial_count": sum(1 for o in by_density.get(d, []) if o.trial_kind == "ladder"),
                "boundary_trial_count": sum(1 for o in by_density.get(d, []) if o.trial_kind == "boundary"),
                "ladder_passed": lpass.get(d, False),
                "passed": passed[d],
            } for d in run],
            "densities_not_run": [d for d in self.densities if d not in run],
            "stop_reason": stop_reason,
            "complete": done and not interrupted,
            "boundary": {
                "last_pass": last_pass, "first_miss": first_miss,
                "last_pass_trials": len(by_density.get(last_pass, [])) if last_pass is not None else 0,
                "first_miss_trials": len(by_density.get(first_miss, [])) if first_miss is not None else 0,
            },
            "boundary_checks": boundary_checks,
        }
