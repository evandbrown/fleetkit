"""A run's spec: read it, check it, and refuse what this host can't carry out.

``driver trial --spec FILE`` runs one run from a spec. FILE is what
``experiments/schema/expand.py CAMPAIGN --run RUN`` prints (the spec with its campaign, run,
spec name and replica around it) or a bare spec. It is checked with expand.py itself, so the
driver, the launcher and the site's builder apply the same rules (docs/spec.md).

Every value that isn't a spec field is fixed here, the same in every run (``HARNESS``), and
recorded in run.json beside the spec. The command-line options for those values are refused
with ``--spec``, so a run directory's recorded spec is always what ran.
"""
from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXPAND_PY = REPO / "experiments" / "schema" / "expand.py"

# The same in every run, so not fields (docs/spec.md, "The same in every run").
HARNESS = {
    "warmup_trials": 1,            # one density-1 trial before the ladder, labelled, not judged
    "illustration": True,          # one density-1 trial with a screenshot after every step, last, not judged
    "stop_at_first_miss": True,    # the run goes no higher than the first density that doesn't pass
    "metrics_hz": 5.0,             # worker host samples per second (hostd runs with --metrics-period 0.2)
    "sample_interval_ms": 200,     # each microVM samples its own processes during its task
    "reaper_margin_s": 120.0,      # hostd's reapers, a backstop only: idle after ready_timeout_s plus the wait
                                   # after ready plus this, lifetime after ready_timeout_s + the wait after
                                   # ready + task timeout + 2.5x this (RunSpec.timeouts)
    "reruns": 1,                   # a trial that fails outside the experiment runs again once (driver/clean.py)
}

# Options that set a spec field or a HARNESS value; with --spec they come from there only.
SPEC_OWNED_OPTIONS = (
    "--densities", "--trials-per-density", "--boundary-trials", "--settle-s", "--warmup", "--illustration",
    "--stop-at-first-miss", "--metrics-hz", "--sample-interval-ms", "--step-p50-target-ms",
    "--step-p95-target-ms", "--task-p95-target-ms", "--ready-timeout-s", "--step-timeout-ms",
    "--task-timeout-ms", "--vcpus", "--mem-mib", "--fault", "--max-lifetime-s", "--idle-timeout-s",
    "--launch-interval-ms", "--backend", "--no-host-metrics", "--no-fixture-probe", "--release-after-ready-s",
)


class SpecError(Exception):
    """The spec can't be run as given; ``problems`` says why, each starting with its field."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass
class RunSpec:
    spec: dict
    campaign: str | None = None
    run: str | None = None
    spec_name: str | None = None
    replica: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def hypervisor(self) -> dict:
        return dict(self.spec["hypervisor"])

    @property
    def backend(self) -> str:
        return self.spec["hypervisor"]["name"]

    @property
    def densities(self) -> list[int]:
        return [int(d) for d in self.spec["densities"]]

    @property
    def release_after_ready_s(self) -> float:
        """Seconds each trial waits once every microVM is ready before it releases the tasks. Optional in
        the spec (specs recorded before it existed leave it out): absent is 0, the tasks start at once."""
        return float(self.spec["procedure"].get("release_after_ready_s", 0))

    def timeouts(self) -> dict:
        """The trial's timeouts: the spec's three, and hostd's reapers set well clear of them. A microVM
        sits ready and idle from its own ready until the release, at most ready_timeout_s plus the wait after
        ready, so both reapers allow for the wait; with no wait they are what they always were."""
        c, m, w = self.spec["criteria"], HARNESS["reaper_margin_s"], self.release_after_ready_s
        return {"ready_timeout_s": float(c["ready_timeout_s"]), "step_timeout_ms": int(c["step_timeout_ms"]),
                "task_timeout_ms": int(c["task_timeout_ms"]),
                "idle_timeout_s": float(c["ready_timeout_s"]) + w + m,
                "max_lifetime_s": float(c["ready_timeout_s"]) + w + c["task_timeout_ms"] / 1000.0 + 2.5 * m,
                "launch_interval_ms": 0}

    def record(self) -> dict:
        """run.json's fields for this spec: the resolved spec, where it came from, the fixed values."""
        out = {"spec": self.spec, "harness": dict(HARNESS)}
        for k in ("campaign", "run", "spec_name", "replica"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        return out


_expand = None


def expand_module():
    """experiments/schema/expand.py, loaded once by path (the harness's venv doesn't install it)."""
    global _expand
    if _expand is None:
        if not EXPAND_PY.exists():
            raise SpecError([f"cannot check the spec: {EXPAND_PY.relative_to(REPO)} is missing"])
        mod_spec = importlib.util.spec_from_file_location("fleetkit_expand", EXPAND_PY)
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        _expand = mod
    return _expand


def check(spec) -> list[str]:
    """The spec's errors under the same rules expand.py applies to every spec in a campaign."""
    ex = expand_module()
    errs = ex.validate(spec, ex.SCHEMAS["spec.schema.json"])
    if not errs:
        errs, _ = ex.check_spec(spec)
    return errs


def load(path) -> RunSpec:
    try:
        doc = expand_module().read_json(path)
    except Exception as exc:  # unreadable, not JSON, a repeated key, NaN
        raise SpecError([f"{path}: {exc}"]) from None
    if isinstance(doc, dict) and isinstance(doc.get("spec"), dict):
        rs = RunSpec(spec=doc["spec"], campaign=doc.get("campaign"), run=doc.get("run"),
                     spec_name=doc.get("spec_name"), replica=doc.get("replica"),
                     extra={k: v for k, v in doc.items()
                            if k not in ("spec", "campaign", "run", "spec_name", "replica")})
    elif isinstance(doc, dict):
        rs = RunSpec(spec=doc)
    else:
        raise SpecError([f"{path}: a spec is a JSON object"])
    errs = check(rs.spec)
    if errs:
        raise SpecError(errs)
    return rs


def refusals(rs: RunSpec, health: dict | None, host_info: dict | None, local_docker: bool = False) -> list[str]:
    """What this host daemon can't carry out of the spec, from its /health and /host/info.
    A spec value the harness can't honour is refused before any trial, never approximated.
    ``local_docker`` (``--backend docker``): a local check of the run on Docker, which has no hypervisor
    and no instance type, so only the daemon's backend and its slots are checked. The run records
    backend docker, so it is never a result (D39)."""
    out: list[str] = []
    info = host_info if isinstance(host_info, dict) else {}
    backend = (health or {}).get("backend") or info.get("backend")
    if local_docker:
        if backend and backend != "docker":
            out.append(f"--backend docker: this host's daemon runs {backend}, not docker")
        slots = info.get("max_slots")
        if isinstance(slots, int) and max(rs.densities) > slots:
            out.append(f"densities: {max(rs.densities)} microVMs at once is more than this host daemon's "
                       f"{slots} slots")
        return out
    if backend and backend != rs.backend:
        out.append(f"hypervisor.name: this worker host's daemon runs {backend}, not {rs.backend}")
    # A daemon older than the spec reports no options: it offers only mmio without a random-number device.
    offered = info.get("hypervisor_options") or {"virtio_transport": ["mmio"], "virtio_rng": [False]}
    for key in ("virtio_transport", "virtio_rng"):
        value = rs.spec["hypervisor"][key]
        ok = [v for v in offered.get(key, []) if v == value and type(v) is type(value)]
        if not ok:
            have = ", ".join(json.dumps(v) for v in offered.get(key, [])) or "nothing"
            out.append(f"hypervisor.{key}: the {backend or rs.backend} backend here can't carry out "
                       f"{json.dumps(value)} (it offers {have})")
    slots = info.get("max_slots")
    if isinstance(slots, int) and max(rs.densities) > slots:
        out.append(f"densities: {max(rs.densities)} microVMs at once is more than this host daemon's "
                   f"{slots} slots")
    ec2 = info.get("ec2") if isinstance(info.get("ec2"), dict) else None
    want = rs.spec["worker_host"]["instance_type"]
    if ec2 and ec2.get("instance_type") and ec2["instance_type"] != want:
        out.append(f"worker_host.instance_type: this worker host is {ec2['instance_type']}, not {want}")
    return out


def same_spec(recorded, rs: RunSpec) -> bool:
    return isinstance(recorded, dict) and expand_module().same(recorded, rs.spec)
