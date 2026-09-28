"""Command line: ``python3 -m driver {trial,report,smoke,bundle}`` (design section 4, "Driver CLI")."""
from __future__ import annotations

import argparse
import copy
import json
import os
import signal
import sys
import time
from pathlib import Path

from . import __version__, schemas
from .config import (DEFAULT_FIXTURE_BASE_URL, DEFAULT_FIXTURE_CHECK_URL, DEFAULT_HOST_URL, DEFAULT_OTLP_ENDPOINT,
                     DEFAULT_SAMPLE_INTERVAL_MS, Timeouts, TrialConfig)
from .clean import SupportHealth, outside_cause
from .criteria import make_criteria
from .hostclient import HostClient, HostError
from .ladder import LadderPlanner, Outcome
from .metrics import HostMetricsSampler
from .outputs import LegacyRunDirError, RunDir, read_json
from .spec import HARNESS, SPEC_OWNED_OPTIONS, RunSpec, SpecError, load as load_spec, refusals, same_spec
from .telemetry import Tracer
from .trial import TrialRunner


def _default_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()


def _add_common(p: argparse.ArgumentParser, need_backend: bool = True) -> None:
    p.add_argument("--backend", choices=("docker", "firecracker", "cloud-hypervisor"), required=need_backend,
                   help="the host daemon's backend (with --spec: the spec's hypervisor.name, or docker to check "
                        "the run locally; a Docker run is never a result)")
    p.add_argument("--host-url", default=DEFAULT_HOST_URL, help="host daemon base URL")
    p.add_argument("--fixture-check-url", default=DEFAULT_FIXTURE_CHECK_URL,
                   help="fixture URL the driver checks before a trial")
    p.add_argument("--fixture-base-url", default=None,
                   help="fixture URL the guest uses (default: http://fixture on docker, http://10.200.0.1:8081 on firecracker)")
    p.add_argument("--products", default=None,
                   help="task-products.json URL or path (default: <fixture-check-url>/task-products.json, then fixture/dist/)")
    p.add_argument("--out", required=True, help="run directory, results/<run-id>")
    p.add_argument("--run-id", default=None, help="default: the basename of --out")
    p.add_argument("--host-id", default=os.environ.get("FLEETKIT_HOST_ID") or None,
                   help="fleetkit.host_id correlation key (default: $FLEETKIT_HOST_ID, else 'local'; never the "
                        "hostname, which can carry the operator's name into a shared bundle)")
    p.add_argument("--vcpus", type=int, default=2)
    p.add_argument("--mem-mib", type=int, default=2048)
    p.add_argument("--step-timeout-ms", type=int, default=Timeouts.step_timeout_ms)
    p.add_argument("--task-timeout-ms", type=int, default=Timeouts.task_timeout_ms)
    p.add_argument("--ready-timeout-s", type=float, default=Timeouts.ready_timeout_s,
                   help="60 local; 90 on AWS for the first density-1 trial, then 3x the observed startup_ms")
    p.add_argument("--max-lifetime-s", type=float, default=Timeouts.max_lifetime_s)
    p.add_argument("--idle-timeout-s", type=float, default=Timeouts.idle_timeout_s)
    p.add_argument("--launch-interval-ms", type=int, default=Timeouts.launch_interval_ms)
    p.add_argument("--proxy-margin-ms", type=int, default=Timeouts.proxy_margin_ms, help=argparse.SUPPRESS)
    p.add_argument("--client-margin-ms", type=int, default=Timeouts.client_margin_ms, help=argparse.SUPPRESS)
    p.add_argument("--otlp-endpoint", default=DEFAULT_OTLP_ENDPOINT)
    p.add_argument("--no-lgtm", action="store_true", help="do not export to the collector; JSONL files only")
    p.add_argument("--no-host-metrics", action="store_true", help="skip 1 Hz host metrics sampling")
    p.add_argument("--quiet", action="store_true", help="do not echo the log to stdout")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="driver", description="fleetkit driver: trial, report, smoke, bundle")
    p.add_argument("--version", action="version", version=f"driver {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("trial", help="run trials at several densities and write the run directory")
    _add_common(t, need_backend=False)
    t.add_argument("--spec", default=None,
                   help="run one run from a spec (what experiments/schema/expand.py CAMPAIGN --run RUN prints): the "
                        "densities, procedure, criteria, microVM and hypervisor come from it, and the options that "
                        "would set them are refused")
    t.add_argument("--support-health-url", default=None,
                   help="the support host's health service (http://<support>:8082); with --spec, a trial during "
                        "which the support host was unhealthy or overloaded is not a result and runs again")
    t.add_argument("--densities", default="2,4",
                   help="comma-separated densities to test, in order (microVMs started at once per trial), e.g. 2,4")
    t.add_argument("--trials-per-density", type=int, default=1, help="ladder trials at each density")
    t.add_argument("--fault", default=None, help="fault name for every microVM (crash_on_start, never_ready, "
                                                  "hang_task, hang_step, slow_step:<ms>); the trials are labelled "
                                                  "fault-<name>, not numbered")
    t.add_argument("--step-p50-target-ms", type=float, default=None, help="pass criterion: every step's p50 (unset: not checked)")
    t.add_argument("--step-p95-target-ms", type=float, default=None, help="pass criterion: every step's p95 (unset: not checked)")
    t.add_argument("--task-p95-target-ms", type=float, default=None, help="pass criterion: task_ms p95 (unset: not checked)")
    t.add_argument("--stop-at-first-miss", action="store_true",
                   help="after a density's ladder trials, run no higher density if it did not pass; a miss is then a "
                        "result, so the exit code is non-zero only for trial errors (fixture, host daemon, verify-clean)")
    t.add_argument("--boundary-trials", type=int, default=0,
                   help="after the ladder, K more trials at the last passing and the first failing density, walking "
                        "down while a boundary trial at the last pass fails")
    t.add_argument("--warmup", type=int, default=0,
                   help="K trials at density 1 before the ladder (trial kind warmup, labelled, not evaluated)")
    t.add_argument("--settle-s", type=float, default=0.0,
                   help="wait S seconds before each trial, recording host cpu_util as trial.json pre_trial")
    t.add_argument("--release-after-ready-s", type=float, default=0.0,
                   help="once every microVM of a trial is ready, wait S seconds before releasing the tasks together "
                        "(a warm start; 0, the default, releases them at once); --ready-timeout-s plus S must be "
                        "below --idle-timeout-s")
    t.add_argument("--illustration", action="store_true",
                   help="finish with one density-1 trial with untimed per-step screenshots (trial kind illustration)")
    t.add_argument("--metrics-hz", type=float, default=1.0,
                   help="host metrics sampling rate; start the host daemon with a matching --metrics-period")
    t.add_argument("--sample-interval-ms", type=int, default=DEFAULT_SAMPLE_INTERVAL_MS,
                   help="guest process sampling interval sent with every task (0 disables)")
    t.add_argument("--no-fixture-probe", action="store_true",
                   help="do not time a GET of <fixture-check-url>/ at 1 Hz (fixture_rtt_ms in host_metrics.csv)")

    r = sub.add_parser("report", help="report from a run directory (works from the bundle alone)")
    r.add_argument("--run", required=True)
    r.add_argument("--out", default=None,
                   help="where to write report.json and report.md (default: the run directory; required for a run "
                        "directory from before the glossary, which is never rewritten)")
    r.add_argument("--price-per-hour", type=float, default=None, help="public on-demand USD per hour (assumed)")
    r.add_argument("--instance", default=None)
    # criteria: from these flags when any is given, else from run.json ``criteria`` (the run's own)
    r.add_argument("--step-p50-target-ms", "--step-target-ms", dest="step_target_ms", type=float, default=None,
                   help="target for step p50 (default: run.json criteria)")
    r.add_argument("--step-p95-target-ms", "--step-p95-ms", dest="step_p95_ms", type=float, default=None,
                   help="target for step p95 (default: run.json criteria)")
    r.add_argument("--task-p95-target-ms", "--task-target-ms", dest="task_target_ms", type=float, default=None,
                   help="target for task p95 (default: run.json criteria)")
    r.add_argument("--s3-get-price-per-1000", type=float, default=0.0004)
    r.add_argument("--fixture-manifest", default=None, help="fixture manifest with expected bytes/requests per task")
    r.add_argument("--bytes-tolerance", type=float, default=0.25)
    r.add_argument("--quiet", action="store_true")

    s = sub.add_parser("smoke", help="the smoke suite: enumerated assertions, smoke-report.md, green counter")
    _add_common(s)
    s.add_argument("--densities", default="2,4", help="densities for assertion 1, one trial at each")
    s.add_argument("--idle-case-s", type=float, default=5.0)
    s.add_argument("--lifetime-case-s", type=float, default=8.0)
    s.add_argument("--never-ready-timeout-s", type=float, default=10.0)
    s.add_argument("--crash-bound-s", type=float, default=10.0)
    s.add_argument("--case-grace-s", type=float, default=15.0)
    s.add_argument("--lgtm-dir", default=None, help="results/lgtm (otlp/ and data/ mounts)")
    s.add_argument("--hostd-dir", default=None, help="where the host daemon writes spans.jsonl/logs.jsonl/hostd.log")
    s.add_argument("--until-green", type=int, default=None,
                   help="run rounds until this many consecutive green rounds")
    s.add_argument("--max-rounds", type=int, default=10)
    s.add_argument("--budget-s", type=float, default=3 * 3600)

    b = sub.add_parser("bundle", help="assemble the evidence bundle")
    b.add_argument("--run", required=True)
    b.add_argument("--strict", action="store_true", help="exit non-zero if any mandatory item is missing")
    b.add_argument("--lgtm-dir", default=None, help="default: <run>/../lgtm when it exists")
    b.add_argument("--snapshot-lgtm", action="store_true", help="compose stop, tar data/, compose start")
    b.add_argument("--compose-file", default=None)
    b.add_argument("--hostd-dir", default=None)
    b.add_argument("--hostd-log", default=None)
    b.add_argument("--console-logs-dir", default=None)
    b.add_argument("--cloud-init-log", default=None)
    b.add_argument("--hostcheck-output", default=None)
    b.add_argument("--aws", action="store_true", help="AWS items (cloud-init, hostcheck, console logs) are mandatory")
    b.add_argument("--lock-env", default=None)
    b.add_argument("--guest-manifest", default=None)
    b.add_argument("--ami-lock", default=None)
    b.add_argument("--ami-id", default=None)
    b.add_argument("--instance-type", default=None)
    b.add_argument("--vcpu-quota", type=int, default=None)
    b.add_argument("--host-provisioning-s", type=float, default=None)
    b.add_argument("--git-commit", default=None)
    b.add_argument("--quiet", action="store_true")
    return p


def _timeouts(a) -> Timeouts:
    return Timeouts(step_timeout_ms=a.step_timeout_ms, task_timeout_ms=a.task_timeout_ms,
                    ready_timeout_s=a.ready_timeout_s, max_lifetime_s=a.max_lifetime_s,
                    idle_timeout_s=a.idle_timeout_s, launch_interval_ms=a.launch_interval_ms,
                    proxy_margin_ms=a.proxy_margin_ms, client_margin_ms=a.client_margin_ms)


class Invocation:
    """Per-invocation resources: run directory, tracer, host client, writers, metrics sampler."""

    def __init__(self, a):
        self.rundir = RunDir(Path(a.out), a.run_id)
        self.rundir.refuse_if_legacy()
        self.run_id = self.rundir.run_id
        self.host_id = a.host_id or "local"
        self.tracer = Tracer(self.rundir.spans_jsonl, self.rundir.logs_jsonl, self.rundir.driver_log,
                             otlp_endpoint=None if a.no_lgtm else a.otlp_endpoint,
                             resource_attributes={"fleetkit.run_id": self.run_id, "fleetkit.host_id": self.host_id,
                                                  "fleetkit.backend": a.backend},
                             stdout=not a.quiet)
        self.client = HostClient(a.host_url, run_id=self.run_id)
        self.writers = self.rundir.open_writers()
        self.sampler = None
        self.rundir.update_run_json(host_id=self.host_id, started_ts=time.time(), backend=a.backend,
                                    host_url=a.host_url, driver_version=__version__)
        self.tracer.info(f"driver {__version__} {a.command}", run_id=self.run_id, out=str(self.rundir.root),
                         host_url=a.host_url, backend=a.backend)
        try:
            h = self.client.health()
            self.tracer.info("host daemon healthy", health=json.dumps(h)[:200])
        except HostError as exc:
            self.tracer.error(f"host daemon not reachable at {a.host_url}: {exc}")
            raise
        if not a.no_host_metrics:
            hz = float(getattr(a, "metrics_hz", None) or 1.0)
            probe = None
            if a.command == "trial" and not getattr(a, "no_fixture_probe", True):
                probe = a.fixture_check_url
            self.sampler = HostMetricsSampler(self.client, self.writers.host_metrics, interval_s=1.0 / hz,
                                              log=self.tracer, fixture_url=probe).start()

    def close(self) -> None:
        if self.sampler:
            self.sampler.stop()
            self.tracer.info("host metrics sampling stopped", samples=self.sampler.samples, errors=self.sampler.errors,
                             duplicate_samples_skipped=self.sampler.duplicates, fixture_probes=self.sampler.probes,
                             fixture_probe_errors=self.sampler.probe_errors)
        self.rundir.update_run_json(ended_ts=time.time())
        self.writers.close()
        self.tracer.close()


def _install_signals() -> None:
    def on_term(signum, frame):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, on_term)
    except (ValueError, OSError):
        pass


def _run_spec(a, criteria: dict, densities: list[int]) -> dict:
    """run.json ``spec``: everything fixed about this run, as the driver was asked to carry it out.
    Measured facts (the host's GET /host/info, the guest's own report) go in ``observed`` instead."""
    from .bundle import _git_commit
    return {
        "argv": list(getattr(a, "_argv", None) or []),
        "options": {k: v for k, v in vars(a).items() if not k.startswith("_")},
        "criteria": criteria, "densities": densities, "trials_per_density": a.trials_per_density,
        "boundary_trials": a.boundary_trials, "stop_at_first_miss": a.stop_at_first_miss, "warmup": a.warmup,
        "settle_s": a.settle_s, "release_after_ready_s": a.release_after_ready_s,
        "illustration": a.illustration, "metrics_hz": a.metrics_hz,
        "sample_interval_ms": a.sample_interval_ms,
        "fixture_probe": not a.no_fixture_probe and not a.no_host_metrics,
        "fixture_check_url": a.fixture_check_url,
        "fixture_base_url": a.fixture_base_url or DEFAULT_FIXTURE_BASE_URL.get(a.backend),
        "otlp_endpoint": None if a.no_lgtm else a.otlp_endpoint,
        "git_commit": os.environ.get("FLEETKIT_GIT_COMMIT") or _git_commit(),
    }


def _first_guest_info(runner: TrialRunner) -> dict | None:
    for s in sorted(runner.microvms, key=lambda x: x.slot):
        if s.ready and isinstance(s.info.get("guest_info"), dict):
            return s.info["guest_info"]
    return None


def _refuse_legacy(exc: LegacyRunDirError) -> int:
    print(str(exc), file=sys.stderr)
    return 2


def _spec_mode(a, argv: list[str]) -> RunSpec:
    """Read and check --spec, refuse options that would override it, and set ``a`` from it."""
    given = sorted({tok.split("=", 1)[0] for tok in argv} & set(SPEC_OWNED_OPTIONS))
    # The one override: --backend docker checks the run end to end on this machine. Docker has no hypervisor,
    # so the spec's hypervisor section isn't sent, and the run records backend docker: never a result (D39).
    if a.backend == "docker":
        given = [opt for opt in given if opt != "--backend"]
    if given:
        raise SpecError([f"{opt}: comes from the spec with --spec" for opt in given])
    rs = load_spec(a.spec)
    s, c, p = rs.spec, rs.spec["criteria"], rs.spec["procedure"]
    t = rs.timeouts()
    a.backend = "docker" if a.backend == "docker" else rs.backend
    a.densities = ",".join(str(d) for d in rs.densities)
    a.trials_per_density, a.boundary_trials, a.settle_s = p["trials_per_density"], p["boundary_trials"], float(p["settle_s"])
    a.release_after_ready_s = rs.release_after_ready_s  # timeouts() already allows the reapers for it
    a.warmup, a.illustration = HARNESS["warmup_trials"], HARNESS["illustration"]
    a.stop_at_first_miss, a.fault = HARNESS["stop_at_first_miss"], None
    a.metrics_hz, a.sample_interval_ms = HARNESS["metrics_hz"], HARNESS["sample_interval_ms"]
    a.no_host_metrics = a.no_fixture_probe = False
    a.step_p50_target_ms, a.step_p95_target_ms = c["step_p50_target_ms"], c["step_p95_target_ms"]
    a.task_p95_target_ms = c["task_p95_target_ms"]
    a.ready_timeout_s, a.step_timeout_ms, a.task_timeout_ms = t["ready_timeout_s"], t["step_timeout_ms"], t["task_timeout_ms"]
    a.idle_timeout_s, a.max_lifetime_s, a.launch_interval_ms = t["idle_timeout_s"], t["max_lifetime_s"], t["launch_interval_ms"]
    a.vcpus, a.mem_mib = s["microvm"]["vcpus"], s["microvm"]["memory_mib"]
    return rs


def _refuse_reuse(out: Path, rs: RunSpec) -> str | None:
    """A run directory holds one spec: refuse one that holds trials of anything else."""
    prior = read_json(out / "run.json", None)
    has_trials = (out / "trials").exists() and any((out / "trials").iterdir())
    if isinstance(prior, dict) and isinstance(prior.get("spec"), dict) and "argv" in prior["spec"]:
        return f"{out} already holds trials run without this spec; use a new --out directory"
    if isinstance(prior, dict) and "spec" in prior:
        if not same_spec(prior["spec"], rs):
            return f"{out} already holds a run of a different spec; use a new --out directory"
    elif has_trials:
        return f"{out} already holds trials run without this spec; use a new --out directory"
    return None


def _resume(inv: "Invocation") -> tuple[list[Outcome], int, bool]:
    """The trials a run directory of this spec already holds: the ladder history, how many warm-up
    trials ran, whether the illustration ran. A trial cut off before it finished is set aside."""
    history: list[Outcome] = []
    warmups, illustrated = 0, False
    for t in inv.rundir.list_trials():
        tid = t.get("trial_id")
        if not t.get("complete"):
            mids = [m.get("microvm_id") for m in t.get("microvms") or []]
            where = inv.rundir.set_aside(tid, inv.writers, mids)
            inv.rundir.log_ops("trial_set_aside", trial_id=tid, density=t.get("density"),
                               trial_kind=t.get("trial_kind"), cause="interrupted before it finished",
                               set_aside=str(where.relative_to(inv.rundir.root)), next="run again")
            continue
        kind = t.get("trial_kind")
        if kind in ("ladder", "boundary") and t.get("passed") is not None:
            history.append(Outcome(kind, int(t["density"]), bool(t["passed"]), tid))
        elif kind == "warmup":
            warmups += 1
        elif kind == "illustration":
            illustrated = True
    if history or warmups or illustrated:
        inv.rundir.log_ops("resumed", trials=len(history), warmups=warmups, illustration=illustrated)
        inv.tracer.info(f"resuming: {len(history)} trials already in the run directory")
    return history, warmups, illustrated


def cmd_trial(a) -> int:
    rs: RunSpec | None = None
    if a.spec:
        try:
            rs = _spec_mode(a, list(getattr(a, "_argv", None) or []))
        except SpecError as exc:
            for prob in exc.problems:
                print(f"spec: {prob}", file=sys.stderr)
            return 2
    elif not a.backend:
        print("--backend is required without --spec", file=sys.stderr)
        return 2
    densities = [int(x) for x in a.densities.split(",") if x.strip()]
    if not densities or any(n < 1 for n in densities):
        print("--densities must list positive integers", file=sys.stderr)
        return 2
    ladder_mode = a.stop_at_first_miss or a.boundary_trials > 0
    if ladder_mode and any(hi <= lo for lo, hi in zip(densities, densities[1:])):
        print("--stop-at-first-miss and --boundary-trials need --densities in strictly ascending order", file=sys.stderr)
        return 2
    if (a.metrics_hz <= 0 or a.boundary_trials < 0 or a.warmup < 0 or a.settle_s < 0 or a.sample_interval_ms < 0
            or a.release_after_ready_s < 0):
        print("--metrics-hz must be positive; --boundary-trials, --warmup, --settle-s, --sample-interval-ms and "
              "--release-after-ready-s must not be negative", file=sys.stderr)
        return 2
    # A microVM ready at once sits idle until the last is ready (up to --ready-timeout-s) and then through the wait,
    # so the reapers must allow for both, as RunSpec.timeouts does with --spec. Checked only with a wait, so a run
    # without one is refused nothing it wasn't before.
    wait = a.release_after_ready_s
    if wait > 0 and (a.ready_timeout_s + wait >= a.idle_timeout_s
                     or a.ready_timeout_s + wait + a.task_timeout_ms / 1000.0 >= a.max_lifetime_s):
        print("--release-after-ready-s: --ready-timeout-s plus the wait must be below --idle-timeout-s, and with "
              "--task-timeout-ms below --max-lifetime-s, or hostd's reapers destroy microVMs during the wait",
              file=sys.stderr)
        return 2
    if rs is not None:
        why = _refuse_reuse(Path(a.out), rs)
        if why:
            print(why, file=sys.stderr)
            return 2
        probe = HostClient(a.host_url)
        try:
            health = probe.health()
        except HostError as exc:
            print(f"host daemon not reachable at {a.host_url}: {exc}", file=sys.stderr)
            return 3
        probs = refusals(rs, health if isinstance(health, dict) else None, probe.host_info(),
                         local_docker=a.backend == "docker")
        if probs:
            for prob in probs:
                print(f"spec: {prob}", file=sys.stderr)
            return 2
    _install_signals()
    try:
        inv = Invocation(a)
    except LegacyRunDirError as exc:
        return _refuse_legacy(exc)
    except HostError:
        return 3
    criteria = make_criteria(a.step_p50_target_ms, a.step_p95_target_ms, a.task_p95_target_ms)
    planner = LadderPlanner(densities, a.trials_per_density, a.boundary_trials, a.stop_at_first_miss)
    history: list[Outcome] = []
    docs: list[dict] = []
    rc = 0
    interrupted = not_clean = False
    support = SupportHealth(a.support_health_url) if rs is not None and a.support_health_url else None
    support_said_nothing = False
    try:
        observed = {"host_info": inv.client.host_info(),
                    "guest_info": None}  # filled from the first ready microVM's record
        if rs is not None:
            record = rs.record()
            record["harness"].update(_run_harness(a, rs))
            inv.rundir.update_run_json(criteria=criteria, observed=observed, **record)
            history, warmups_done, illustrated = _resume(inv)
        else:
            inv.rundir.update_run_json(criteria=criteria, spec=_run_spec(a, criteria, densities), observed=observed)
            warmups_done, illustrated = 0, False
        inv.rundir.update_run_json(plan=planner.summary(history))
        base = _timeouts(a)
        fault_label = f"fault-{schemas.fault_name(a.fault)}" if a.fault else None

        def run_one(density: int, trial_kind: str, label: str | None = None, shots: bool = False):
            cfg = TrialConfig(run_id=inv.run_id, backend=a.backend, density=density,
                              timeouts=copy.deepcopy(base), vcpus=a.vcpus, mem_mib=a.mem_mib, fault=a.fault,
                              fixture_check_url=a.fixture_check_url, fixture_base_url=a.fixture_base_url,
                              products_source=a.products, host_id=inv.host_id, trial_label=fault_label or label,
                              trial_kind="fault" if a.fault else trial_kind, criteria=criteria, settle_s=a.settle_s,
                              sample_interval_ms=a.sample_interval_ms, screenshot_each_step=shots,
                              hypervisor=rs.hypervisor if rs is not None and a.backend != "docker" else None,
                              release_after_ready_s=a.release_after_ready_s)
            runner = TrialRunner(cfg, inv.client, inv.rundir, inv.writers, inv.tracer, metrics=inv.sampler)
            doc, harness_error = None, None
            try:
                doc = runner.run()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if rs is None:
                    raise
                harness_error = f"{type(exc).__name__}: {exc}"
                doc = read_json(inv.rundir.trials_dir / runner.trial_id / "trial.json", None)
            finally:
                if observed["guest_info"] is None:
                    gi = _first_guest_info(runner)
                    if gi is not None:
                        observed["guest_info"] = gi
                        inv.rundir.update_run_json(observed=observed)
            return runner, doc, harness_error

        def run_clean(density: int, trial_kind: str, label: str | None = None, shots: bool = False):
            """-> (the trial's doc, None) when it is a result; (None, cause) when it couldn't be run
            cleanly even after running it again. Without a spec every trial is a result, as before."""
            nonlocal support_said_nothing
            if rs is None:
                _, doc, _ = run_one(density, trial_kind, label, shots)
                docs.append(doc)
                return doc, None
            cause = None
            for attempt in range(1, HARNESS["reruns"] + 2):
                t0 = time.time()
                runner, doc, harness_error = run_one(density, trial_kind, label, shots)
                t1 = time.time()
                health, health_error = None, None
                try:
                    health = inv.client.health()
                except HostError as exc:
                    health_error = str(exc)
                said = None
                if support is not None:
                    try:
                        said = support.window(t0, t1)
                    except Exception as exc:
                        if not support_said_nothing:
                            support_said_nothing = True
                            inv.rundir.log_ops("support_health_unavailable", url=a.support_health_url,
                                               error=f"{type(exc).__name__}: {exc}")
                            inv.tracer.warn(f"the support host's health service did not answer: {exc}")
                cause = outside_cause(doc, harness_error=harness_error, trial_s=t1 - t0,
                                      health_after=health if isinstance(health, dict) else None,
                                      health_error=health_error, support=said)
                if cause is None:
                    docs.append(doc)
                    return doc, None
                again = attempt <= HARNESS["reruns"]
                mids = [m.get("microvm_id") for m in (doc or {}).get("microvms") or []]
                where = inv.rundir.set_aside(runner.trial_id, inv.writers, mids)
                inv.rundir.log_ops("trial_set_aside", trial_id=runner.trial_id, density=density,
                                   trial_kind=trial_kind, attempt=attempt, cause=cause,
                                   set_aside=str(where.relative_to(inv.rundir.root)),
                                   next="run again" if again else "give up")
                inv.tracer.warn(f"trial {runner.trial_id} is not a result ({cause}); "
                                + ("running it again" if again else "not running it a third time"))
            return None, cause

        for _ in range(max(0, a.warmup - warmups_done)):
            run_clean(1, "warmup", "warmup")
        while True:
            nxt = planner.next(history)
            if nxt is None:
                break
            doc, cause = run_clean(nxt.density, nxt.trial_kind)
            if doc is None:
                not_clean = True
                if not any(o.density == nxt.density for o in history):
                    inv.rundir.log_ops("density_not_tested", density=nxt.density, cause=cause)
                inv.tracer.warn(f"density {nxt.density}: no clean trial ({cause}); the run goes no further")
                break
            ev = doc.get("evaluation") or {}
            history.append(Outcome(nxt.trial_kind, nxt.density, bool(ev.get("passed")), doc["trial_id"]))
            inv.rundir.update_run_json(plan=planner.summary(history))
            inv.tracer.info(f"{nxt.trial_kind} trial {doc['trial_id']} {'passed' if ev.get('passed') else 'missed'}",
                            reasons="; ".join(ev.get("reasons") or []))
        if a.illustration and not illustrated:
            run_clean(1, "illustration", "illustration", shots=True)
    except KeyboardInterrupt:
        interrupted = True
        inv.tracer.warn("interrupted")
        rc = 130
    finally:
        plan = planner.summary(history, interrupted=interrupted, not_clean=not_clean)
        inv.rundir.update_run_json(plan=plan)
        if not interrupted:
            inv.tracer.info("plan done", stop_reason=plan["stop_reason"], last_pass=plan["boundary"]["last_pass"],
                            first_miss=plan["boundary"]["first_miss"])
        inv.close()
    if rc == 0:
        if rs is not None:
            # a missed density is a result; a run that couldn't finish cleanly exits 1 (ops.jsonl says why)
            rc = 1 if not_clean else 0
        elif ladder_mode:
            # a missed density is the experiment's result; only a trial that could not be measured fails the run
            rc = 1 if any(d.get("error") for d in docs) else 0
        else:
            rc = 1 if any(d.get("status") != "ok" for d in docs) else 0
    return rc


def _run_harness(a, rs: RunSpec) -> dict:
    """run.json ``harness``, beside the spec: how this run was carried out (HARNESS is added by the spec)."""
    from .bundle import _git_commit
    # the wait after ready as carried out: the spec's, or 0 when the spec leaves it out (HARNESS held it before
    # it was a field, so every run.json still says what the wait was)
    return {**rs.timeouts(), "release_after_ready_s": a.release_after_ready_s,
            "backend": a.backend, "fixture_check_url": a.fixture_check_url,
            "fixture_base_url": a.fixture_base_url or DEFAULT_FIXTURE_BASE_URL.get(a.backend),
            "support_health_url": a.support_health_url, "otlp_endpoint": None if a.no_lgtm else a.otlp_endpoint,
            "argv": list(getattr(a, "_argv", None) or []),
            "git_commit": os.environ.get("FLEETKIT_GIT_COMMIT") or _git_commit()}


def cmd_report(a) -> int:
    from .report import run_report
    try:
        rep, md = run_report(a.run, out_dir=a.out, price_per_hour=a.price_per_hour, instance=a.instance,
                             step_target_ms=a.step_target_ms, step_p95_ms=a.step_p95_ms,
                             task_target_ms=a.task_target_ms, s3_get_price_per_1000=a.s3_get_price_per_1000,
                             fixture_manifest=a.fixture_manifest, bytes_tolerance=a.bytes_tolerance)
    except LegacyRunDirError as exc:
        return _refuse_legacy(exc)
    if not a.quiet:
        print(md)
    return 0


def cmd_smoke(a) -> int:
    from .smoke import SmokeOptions, SmokeSuite
    _install_signals()
    try:
        inv = Invocation(a)
    except LegacyRunDirError as exc:
        return _refuse_legacy(exc)
    except HostError:
        return 3
    opts = SmokeOptions(backend=a.backend, run_id=inv.run_id, timeouts=_timeouts(a),
                        densities=[int(x) for x in a.densities.split(",") if x.strip()], vcpus=a.vcpus,
                        mem_mib=a.mem_mib, fixture_check_url=a.fixture_check_url,
                        fixture_base_url=a.fixture_base_url, products_source=a.products, host_id=inv.host_id,
                        idle_case_s=a.idle_case_s, lifetime_case_s=a.lifetime_case_s,
                        never_ready_timeout_s=a.never_ready_timeout_s, crash_bound_s=a.crash_bound_s,
                        case_grace_s=a.case_grace_s, lgtm_dir=a.lgtm_dir, hostd_dir=a.hostd_dir, budget_s=a.budget_s)
    rc = 1
    try:
        suite = SmokeSuite(opts, inv.client, inv.rundir, inv.writers, inv.tracer)
        if a.until_green:
            res = suite.run_until_green(a.until_green, a.max_rounds)
            rc = 0 if res and res["state"]["consecutive_green"] >= a.until_green else 1
        else:
            res = suite.run_once()
            rc = 0 if res["green"] else 1
        if not a.quiet:
            print(inv.rundir.smoke_report_md.read_text(encoding="utf-8"))
    except KeyboardInterrupt:
        inv.tracer.warn("interrupted")
        rc = 130
    finally:
        inv.close()
    return rc


def cmd_bundle(a) -> int:
    from .bundle import run_bundle

    class _Log:
        def warn(self, m): print("warn: " + m, file=sys.stderr)
        def error(self, m): print("error: " + m, file=sys.stderr)

    if RunDir(Path(a.run)).legacy:
        return _refuse_legacy(LegacyRunDirError(
            f"{a.run} was written before the glossary; it is evidence already and is never rebundled"))
    res = run_bundle(a.run, strict=a.strict, lgtm_dir=a.lgtm_dir, snapshot=a.snapshot_lgtm,
                     compose_file=a.compose_file, hostd_dir=a.hostd_dir, hostd_log=a.hostd_log,
                     console_logs_dir=a.console_logs_dir, cloud_init_log=a.cloud_init_log,
                     hostcheck_output=a.hostcheck_output, aws=a.aws, lock_env=a.lock_env,
                     guest_manifest=a.guest_manifest, ami_lock=a.ami_lock, instance_type=a.instance_type,
                     vcpu_quota=a.vcpu_quota, host_provisioning_s=a.host_provisioning_s, git_commit=a.git_commit,
                     ami_id=a.ami_id, log=_Log())
    inv = res["inventory"]
    if not a.quiet:
        print(f"bundle {res['run_id']}: {len(inv['files'])} files, {inv['total_bytes']} bytes")
        for m in inv["mandatory_missing"]:
            print(f"  MISSING (mandatory): {m}")
        for m in inv["optional_absent"]:
            print(f"  absent (optional): {m}")
        for n in res["notes"]:
            print(f"  {n}")
    if a.strict and inv["mandatory_missing"]:
        return 1
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = build_parser().parse_args(argv)
    a._argv = argv
    return {"trial": cmd_trial, "report": cmd_report, "smoke": cmd_smoke, "bundle": cmd_bundle}[a.command](a)
