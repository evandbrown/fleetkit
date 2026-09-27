"""Command line: ``python3 -m driver {trial,report,smoke,bundle}`` (design section 4, "Driver CLI")."""
from __future__ import annotations

import argparse
import collections
import copy
import json
import os
import signal
import sys
import time
from pathlib import Path

from . import __version__
from .config import (DEFAULT_FIXTURE_BASE_URL, DEFAULT_FIXTURE_CHECK_URL, DEFAULT_HOST_URL, DEFAULT_OTLP_ENDPOINT,
                     DEFAULT_SAMPLE_INTERVAL_MS, Timeouts, TrialConfig)
from .criteria import make_criteria
from .hostclient import HostClient, HostError
from .ladder import LadderPlanner, Outcome
from .metrics import HostMetricsSampler
from .outputs import RunDir
from .telemetry import Tracer
from .trial import TrialRunner


def _default_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()


def _add_common(p: argparse.ArgumentParser, need_backend: bool = True) -> None:
    p.add_argument("--backend", choices=("docker", "firecracker"), required=need_backend)
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
                   help="60 local; 90 on AWS for the first n=1 trial, then 3x the observed startup_ms")
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

    t = sub.add_parser("trial", help="run trials at several N and write the run directory")
    _add_common(t)
    t.add_argument("--n", default="2,4", help="comma-separated concurrency levels, e.g. 2,4")
    t.add_argument("--repeats", type=int, default=1)
    t.add_argument("--fault", default=None, help="fault name for every session (crash_on_start, never_ready, "
                                                  "hang_task, hang_step, slow_step:<ms>)")
    t.add_argument("--step-p50-target-ms", type=float, default=None, help="pass criterion: every step's p50 (unset: not checked)")
    t.add_argument("--step-p95-target-ms", type=float, default=None, help="pass criterion: every step's p95 (unset: not checked)")
    t.add_argument("--task-p95-target-ms", type=float, default=None, help="pass criterion: task_ms p95 (unset: not checked)")
    t.add_argument("--stop-at-first-miss", action="store_true",
                   help="after all repeats of a level, run no higher level if it did not pass; a miss is then a "
                        "result, so the exit code is non-zero only for trial errors (fixture, host daemon, verify-clean)")
    t.add_argument("--confirm-repeats", type=int, default=0,
                   help="after the ladder, K more trials at the last passing and the first failing level, walking "
                        "down while a confirmation of the last pass fails")
    t.add_argument("--warmup", type=int, default=0, help="K trials at n=1 before the ladder (kind warmup, not evaluated)")
    t.add_argument("--settle-s", type=float, default=0.0,
                   help="wait S seconds before each trial, recording host cpu_util as trial.json pre_trial")
    t.add_argument("--illustration", action="store_true",
                   help="finish with one n=1 trial with untimed per-step screenshots (kind illustration)")
    t.add_argument("--metrics-hz", type=float, default=1.0,
                   help="host metrics sampling rate; start the host daemon with a matching --metrics-period")
    t.add_argument("--sample-interval-ms", type=int, default=DEFAULT_SAMPLE_INTERVAL_MS,
                   help="guest process sampling interval sent with every task (0 disables)")
    t.add_argument("--no-fixture-probe", action="store_true",
                   help="do not time a GET of <fixture-check-url>/ at 1 Hz (fixture_rtt_ms in host_metrics.csv)")

    r = sub.add_parser("report", help="report from a run directory (works from the bundle alone)")
    r.add_argument("--run", required=True)
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
    s.add_argument("--levels", default="2,4", help="concurrency levels for assertion 1")
    s.add_argument("--idle-case-s", type=float, default=5.0)
    s.add_argument("--lifetime-case-s", type=float, default=8.0)
    s.add_argument("--never-ready-timeout-s", type=float, default=10.0)
    s.add_argument("--crash-bound-s", type=float, default=10.0)
    s.add_argument("--case-grace-s", type=float, default=15.0)
    s.add_argument("--lgtm-dir", default=None, help="results/lgtm (otlp/ and data/ mounts)")
    s.add_argument("--hostd-dir", default=None, help="where the host daemon writes spans.jsonl/logs.jsonl/hostd.log")
    s.add_argument("--until-green", type=int, default=None, help="repeat until this many consecutive green runs")
    s.add_argument("--max-runs", type=int, default=10)
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


class Session:
    """Per-invocation resources: run directory, tracer, host client, writers, metrics sampler."""

    def __init__(self, a):
        self.rundir = RunDir(Path(a.out), a.run_id)
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
                             repeats_skipped=self.sampler.repeats, fixture_probes=self.sampler.probes,
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


def _trial_inputs(a, sess: Session, criteria: dict, levels: list[int]) -> dict:
    """run.json ``inputs``: everything needed to know what this invocation was asked to do, and on what."""
    from .bundle import _git_commit
    return {
        "argv": list(getattr(a, "_argv", None) or []),
        "options": {k: v for k, v in vars(a).items() if not k.startswith("_")},
        "criteria": criteria, "ladder": levels, "repeats": a.repeats, "confirm_repeats": a.confirm_repeats,
        "stop_at_first_miss": a.stop_at_first_miss, "warmup": a.warmup, "settle_s": a.settle_s,
        "illustration": a.illustration, "metrics_hz": a.metrics_hz, "sample_interval_ms": a.sample_interval_ms,
        "fixture_probe": not a.no_fixture_probe and not a.no_host_metrics,
        "fixture_check_url": a.fixture_check_url,
        "fixture_base_url": a.fixture_base_url or DEFAULT_FIXTURE_BASE_URL[a.backend],
        "otlp_endpoint": None if a.no_lgtm else a.otlp_endpoint,
        "host_info": sess.client.host_info(),
        "guest_info": None,  # filled from the first ready session's record
        "git_commit": os.environ.get("FLEETKIT_GIT_COMMIT") or _git_commit(),
    }


def _first_guest_info(runner: TrialRunner) -> dict | None:
    for s in sorted(runner.sessions, key=lambda x: x.slot):
        if s.ready and isinstance(s.info.get("guest_info"), dict):
            return s.info["guest_info"]
    return None


def cmd_trial(a) -> int:
    levels = [int(x) for x in a.n.split(",") if x.strip()]
    if not levels or any(n < 1 for n in levels):
        print("--n must list positive integers", file=sys.stderr)
        return 2
    ladder_mode = a.stop_at_first_miss or a.confirm_repeats > 0
    if ladder_mode and any(hi <= lo for lo, hi in zip(levels, levels[1:])):
        print("--stop-at-first-miss and --confirm-repeats need --n in strictly ascending order", file=sys.stderr)
        return 2
    if a.metrics_hz <= 0 or a.confirm_repeats < 0 or a.warmup < 0 or a.settle_s < 0 or a.sample_interval_ms < 0:
        print("--metrics-hz must be positive; --confirm-repeats, --warmup, --settle-s and --sample-interval-ms "
              "must not be negative", file=sys.stderr)
        return 2
    _install_signals()
    try:
        sess = Session(a)
    except HostError:
        return 3
    criteria = make_criteria(a.step_p50_target_ms, a.step_p95_target_ms, a.task_p95_target_ms)
    planner = LadderPlanner(levels, a.repeats, a.confirm_repeats, a.stop_at_first_miss)
    history: list[Outcome] = []
    docs: list[dict] = []
    rc = 0
    interrupted = False
    try:
        inputs = _trial_inputs(a, sess, criteria, levels)
        sess.rundir.update_run_json(criteria=criteria, inputs=inputs, plan=planner.summary(history))
        base = _timeouts(a)

        def run_one(level_n: int, repeat: int, kind: str, label: str | None = None, shots: bool = False) -> dict:
            cfg = TrialConfig(run_id=sess.run_id, backend=a.backend, level_n=level_n, repeat=repeat,
                              timeouts=copy.deepcopy(base), vcpus=a.vcpus, mem_mib=a.mem_mib, fault=a.fault,
                              fixture_check_url=a.fixture_check_url, fixture_base_url=a.fixture_base_url,
                              products_source=a.products, host_id=sess.host_id, trial_label=label,
                              kind="fault" if a.fault else kind, criteria=criteria, settle_s=a.settle_s,
                              sample_interval_ms=a.sample_interval_ms, screenshot_each_step=shots)
            runner = TrialRunner(cfg, sess.client, sess.rundir, sess.writers, sess.tracer, metrics=sess.sampler)
            try:
                doc = runner.run()
            finally:
                if inputs["guest_info"] is None:
                    gi = _first_guest_info(runner)
                    if gi is not None:
                        inputs["guest_info"] = gi
                        sess.rundir.update_run_json(inputs=inputs)
            docs.append(doc)
            return doc

        for k in range(1, a.warmup + 1):
            run_one(1, k, "warmup", f"warmup-n1-r{k}")
        at_level: collections.Counter = collections.Counter()  # repeat counts every trial at a level
        while True:
            nxt = planner.next(history)
            if nxt is None:
                break
            at_level[nxt.level] += 1
            doc = run_one(nxt.level, at_level[nxt.level], nxt.kind)
            ev = doc.get("evaluation") or {}
            history.append(Outcome(nxt.kind, nxt.level, bool(ev.get("passed")), doc["trial_id"]))
            sess.rundir.update_run_json(plan=planner.summary(history))
            sess.tracer.info(f"{nxt.kind} trial {doc['trial_id']} {'passed' if ev.get('passed') else 'missed'}",
                             reasons="; ".join(ev.get("reasons") or []))
        if a.illustration:
            run_one(1, 1, "illustration", "illustration-n1", shots=True)
    except KeyboardInterrupt:
        interrupted = True
        sess.tracer.warn("interrupted")
        rc = 130
    finally:
        plan = planner.summary(history, interrupted=interrupted)
        sess.rundir.update_run_json(plan=plan)
        if not interrupted:
            sess.tracer.info("plan done", stop_reason=plan["stop_reason"], last_pass=plan["boundary"]["last_pass"],
                             first_miss=plan["boundary"]["first_miss"])
        sess.close()
    if rc == 0:
        if ladder_mode:
            # a missed level is the experiment's result; only a trial that could not be measured fails the run
            rc = 1 if any(d.get("error") for d in docs) else 0
        else:
            rc = 1 if any(d.get("status") != "ok" for d in docs) else 0
    return rc


def cmd_report(a) -> int:
    from .report import run_report
    rep, md = run_report(a.run, price_per_hour=a.price_per_hour, instance=a.instance,
                         step_target_ms=a.step_target_ms, step_p95_ms=a.step_p95_ms,
                         task_target_ms=a.task_target_ms, s3_get_price_per_1000=a.s3_get_price_per_1000,
                         fixture_manifest=a.fixture_manifest, bytes_tolerance=a.bytes_tolerance)
    if not a.quiet:
        print(md)
    return 0


def cmd_smoke(a) -> int:
    from .smoke import SmokeOptions, SmokeSuite
    _install_signals()
    try:
        sess = Session(a)
    except HostError:
        return 3
    opts = SmokeOptions(backend=a.backend, run_id=sess.run_id, timeouts=_timeouts(a),
                        levels=[int(x) for x in a.levels.split(",") if x.strip()], vcpus=a.vcpus,
                        mem_mib=a.mem_mib, fixture_check_url=a.fixture_check_url,
                        fixture_base_url=a.fixture_base_url, products_source=a.products, host_id=sess.host_id,
                        idle_case_s=a.idle_case_s, lifetime_case_s=a.lifetime_case_s,
                        never_ready_timeout_s=a.never_ready_timeout_s, crash_bound_s=a.crash_bound_s,
                        case_grace_s=a.case_grace_s, lgtm_dir=a.lgtm_dir, hostd_dir=a.hostd_dir, budget_s=a.budget_s)
    rc = 1
    try:
        suite = SmokeSuite(opts, sess.client, sess.rundir, sess.writers, sess.tracer)
        if a.until_green:
            res = suite.run_until_green(a.until_green, a.max_runs)
            rc = 0 if res and res["state"]["consecutive_green"] >= a.until_green else 1
        else:
            res = suite.run_once()
            rc = 0 if res["green"] else 1
        if not a.quiet:
            print(sess.rundir.smoke_report_md.read_text(encoding="utf-8"))
    except KeyboardInterrupt:
        sess.tracer.warn("interrupted")
        rc = 130
    finally:
        sess.close()
    return rc


def cmd_bundle(a) -> int:
    from .bundle import run_bundle

    class _Log:
        def warn(self, m): print("warn: " + m, file=sys.stderr)
        def error(self, m): print("error: " + m, file=sys.stderr)

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
