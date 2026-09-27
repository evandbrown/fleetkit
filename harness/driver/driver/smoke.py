"""``driver smoke``: the enumerated assertions of design section 7, in order, with a pass/fail per
assertion in smoke-report.md and a consecutive-green counter persisted in smoke-state.json.

  (1) one trial at each of densities 2 and 4: every microVM ready, every task ok, zero startup failures
  (2) each of the five faults yields the tabulated result within task_timeout + proxy margin + client margin
  (3) a microVM with idle_timeout_s=5 is destroyed with idle_expired; one with max_lifetime_s=8 with lifetime_expired
  (4) verify-clean passes after each of the above
  (5) the trial trace has spans from driver, hostd and guest-daemon, and log records with that trace id;
      and, per the section 7 observability row, a microvm.state record for every microVM of the
      assertion-1 trials under each trial's trace id

One pass through the five assertions is a round. Any failed assertion resets the counter. "Reliably"
means three consecutive green rounds within a three-hour local budget. Density trials are numbered
within their density across rounds (``d2-t1``, then ``d2-t2``); fault trials and microVM cases are
labelled (``fault-hang_step``, ``case-idle``, then ``-2`` in the next round).
"""
from __future__ import annotations

import copy
import dataclasses
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import schemas
from .bundle import copy_otlp, default_sibling
from .config import READY_GRACE_S, READY_POLL_INTERVAL_S, Timeouts, TrialConfig
from .evidence import logs_for_trace, microvm_state_ids, services_for_trace
from .hostclient import HostClient, HostError
from .outputs import RunDir, Writers, write_json_atomic
from .telemetry import Tracer
from .trial import TrialRunner

REQUIRED_SERVICES = ("driver", "hostd", "guest-daemon")
LOCAL_BUDGET_S = 3 * 3600


@dataclass
class SmokeOptions:
    backend: str
    run_id: str
    timeouts: Timeouts = field(default_factory=Timeouts)
    densities: list = field(default_factory=lambda: [2, 4])
    vcpus: int = 2
    mem_mib: int = 2048
    fixture_check_url: str = "http://127.0.0.1:8081"
    fixture_base_url: str | None = None
    products_source: str | None = None
    host_id: str = ""
    idle_case_s: float = 5.0
    lifetime_case_s: float = 8.0
    never_ready_timeout_s: float = 10.0  # ready_timeout_s used for the never_ready fault case
    crash_bound_s: float = 10.0  # "within seconds" for crash_on_start
    case_grace_s: float = 15.0  # slack for the reaper cases
    lgtm_dir: str | None = None
    hostd_dir: str | None = None
    budget_s: float = LOCAL_BUDGET_S


@dataclass
class Assertion:
    index: str
    name: str
    passed: bool
    detail: str
    duration_s: float
    trial_id: str = ""


class SmokeSuite:
    def __init__(self, opts: SmokeOptions, client: HostClient, rundir: RunDir, writers: Writers, tracer: Tracer):
        self.o = opts
        self.client = client
        self.rundir = rundir
        self.w = writers
        self.tr = tracer
        self.assertions: list[Assertion] = []
        self.trace_id: str | None = None
        self.density_trials: list[dict] = []  # assertion-1 trials: {trial_id, trace_id, microvm_ids}
        self.clean_checks: list[tuple[str, bool, str]] = []
        self.o.lgtm_dir = default_sibling(rundir, self.o.lgtm_dir, "lgtm")
        self.o.hostd_dir = default_sibling(rundir, self.o.hostd_dir, "hostd")

    # ---- one round --------------------------------------------------------------------
    def run_once(self) -> dict:
        t0 = time.monotonic()
        self.assertions = []
        self.clean_checks = []
        self.trace_id = None
        self.density_trials = []
        self.tr.info("smoke round starting", backend=self.o.backend, densities=",".join(map(str, self.o.densities)))
        self._assert_densities()
        self._assert_faults()
        self._assert_reapers()
        self._assert_clean()
        self._assert_traces()
        green = all(a.passed for a in self.assertions)
        duration = time.monotonic() - t0
        state = self._update_state(green, duration)
        self._write_report(state, duration)
        self.tr.info("smoke round " + ("GREEN" if green else "RED"),
                     consecutive_green=state["consecutive_green"], duration_s=round(duration, 1))
        return {"green": green, "assertions": [dataclasses.asdict(a) for a in self.assertions],
                "duration_s": duration, "state": state}

    def run_until_green(self, target: int, max_rounds: int) -> dict:
        last = None
        for i in range(max_rounds):
            last = self.run_once()
            if last["state"]["consecutive_green"] >= target:
                break
            if last["state"]["cumulative_s"] > self.o.budget_s:
                self.tr.warn("smoke budget exceeded; stopping", cumulative_s=round(last["state"]["cumulative_s"]))
                break
        return last

    # ---- helpers ----------------------------------------------------------------------
    def _trial(self, density: int, label: str | None = None, fault: str | None = None,
               timeouts: Timeouts | None = None) -> dict:
        """A smoke trial at ``density``: numbered (``d<N>-t<k>``) unless it carries a label."""
        cfg = TrialConfig(run_id=self.o.run_id, backend=self.o.backend, density=density,
                          timeouts=timeouts or copy.deepcopy(self.o.timeouts), vcpus=self.o.vcpus,
                          mem_mib=self.o.mem_mib, fault=fault, fixture_check_url=self.o.fixture_check_url,
                          fixture_base_url=self.o.fixture_base_url, products_source=self.o.products_source,
                          host_id=self.o.host_id, trial_label=label, trial_kind="fault" if fault else "smoke")
        return TrialRunner(cfg, self.client, self.rundir, self.w, self.tr).run()

    def _record(self, index: str, name: str, passed: bool, detail: str, t0: float, trial_id: str = "") -> None:
        a = Assertion(index, name, passed, detail, time.monotonic() - t0, trial_id)
        self.assertions.append(a)
        self.tr.log("INFO" if passed else "ERROR", f"assertion {index} {name}: {'PASS' if passed else 'FAIL'} - {detail}",
                    trial_id=trial_id)

    def _note_clean(self, label: str, trial: dict | None, direct: bool = False) -> None:
        if direct:
            try:
                vc = self.client.verify_clean()
            except HostError as exc:
                vc = {"clean": False, "leftovers": [], "error": str(exc)}
        else:
            vc = (trial or {}).get("verify_clean") or {"clean": False, "leftovers": ["no verify-clean result"]}
        self.clean_checks.append((label, bool(vc.get("clean")), json.dumps(vc.get("leftovers") or vc.get("error") or [])))

    # ---- (1) densities ----------------------------------------------------------------
    def _assert_densities(self) -> None:
        for i, n in enumerate(self.o.densities):
            t0 = time.monotonic()
            try:
                t = self._trial(n)
            except HostError as exc:
                self._record(f"1{chr(97 + i)}", f"trial at density {n}", False, f"trial aborted: {exc}", t0)
                continue
            if self.trace_id is None:
                self.trace_id = t.get("trace_id")
            self.density_trials.append({"trial_id": t["trial_id"], "trace_id": t.get("trace_id") or "",
                                        "microvm_ids": [s["microvm_id"] for s in t.get("microvms") or []]})
            c = t["counts"]
            ok = (c["microvms_ready"] == n and c["tasks_ok"] == n and c["microvms_failed_startup"] == 0
                  and c["tasks_dispatched"] == n)
            self._record(f"1{chr(97 + i)}", f"trial at density {n}", ok,
                         f"microVMs ready {c['microvms_ready']}/{n}, tasks ok {c['tasks_ok']}/{n}, "
                         f"startup failures {c['microvms_failed_startup']}, status {t['status']}", t0, t["trial_id"])
            self._note_clean(t["trial_id"], t)

    # ---- (2) faults -------------------------------------------------------------------
    def _assert_faults(self) -> None:
        to = self.o.timeouts
        faults = ["crash_on_start", "never_ready", "hang_task", "hang_step", f"slow_step:{2 * to.step_timeout_ms}"]
        bound_ms = to.task_timeout_ms + to.proxy_margin_ms + to.client_margin_ms
        for i, fault in enumerate(faults):
            name = schemas.fault_name(fault)
            exp = schemas.FAULT_EXPECTATIONS[name]
            t0 = time.monotonic()
            timeouts = copy.deepcopy(to)
            if name == "never_ready":
                timeouts.ready_timeout_s = min(to.ready_timeout_s, self.o.never_ready_timeout_s)
            try:
                t = self._trial(1, f"fault-{name}", fault=fault, timeouts=timeouts)
            except HostError as exc:
                self._record(f"2{chr(97 + i)}", f"fault {fault}", False, f"trial aborted: {exc}", t0)
                continue
            vm = (t.get("microvms") or [{}])[0]
            ts = t["timestamps"]
            problems = []
            outcome = vm.get("outcome")
            if outcome != exp["microvm_outcome"]:
                problems.append(f"microVM outcome {outcome!r} != {exp['microvm_outcome']!r}")
            task = vm.get("task")
            if exp["task"] is None:
                if task is not None:
                    problems.append("a task was dispatched to a microVM that should have failed startup")
                wait_s = (ts["all_ready"] - ts["create_start"]) if ts.get("all_ready") and ts.get("create_start") else None
                if name == "crash_on_start":
                    if wait_s is None or wait_s > self.o.crash_bound_s:
                        problems.append(f"startup failure took {wait_s} s, bound {self.o.crash_bound_s} s")
                elif name == "never_ready":
                    rt = timeouts.ready_timeout_s
                    if wait_s is None or wait_s < rt - 1.0 or wait_s > rt + max(5.0, 0.25 * rt):
                        problems.append(f"startup timeout after {wait_s} s, expected at ready_timeout_s={rt}")
                detail = f"outcome {outcome}, startup phase {wait_s if wait_s is None else round(wait_s, 2)} s"
            else:
                if task is None:
                    problems.append("no task was dispatched")
                    detail = f"outcome {outcome}, no task"
                else:
                    if task["failure_category"] != exp["task"]:
                        problems.append(f"task {task['failure_category']!r} != {exp['task']!r}")
                    if exp["task"] == "step_timeout" and not task.get("failed_step"):
                        problems.append("failed_step is empty")
                    if task["wall_ms"] > bound_ms:
                        problems.append(f"wall_ms {task['wall_ms']:.0f} > bound {bound_ms}")
                    if exp["microvm_state_after"] and vm.get("state_after_task") != exp["microvm_state_after"]:
                        problems.append(f"microVM state after task {vm.get('state_after_task')!r} != {exp['microvm_state_after']!r}")
                    detail = (f"task {task['failure_category']} failed_step={task.get('failed_step') or '-'} "
                              f"wall {task['wall_ms']:.0f} ms (bound {bound_ms}), microVM outcome {outcome}, "
                              f"state after task {vm.get('state_after_task')}")
            if problems:
                detail += "; " + "; ".join(problems)
            self._record(f"2{chr(97 + i)}", f"fault {fault}", not problems, detail, t0, t["trial_id"])
            self._note_clean(t["trial_id"], t)

    # ---- (3) reapers ------------------------------------------------------------------
    def _assert_reapers(self) -> None:
        cases = [("3a", "idle", self.o.idle_case_s, self.o.timeouts.max_lifetime_s, "idle_expired", self.o.idle_case_s),
                 ("3b", "lifetime", self.o.timeouts.idle_timeout_s, self.o.lifetime_case_s, "lifetime_expired",
                  self.o.lifetime_case_s)]
        for index, name, idle_s, life_s, expected, budget in cases:
            if name == "lifetime" and idle_s <= life_s:
                idle_s = life_s + 60  # idle must not fire first
            t0 = time.monotonic()
            try:
                res = self._microvm_case(name, idle_s, life_s, expected, budget)
            except (HostError, KeyboardInterrupt) as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                self._record(index, f"{name} reaper", False, f"case aborted: {exc}", t0)
                continue
            self._record(index, f"{name} reaper", res["passed"], res["detail"], t0, res["trial_id"])
            self._note_clean(res["trial_id"], None, direct=True)

    def _microvm_case(self, name: str, idle_s: float, life_s: float, expected: str, budget_s: float) -> dict:
        to = self.o.timeouts
        seq = self.rundir.next_sequence()
        trial_id = self.rundir.unique_label(f"case-{name}")
        d = self.rundir.trial_dir(trial_id)
        create_request = {"backend": self.o.backend, "count": 1, "vcpus": self.o.vcpus, "mem_mib": self.o.mem_mib,
                          "ready_timeout_s": to.ready_timeout_s, "max_lifetime_s": life_s, "idle_timeout_s": idle_s,
                          "launch_interval_ms": 0, "fault": None}
        with self.tr.start_span(f"smoke.case.{name}", attributes={"fleetkit.run_id": self.o.run_id,
                                                                    "fleetkit.trial_id": trial_id}) as sp:
            headers = {"traceparent": sp.traceparent, "X-Fleetkit-Trial-Id": trial_id}
            created_at = time.time()
            created = self.client.create_microvms(create_request, headers)
            mid = str(created[0].get("id") or created[0].get("microvm_id"))
            slot = created[0].get("slot", 0)
            self.tr.info(f"case {name}: microVM {mid} created", trial_id=trial_id, idle_timeout_s=idle_s,
                         max_lifetime_s=life_s)
            info: dict = {}
            became_ready = False
            deadline = time.monotonic() + to.ready_timeout_s + life_s + budget_s + self.o.case_grace_s + READY_GRACE_S
            while time.monotonic() < deadline:
                try:
                    info = self.client.get_microvm(mid)
                except HostError as exc:
                    if exc.status == 404:
                        break
                    raise
                st = info.get("state")
                if st in ("ready", "busy"):
                    became_ready = True
                if st in schemas.TERMINAL_STATES and info.get("destroyed_ts") is not None:
                    break
                time.sleep(READY_POLL_INTERVAL_S)
            ended_at = time.time()
            self.client.delete_microvm(mid, headers)  # idempotent; a no-op when the reaper got there first
            try:
                info = self.client.get_microvm(mid) or info
            except HostError:
                pass
            outcome = info.get("outcome")
            elapsed = ended_at - created_at
            problems = []
            if not became_ready:
                problems.append("microVM never became ready")
            if outcome != expected:
                problems.append(f"outcome {outcome!r} != {expected!r}")
            if elapsed > to.ready_timeout_s + budget_s + self.o.case_grace_s:
                problems.append(f"took {elapsed:.1f} s, bound {to.ready_timeout_s + budget_s + self.o.case_grace_s:.1f} s")
            passed = not problems
            sp.set(**{"fleetkit.microvm_id": mid, "fleetkit.outcome": outcome or "", "fleetkit.passed": passed})
            if not passed:
                sp.error("; ".join(problems))
        self.w.microvms.append({
            "run_id": self.o.run_id, "trial_id": trial_id, "microvm_id": mid, "slot": slot,
            "backend": self.o.backend, "vcpus": self.o.vcpus, "mem_mib": self.o.mem_mib,
            "created_ts": info.get("created_ts"), "process_started_ts": info.get("process_started_ts"),
            "ready_ts": info.get("ready_ts"), "destroyed_ts": info.get("destroyed_ts"),
            "startup_ms": info.get("startup_ms"), "cleanup_ms": info.get("cleanup_ms"),
            "outcome": outcome, "error": info.get("error") or "",
            "kernel_start_ts": info.get("kernel_start_ts"), "guestd_start_ts": info.get("guestd_start_ts"),
            "chromium_launch_ts": info.get("chromium_launch_ts"), "chromium_ready_ts": info.get("chromium_ready_ts"),
        })
        detail = f"outcome {outcome} after {elapsed:.1f} s (idle {idle_s} s, lifetime {life_s} s)"
        if problems:
            detail += "; " + "; ".join(problems)
        write_json_atomic(d / "case.json", {"trial_id": trial_id, "sequence": seq, "case": name, "microvm_id": mid,
                                            "create_request": create_request, "expected_outcome": expected,
                                            "outcome": outcome, "elapsed_s": elapsed, "passed": passed,
                                            "problems": problems, "microvm": info})
        return {"passed": passed, "detail": detail, "trial_id": trial_id}

    # ---- (4) clean --------------------------------------------------------------------
    def _assert_clean(self) -> None:
        t0 = time.monotonic()
        bad = [(label, lo) for label, ok, lo in self.clean_checks if not ok]
        detail = f"{len(self.clean_checks) - len(bad)}/{len(self.clean_checks)} checks clean"
        if bad:
            detail += "; leftovers: " + "; ".join(f"{l}: {lo}" for l, lo in bad)
        self._record("4", "verify-clean after every case", not bad and bool(self.clean_checks), detail, t0)

    # ---- (5) traces -------------------------------------------------------------------
    def _assert_traces(self) -> None:
        t0 = time.monotonic()
        if not self.trace_id:
            self._record("5", "trace completeness", False, "no trial trace id (assertion 1 did not run)", t0)
            return
        if self.o.lgtm_dir:
            try:
                copy_otlp(Path(self.o.lgtm_dir), self.rundir)
            except Exception as exc:
                self.tr.warn(f"otlp copy from {self.o.lgtm_dir} failed: {exc}")
        span_files, log_files = self.telemetry_files()
        services = services_for_trace(span_files, self.trace_id)
        logs = logs_for_trace(log_files, self.trace_id)
        missing = [s for s in REQUIRED_SERVICES if not services.get(s)]
        # section 7, observability row: microvm.state records for every microVM under the trial's trace id
        state_missing: dict[str, list[str]] = {}
        state_seen = 0
        for dt in self.density_trials:
            have = microvm_state_ids(log_files, dt["trace_id"]) if dt["trace_id"] else set()
            state_seen += len(have)
            lacking = [mid for mid in dt["microvm_ids"] if mid not in have]
            if lacking or not dt["microvm_ids"]:
                state_missing[dt["trial_id"]] = lacking
        ok = not missing and sum(logs.values()) > 0 and not state_missing
        detail = (f"trace {self.trace_id}: spans by service {json.dumps(services)}, log records by service "
                  f"{json.dumps(logs)}, microvm.state records for {state_seen}/"
                  f"{sum(len(dt['microvm_ids']) for dt in self.density_trials)} microVMs; "
                  f"sources {[p.parent.name + '/' + p.name for p in span_files]}")
        if missing:
            detail += f"; missing spans from {missing}"
        if not sum(logs.values()):
            detail += "; no log records with the trace id"
        if state_missing:
            detail += "; no microvm.state record for " + ", ".join(
                f"{tid}: {', '.join(mids) if mids else 'no microVMs'}" for tid, mids in state_missing.items())
        self._record("5", "trace completeness", ok, detail, t0)

    def telemetry_files(self) -> tuple[list[Path], list[Path]]:
        r = self.rundir
        spans = [r.otlp_dir / "traces.jsonl", r.spans_jsonl, r.root / "hostd-spans.jsonl"]
        logs = [r.otlp_dir / "logs.jsonl", r.logs_jsonl, r.root / "hostd-logs.jsonl"]
        if self.o.hostd_dir:
            h = Path(self.o.hostd_dir)
            spans += [h / "spans.jsonl", h / "hostd-spans.jsonl"]
            logs += [h / "logs.jsonl", h / "hostd-logs.jsonl"]
        return [p for p in spans if p.exists()], [p for p in logs if p.exists()]

    # ---- state and report -------------------------------------------------------------
    def _update_state(self, green: bool, duration: float) -> dict:
        st = self.rundir.read_smoke_state() or {}
        st.setdefault("consecutive_green", 0)
        st.setdefault("rounds", [])
        st.setdefault("cumulative_s", 0.0)
        st["consecutive_green"] = st["consecutive_green"] + 1 if green else 0
        st["cumulative_s"] += duration
        st["budget_s"] = self.o.budget_s
        st["rounds"].append({"ts": time.time(), "green": green, "duration_s": round(duration, 1),
                           "backend": self.o.backend,
                           "failed": [f"{a.index} {a.name}" for a in self.assertions if not a.passed]})
        write_json_atomic(self.rundir.smoke_state_json, st)
        return st

    def _write_report(self, state: dict, duration: float) -> None:
        green = all(a.passed for a in self.assertions)
        L = [f"# Smoke report: run {self.rundir.run_id}", "",
             f"Backend: {self.o.backend}. This round: **{'GREEN' if green else 'RED'}** in {duration:.0f} s. "
             f"Consecutive green rounds: **{state['consecutive_green']}** (three needed). "
             f"Cumulative smoke time {state['cumulative_s'] / 60:.1f} min of the {self.o.budget_s / 3600:.0f} h local budget"
             + (" (budget exceeded; the AWS leg starts with the green subset)" if state["cumulative_s"] > self.o.budget_s else "") + ".",
             "", "| # | assertion | result | trial | detail | took |", "|---|---|---|---|---|---:|"]
        for a in self.assertions:
            L.append(f"| {a.index} | {a.name} | {'PASS' if a.passed else 'FAIL'} | {a.trial_id} | "
                     f"{a.detail.replace('|', '/')} | {a.duration_s:.1f} s |")
        L += ["", "## Timeouts used", "", "```", json.dumps(self.o.timeouts.as_dict(), indent=2), "```", "",
              f"never_ready case ready_timeout_s: {min(self.o.timeouts.ready_timeout_s, self.o.never_ready_timeout_s)}; "
              f"idle case idle_timeout_s: {self.o.idle_case_s}; lifetime case max_lifetime_s: {self.o.lifetime_case_s}",
              "", "## Round history", "", "| when | result | took | failed |", "|---|---|---:|---|"]
        for r in state["rounds"][-20:]:
            L.append(f"| {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(r['ts']))} | {'GREEN' if r['green'] else 'RED'} | "
                     f"{r['duration_s']} s | {', '.join(r['failed']) or '-'} |")
        L.append("")
        self.rundir.smoke_report_md.write_text("\n".join(L), encoding="utf-8")
