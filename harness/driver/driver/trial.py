"""The trial protocol (design sections 4, 5, 6 and 11).

One trial at density N: optional settle wait, fixture check, create N microVMs, wait until all are
ready or failed, optionally wait ``release_after_ready_s`` more with every microVM ready and idle (a
warm start), release a barrier so N tasks start together (or spaced by launch_interval_ms), collect
rows as they return, destroy every microVM, verify-clean, write trial.json with the trial's
``trial_kind`` and its ``evaluation`` against the criteria (driver/criteria.py).

The wait after ready sits between ``all_ready`` and ``barrier_release``: task times start at the
release, so no task's time includes it; the execution window (release to last task return) leaves it
out and the observed window (create to verify-clean) takes it in. trial.json records the wait asked
for (``release_after_ready_s``) and the wait carried out (``timestamps.release_wait_start`` and
``release_wait_end``, null when the trial did not wait).

A counting trial is numbered from 1 within its density, ``d<N>-t<number>`` ("trial 2 at density 8"
is ``d8-t2``); warm-up, illustration and fault trials are labelled instead. ``sequence`` records the
run-wide execution order. Every CSV row is appended as it arrives and trial.json is rewritten at
each phase, so the run directory is valid after any interruption.
"""
from __future__ import annotations

import base64
import collections
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import schemas
from .config import CONSOLE_KERNEL_ARGS, DESTROY_GRACE_S, READY_GRACE_S, READY_POLL_INTERVAL_S, TrialConfig
from .criteria import evaluate_trial
from .hostclient import HostClient, HostError
from .outputs import RunDir, Writers, write_json_atomic
from .products import FixtureCheckError, Product, assign, fixture_check, load_products
from .stats import percentile, mean, summary, to_float
from .telemetry import Span, Tracer


@dataclass
class MicrovmRec:
    microvm_id: str
    slot: int
    address: str = ""
    info: dict = field(default_factory=dict)  # last GET /microvms/{id} body
    ready: bool = False
    failed: bool = False
    driver_error: str = ""
    task: dict | None = None
    state_after_task: str | None = None
    csv_written: bool = False

    @property
    def state(self) -> str:
        return str(self.info.get("state") or "")

    @property
    def outcome(self) -> str:
        return str(self.info.get("outcome") or "")

    @property
    def running_flags(self) -> list | None:
        """The flags this microVM's browser process runs with, as its guest read them back; None if not told."""
        gi = self.info.get("guest_info")
        flags = gi.get("chromium_running_flags") if isinstance(gi, dict) else None
        return list(flags) if isinstance(flags, list) else None


class TrialRunner:
    def __init__(self, cfg: TrialConfig, client: HostClient, rundir: RunDir, writers: Writers,
                 tracer: Tracer, seq: int | None = None, metrics=None):
        self.cfg = cfg
        self.client = client
        self.rundir = rundir
        self.w = writers
        self.tr = tracer
        self.metrics = metrics  # HostMetricsSampler, for the settle wait's cpu_util; polled directly if None
        self.sequence = seq if seq is not None else rundir.next_sequence()
        if cfg.trial_label:
            self.trial_number: int | None = None
            self.trial_id = rundir.unique_label(cfg.trial_label)
        else:
            self.trial_number = rundir.next_trial_number(cfg.density)
            self.trial_id = f"d{cfg.density}-t{self.trial_number}"
        self.trial_dir = rundir.trial_dir(self.trial_id)
        self.microvms: list[MicrovmRec] = []
        self.timestamps: dict[str, float | None] = {
            "create_start": None, "all_ready": None, "release_wait_start": None, "release_wait_end": None,
            "barrier_release": None, "last_task_return": None, "verify_clean_pass": None,
        }
        self.phase = "starting"
        self.error: str | None = None
        self.verify_clean: dict | None = None
        self.fixture: dict | None = None
        self.products: list[Product] = []
        self.products_source = ""
        self.span: Span | None = None
        self._lock = threading.Lock()
        self._interrupted = False
        self.pre_trial: dict | None = None
        # this trial's rows as written, for evaluate_trial (the report reads the same rows from the CSVs)
        self.task_rows: list[dict] = []
        self.step_rows: list[dict] = []

    # ---- helpers -----------------------------------------------------------------------
    def _log(self, severity: str, msg: str, **attrs):
        self.tr.log(severity, msg, self.span, trial_id=self.trial_id, backend=self.cfg.backend,
                    density=self.cfg.density, **attrs)

    def _headers(self, span: Span | None = None) -> dict:
        s = span or self.span
        return {"traceparent": s.traceparent if s else "", "X-Fleetkit-Trial-Id": self.trial_id}

    # ---- protocol ----------------------------------------------------------------------
    def run(self) -> dict:
        cfg = self.cfg
        self.span = self.tr.start_span("trial", attributes={
            "fleetkit.run_id": cfg.run_id, "fleetkit.trial_id": self.trial_id,
            "fleetkit.backend": cfg.backend, "fleetkit.density": cfg.density,
            "fleetkit.trial_number": self.trial_number, "fleetkit.fault": cfg.fault or "",
            "fleetkit.host_id": cfg.host_id, "fleetkit.trial_kind": cfg.trial_kind})
        self._log("INFO", f"trial {self.trial_id} starting", fault=cfg.fault or "", trial_kind=cfg.trial_kind,
                  sequence=self.sequence, trace_id=self.span.trace_id)
        self._write_trial_json()
        created = False
        try:
            if cfg.settle_s and cfg.settle_s > 0:
                self.phase = "settle"
                self._settle()
                self._write_trial_json()

            self.phase = "fixture_check"
            self._fixture_check_and_products()
            self._write_trial_json()

            self.phase = "create"
            self._create_microvms()
            created = True
            self._write_trial_json()

            self.phase = "wait_ready"
            self._wait_all_ready()
            self._write_trial_json()

            ready = [s for s in self.microvms if s.ready]
            wrong = self._chromium_flags_problem(ready) or self._guest_console_problem(ready)
            if wrong:
                self.error = wrong
                self._log("ERROR", self.error)
                ready = []
            if ready and cfg.release_after_ready_s > 0:
                self.phase = "release_wait"
                self._release_wait(len(ready))
            if ready:
                self.phase = "fixture_recheck"
                try:
                    fixture_check(cfg.fixture_check_url)
                except FixtureCheckError as exc:
                    self.error = f"fixture check failed before the barrier: {exc}"
                    self._log("ERROR", self.error)
                else:
                    self.phase = "tasks"
                    self._run_tasks(ready)
            elif not wrong:
                self._log("WARN", "no microVM became ready; no tasks dispatched")
            self._write_trial_json()
        except KeyboardInterrupt:
            self._interrupted = True
            self.error = "interrupted"
            self._log("WARN", "interrupted; cleaning up microVMs")
        except FixtureCheckError as exc:
            self.error = f"fixture check failed: {exc}"
            self._log("ERROR", f"trial {self.trial_id} aborted: {self.error}")
        except HostError as exc:
            self.error = f"host daemon: {exc}"
            self._log("ERROR", f"trial {self.trial_id} aborted: {self.error}")
        finally:
            if created:
                self.phase = "cleanup"
                self._write_trial_json()
                try:
                    self._cleanup()
                except KeyboardInterrupt:
                    self._interrupted = True
                    self.error = self.error or "interrupted during cleanup"
                    self._log("WARN", "interrupted during cleanup; hostd's lifetime and idle reapers still apply")
            self.phase = "verify_clean"
            try:
                self._verify_clean()
            except HostError as exc:
                self.verify_clean = {"clean": False, "leftovers": [], "error": str(exc)}
                self._log("ERROR", f"verify-clean failed: {exc}")
            self.phase = "done"
            result = self._write_trial_json(final=True)
            if result["status"] != "ok":
                self.span.error(self.error or result["status"])
            self.span.set(**{"fleetkit.status": result["status"]})
            self.span.end()
            self._log("INFO", f"trial {self.trial_id} {result['status']}",
                      microvms_ready=result["counts"]["microvms_ready"],
                      tasks_ok=result["counts"]["tasks_ok"], tasks=result["counts"]["tasks_dispatched"])
        if self._interrupted:
            raise KeyboardInterrupt
        return result

    def _settle(self) -> None:
        """Wait settle_s before the trial and record the host cpu_util seen meanwhile (pre_trial)."""
        wait_s = float(self.cfg.settle_s)
        values: list[float] = []
        with self.tr.start_span("settle", self.span, {"fleetkit.settle_s": wait_s}) as sp:
            t0 = time.time()
            if self.metrics is not None:
                time.sleep(wait_s)
                values = self.metrics.cpu_util_between(t0, time.time())
            else:
                deadline = time.monotonic() + wait_s
                while True:
                    try:
                        m = self.client.host_metrics(timeout_s=1.0)
                        v = m.get("cpu_util") if isinstance(m, dict) else None
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            values.append(float(v))
                    except HostError:
                        pass
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    time.sleep(min(1.0, left))
            self.pre_trial = {"settle_s": wait_s, "cpu_util_mean": mean(values),
                              "cpu_util_max": max(values) if values else None, "samples": len(values)}
            sp.set(**{"fleetkit.cpu_util_mean": self.pre_trial["cpu_util_mean"]})
        self._log("INFO", "settled", **{k: v for k, v in self.pre_trial.items() if v is not None})

    def _chromium_flags_problem(self, ready: list[MicrovmRec]) -> str | None:
        """Why a ready microVM's Chromium isn't running the flags the spec says, or None. Each guest reads its
        browser process's flags back from /proc (guest_info.chromium_running_flags). A guest that can't
        (one older than the read-back) passes only when the spec adds no flags, as every run before them did."""
        want = self.cfg.chromium_flags_expected
        if want is None:
            return None
        for s in ready:
            running = s.running_flags
            if running is None:
                if self.cfg.chromium_extra_flags:
                    return (f"chromium flags: microVM {s.microvm_id} didn't report the flags its browser runs with, "
                            f"so the spec's extra flags can't be shown to have run")
                continue
            if list(running) != list(want):
                return (f"chromium flags: microVM {s.microvm_id}'s browser runs {json.dumps(running)}, "
                        f"not {json.dumps(want)}")
        return None

    def _guest_console_problem(self, ready: list[MicrovmRec]) -> str | None:
        """Why a ready microVM's guest didn't boot with the spec's console, or None. Checked only when the spec
        asks for a console other than verbose (every microVM before the field booted verbose): on a hypervisor
        the guest's own kernel command line (guest_info.kernel_cmdline, read from /proc/cmdline) must carry the
        mode's words and no other mode's (quiet is not quiet-i8042); on docker, which has no guest kernel, the
        guest must report the mode the host passed it (guest_info.console)."""
        want = self.cfg.console
        if not want:
            return None
        words = CONSOLE_KERNEL_ARGS[want]
        others = sorted({w for ws in CONSOLE_KERNEL_ARGS.values() for w in ws} - set(words))
        for s in ready:
            gi = s.info.get("guest_info") if isinstance(s.info.get("guest_info"), dict) else {}
            if self.cfg.backend == "docker":
                if gi.get("console") != want:
                    return (f"guest console: microVM {s.microvm_id}'s guest reports console "
                            f"{json.dumps(gi.get('console'))}, not {json.dumps(want)}")
                continue
            cmdline = gi.get("kernel_cmdline")
            if not isinstance(cmdline, str):
                return (f"guest console: microVM {s.microvm_id} didn't report its kernel command line, so console "
                        f"{want} can't be shown to have run")
            have = cmdline.split()
            missing = [w for w in words if w not in have]
            if missing:
                return (f"guest console: microVM {s.microvm_id}'s kernel command line lacks {' '.join(missing)} "
                        f"for console {want}: {cmdline}")
            extra = [w for w in others if w in have]
            if extra:
                return (f"guest console: microVM {s.microvm_id}'s kernel command line carries {' '.join(extra)}, "
                        f"which console {want} doesn't add: {cmdline}")
        return None

    def _release_wait(self, n_ready: int) -> None:
        """Hold every ready microVM ready and idle for release_after_ready_s before the barrier (a warm
        start), recording when the wait began and ended. hostd's idle reaper allows for it (spec.timeouts)."""
        wait_s = float(self.cfg.release_after_ready_s)
        with self.tr.start_span("release_wait", self.span, {"fleetkit.release_after_ready_s": wait_s,
                                                            "fleetkit.ready": n_ready}):
            self.timestamps["release_wait_start"] = time.time()
            t0 = time.monotonic()
            self._write_trial_json()  # an interrupted trial shows it was waiting
            time.sleep(max(0.0, wait_s - (time.monotonic() - t0)))
            self.timestamps["release_wait_end"] = time.time()
        self._log("INFO", "waited after ready", release_after_ready_s=wait_s,
                  waited_s=round(self.timestamps["release_wait_end"] - self.timestamps["release_wait_start"], 3))

    def _fixture_check_and_products(self) -> None:
        with self.tr.start_span("fixture_check", self.span) as sp:
            self.fixture = fixture_check(self.cfg.fixture_check_url)
            self.products, self.products_source = load_products(self.cfg.products_source,
                                                                self.cfg.fixture_check_url)
            sp.set(**{"fleetkit.products": len(self.products), "fleetkit.products_source": self.products_source})
        self._log("INFO", "fixture check ok", url=self.cfg.fixture_check_url, products=len(self.products))

    def _create_microvms(self) -> None:
        cfg, t = self.cfg, self.cfg.timeouts
        create_request = {
            "backend": cfg.backend, "count": cfg.density, "vcpus": cfg.vcpus, "mem_mib": cfg.mem_mib,
            "ready_timeout_s": t.ready_timeout_s, "max_lifetime_s": t.max_lifetime_s,
            "idle_timeout_s": t.idle_timeout_s, "launch_interval_ms": t.launch_interval_ms,
            "fault": cfg.fault,
        }
        if cfg.hypervisor is not None:
            create_request["hypervisor"] = dict(cfg.hypervisor)
        if cfg.chromium_extra_flags:
            create_request["chromium_extra_flags"] = list(cfg.chromium_extra_flags)
        if cfg.console:
            create_request["console"] = cfg.console
        if cfg.memory_pages:
            create_request["memory_pages"] = cfg.memory_pages
        with self.tr.start_span("microvms.create", self.span, {"fleetkit.count": cfg.density}) as sp:
            self.timestamps["create_start"] = time.time()
            created = self.client.create_microvms(create_request, self._headers(sp))
            for i, c in enumerate(created):
                mid = str(c.get("id") or c.get("microvm_id") or "")
                if not mid:
                    raise HostError(f"POST /microvms returned a microVM without an id: {c}")
                self.microvms.append(MicrovmRec(mid, int(c.get("slot", i)), str(c.get("address") or "")))
        self._log("INFO", f"created {len(self.microvms)} microVMs", ids=",".join(s.microvm_id for s in self.microvms))

    def _wait_all_ready(self) -> None:
        t = self.cfg.timeouts
        deadline = time.monotonic() + t.ready_timeout_s + READY_GRACE_S
        with self.tr.start_span("wait_all_ready", self.span) as sp:
            pending = {s.microvm_id: s for s in self.microvms}
            while pending:
                for mid in list(pending):
                    s = pending[mid]
                    try:
                        s.info = self.client.get_microvm(mid)
                    except HostError as exc:
                        s.driver_error = str(exc)
                        continue
                    if s.state == "ready" or s.state == "busy":
                        s.ready = True
                        del pending[mid]
                        self._log("INFO", "microVM ready", microvm_id=mid, slot=s.slot,
                                  startup_ms=s.info.get("startup_ms"))
                    elif s.state in schemas.TERMINAL_STATES or s.state == "destroying":
                        s.failed = True
                        del pending[mid]
                        self._log("WARN", "microVM failed before ready", microvm_id=mid, slot=s.slot,
                                  outcome=s.outcome, error=s.info.get("error"))
                if pending and time.monotonic() > deadline:
                    for mid, s in pending.items():
                        s.failed = True
                        s.driver_error = ("driver: readiness deadline exceeded without a terminal state "
                                          f"from hostd (state={s.state or 'unknown'})")
                        self._log("ERROR", s.driver_error, microvm_id=mid)
                    break
                if pending:
                    time.sleep(READY_POLL_INTERVAL_S)
            self.timestamps["all_ready"] = time.time()
            n_ready = sum(1 for s in self.microvms if s.ready)
            sp.set(**{"fleetkit.ready": n_ready, "fleetkit.failed": len(self.microvms) - n_ready})
        self._log("INFO", "wait-all-ready done", ready=n_ready, failed=len(self.microvms) - n_ready,
                  wait_s=round(self.timestamps["all_ready"] - self.timestamps["create_start"], 3))

    def _run_tasks(self, ready: list[MicrovmRec]) -> None:
        t = self.cfg.timeouts
        release = threading.Event()
        order = sorted(ready, key=lambda s: s.slot)
        with self.tr.start_span("tasks", self.span, {"fleetkit.count": len(order)}) as sp:
            with ThreadPoolExecutor(max_workers=len(order), thread_name_prefix="task") as pool:
                futures = [pool.submit(self._task_worker, s, i, release) for i, s in enumerate(order)]
                self.timestamps["barrier_release"] = time.time()
                release.set()
                self._log("INFO", "barrier released", tasks=len(order), launch_interval_ms=t.launch_interval_ms)
                for f in futures:
                    f.result()
            sp.set(**{"fleetkit.tasks_ok": sum(1 for s in order if s.task and s.task["ok"])})

    def _task_worker(self, s: MicrovmRec, index: int, release: threading.Event) -> None:
        cfg, t = self.cfg, self.cfg.timeouts
        release.wait()
        if t.launch_interval_ms > 0 and index > 0:
            time.sleep(index * t.launch_interval_ms / 1000.0)
        product = assign(self.products, s.slot)
        task_id = f"{self.trial_id}-slot{s.slot:03d}"
        payload = {
            "task_id": task_id, "fixture_base_url": cfg.guest_fixture_base_url(),
            "product_id": product.product_id, "query": product.query,
            "expected_title": product.expected_title,
            "step_timeout_ms": t.step_timeout_ms, "task_timeout_ms": t.task_timeout_ms,
            "sample_interval_ms": cfg.sample_interval_ms, "screenshot_each_step": bool(cfg.screenshot_each_step),
        }
        span = self.tr.start_span("task", self.span, {
            "fleetkit.microvm_id": s.microvm_id, "fleetkit.task_id": task_id,
            "fleetkit.slot": s.slot, "fleetkit.product_id": product.product_id})
        headers = self._headers(span)
        headers["X-Fleetkit-Task-Id"] = task_id
        reply = self.client.run_task(s.microvm_id, payload, t.client_timeout_ms / 1000.0, headers)
        body = reply.body if isinstance(reply.body, dict) else {}
        category, error = categorize(reply.status, reply.body, reply.transport_error)
        ok = category == "ok" and body.get("ok") is True
        with self._lock:
            lt = self.timestamps["last_task_return"]
            self.timestamps["last_task_return"] = reply.recv_ts if lt is None else max(lt, reply.recv_ts)

        # screenshot
        screenshot_path = ""
        b64 = body.get("screenshot_b64")
        if b64:
            try:
                self.rundir.screenshots_dir.mkdir(parents=True, exist_ok=True)
                p = self.rundir.screenshots_dir / f"{task_id}.jpg"
                p.write_bytes(base64.b64decode(b64))
                screenshot_path = str(p.relative_to(self.rundir.root))
            except Exception as exc:
                self._log("WARN", f"screenshot for {task_id} not saved: {exc}")
        step_shots = self._save_step_screenshots(task_id, body.get("step_screenshots"))

        # guest log tail
        log_tail = body.get("log_tail")
        if log_tail:
            try:
                self.rundir.guest_logs_dir.mkdir(parents=True, exist_ok=True)
                with open(self.rundir.guest_logs_dir / f"{s.microvm_id}.jsonl", "a", encoding="utf-8") as fh:
                    for line in (log_tail if isinstance(log_tail, list) else str(log_tail).splitlines()):
                        fh.write((json.dumps(line, sort_keys=True) if isinstance(line, dict) else str(line)) + "\n")
            except Exception as exc:
                self._log("WARN", f"guest log tail for {s.microvm_id} not saved: {exc}")

        guest_clock_ns = _int_or_none(body.get("guest_clock_ns"))
        clock_offset_ns = _int_or_none(_pick(body, "clock_offset_ns", "host.clock_offset_ns"))
        if clock_offset_ns is None and guest_clock_ns is not None:
            # design section 8; computed here only when the host daemon did not attach it
            rtt_ns = int((reply.recv_ts - reply.send_ts) * 1e9)
            clock_offset_ns = int(reply.send_ts * 1e9) + rtt_ns // 2 - guest_clock_ns

        row = {
            "run_id": cfg.run_id, "trial_id": self.trial_id, "backend": cfg.backend,
            "density": cfg.density, "trial_number": self.trial_number, "microvm_id": s.microvm_id, "slot": s.slot,
            "task_id": task_id, "product_id": product.product_id, "dispatch_ts": reply.send_ts,
            "task_ms": to_float(body.get("task_ms")), "wall_ms": round(reply.wall_ms, 3), "ok": ok,
            "failure_category": category, "failed_step": body.get("failed_step") or "",
            "bytes_received": body.get("bytes_received"), "request_count": body.get("request_count"),
            "guest_mem_available": _pick(body, "guest_mem_available", "guest_metrics.mem_available",
                                         "guest.mem_available"),
            "chromium_rss": _pick(body, "chromium_rss", "guest_metrics.chromium_rss", "guest.chromium_rss"),
            "screenshot_path": screenshot_path, "trace_id": self.span.trace_id,
            "clock_offset_ns": clock_offset_ns, "error": error,
            "timing_valid": body.get("timing_valid") if isinstance(body.get("timing_valid"), bool) else None,
            "guestd_cpu_ms": to_float(body.get("guestd_cpu_ms")), "trial_kind": cfg.trial_kind,
        }
        self.w.tasks.append(row)
        with self._lock:
            self.task_rows.append(row)
        steps = body.get("steps") if isinstance(body.get("steps"), list) else []
        failed_step = str(body.get("failed_step") or "")
        step_rows = []
        for st in steps:
            if not isinstance(st, dict):
                continue
            name = str(st.get("name", ""))
            step_rows.append({
                "run_id": cfg.run_id, "trial_id": self.trial_id, "task_id": task_id, "step_index": len(step_rows),
                "name": name,
                "dispatch_ts": _step_ts(st.get("dispatch_ns"), guest_clock_ns, clock_offset_ns),
                "settle_ts": _step_ts(st.get("settle_ns"), guest_clock_ns, clock_offset_ns),
                "duration_ms": st.get("duration_ms"),
                "error": error if (not ok and failed_step == name) else "",
                "bytes_received": st.get("bytes_received"), "request_count": st.get("request_count"),
            })
        if not ok and failed_step and not any(r["name"] == failed_step for r in step_rows):
            # The guest returns only the steps completed so far (design section 4), so the step
            # that failed has no record of its own: synthesize one so steps.csv carries the failed
            # step and its error. It never settled, so settle_ts and duration_ms stay empty.
            settled = [r["settle_ts"] for r in step_rows if r["settle_ts"] is not None]
            step_rows.append({
                "run_id": cfg.run_id, "trial_id": self.trial_id, "task_id": task_id, "step_index": len(step_rows),
                "name": failed_step, "dispatch_ts": settled[-1] if settled else reply.send_ts,
                "settle_ts": None, "duration_ms": None, "error": error,
            })
        for r in step_rows:
            self.w.steps.append(r)
        with self._lock:
            self.step_rows.extend(step_rows)
        samples = body.get("proc_samples") if isinstance(body.get("proc_samples"), list) else []
        self.w.guest_metrics.append_many(
            guest_metric_rows(samples, self.trial_id, s.microvm_id, task_id, guest_clock_ns, clock_offset_ns))
        s.task = {"task_id": task_id, "ok": ok, "failure_category": category, "failed_step": row["failed_step"],
                  "task_ms": row["task_ms"], "wall_ms": row["wall_ms"], "http_status": reply.status,
                  "steps": len(steps), "error": error, "screenshot_path": screenshot_path,
                  "dispatch_ts": reply.send_ts, "return_ts": reply.recv_ts,
                  "timing_valid": row["timing_valid"], "guestd_cpu_ms": row["guestd_cpu_ms"],
                  "proc_samples": len(samples), "step_screenshots": step_shots}
        span.set(**{"fleetkit.failure_category": category, "fleetkit.wall_ms": row["wall_ms"],
                    "fleetkit.task_ms": row["task_ms"], "http.status_code": reply.status})
        if not ok:
            span.error(f"{category}: {error}")
        span.end()
        try:
            s.info = self.client.get_microvm(s.microvm_id)
            s.state_after_task = s.state
        except HostError as exc:
            s.state_after_task = f"unknown ({exc})"
        self._log("INFO" if ok else "WARN", f"task {task_id} {category}", microvm_id=s.microvm_id,
                  wall_ms=row["wall_ms"], task_ms=row["task_ms"], failed_step=row["failed_step"],
                  state_after=s.state_after_task, error=error if not ok else "")

    def _save_step_screenshots(self, task_id: str, shots) -> list[str]:
        """screenshots/<task_id>-<step>.jpg for each untimed per-step screenshot (illustration trial)."""
        saved: list[str] = []
        if not isinstance(shots, list):
            return saved
        for i, shot in enumerate(shots):
            if not isinstance(shot, dict) or not shot.get("b64"):
                continue
            step = re.sub(r"[^A-Za-z0-9_.-]", "_", str(shot.get("step") or f"step{i}"))
            try:
                self.rundir.screenshots_dir.mkdir(parents=True, exist_ok=True)
                p = self.rundir.screenshots_dir / f"{task_id}-{step}.jpg"
                p.write_bytes(base64.b64decode(shot["b64"]))
                saved.append(str(p.relative_to(self.rundir.root)))
            except Exception as exc:
                self._log("WARN", f"step screenshot {step} for {task_id} not saved: {exc}")
        return saved

    def _cleanup(self) -> None:
        with self.tr.start_span("cleanup", self.span, {"fleetkit.count": len(self.microvms)}) as sp:
            with ThreadPoolExecutor(max_workers=max(1, len(self.microvms)), thread_name_prefix="destroy") as pool:
                list(pool.map(lambda s: self._destroy_one(s, sp), self.microvms))
            for s in self.microvms:
                self._write_microvm_row(s)
        self._log("INFO", "cleanup done", microvms=len(self.microvms))

    def _destroy_one(self, s: MicrovmRec, parent: Span) -> None:
        reply = self.client.delete_microvm(s.microvm_id, self._headers(parent))
        if reply.transport_error or reply.status >= 400 and reply.status != 404:
            s.driver_error = (s.driver_error + "; " if s.driver_error else "") + \
                f"DELETE: {reply.transport_error or 'HTTP ' + str(reply.status)}"
        deadline = time.monotonic() + DESTROY_GRACE_S
        while True:
            try:
                s.info = self.client.get_microvm(s.microvm_id)
            except HostError as exc:
                if exc.status == 404:
                    break  # gone entirely
                s.driver_error = (s.driver_error + "; " if s.driver_error else "") + f"GET after DELETE: {exc}"
                break
            if s.state in schemas.TERMINAL_STATES and s.info.get("destroyed_ts") is not None:
                break
            if time.monotonic() > deadline:
                s.driver_error = (s.driver_error + "; " if s.driver_error else "") + \
                    f"driver: not destroyed within {DESTROY_GRACE_S:.0f}s (state={s.state})"
                break
            time.sleep(READY_POLL_INTERVAL_S)

    def _write_microvm_row(self, s: MicrovmRec) -> None:
        if s.csv_written:
            return
        s.csv_written = True
        cfg, i = self.cfg, s.info
        outcome = s.outcome
        if outcome not in schemas.MICROVM_OUTCOMES:
            if s.failed and not outcome:
                outcome = "startup_timeout" if "deadline" in s.driver_error else (outcome or "startup_error")
            elif s.ready and s.task is not None and not outcome:
                outcome = "completed"
        err = "; ".join(x for x in (str(i.get("error") or ""), s.driver_error) if x)
        self.w.microvms.append({
            "run_id": cfg.run_id, "trial_id": self.trial_id, "microvm_id": s.microvm_id, "slot": s.slot,
            "backend": cfg.backend, "vcpus": cfg.vcpus, "mem_mib": cfg.mem_mib,
            "created_ts": i.get("created_ts"), "process_started_ts": i.get("process_started_ts"),
            "ready_ts": i.get("ready_ts"), "destroyed_ts": i.get("destroyed_ts"),
            "startup_ms": i.get("startup_ms"), "cleanup_ms": i.get("cleanup_ms"),
            "outcome": outcome, "error": err,
            "kernel_start_ts": i.get("kernel_start_ts"), "guestd_start_ts": i.get("guestd_start_ts"),
            "chromium_launch_ts": i.get("chromium_launch_ts"), "chromium_ready_ts": i.get("chromium_ready_ts"),
        })
        s.info["outcome"] = outcome

    def _verify_clean(self) -> None:
        with self.tr.start_span("verify_clean", self.span) as sp:
            self.verify_clean = self.client.verify_clean()
            clean = bool(self.verify_clean.get("clean"))
            sp.set(**{"fleetkit.clean": clean})
            if clean:
                self.timestamps["verify_clean_pass"] = time.time()
            else:
                sp.error("leftovers")
                self.error = self.error or f"verify-clean failed: {self.verify_clean.get('leftovers')}"
        self._log("INFO" if clean else "ERROR", "verify-clean " + ("clean" if clean else "NOT clean"),
                  leftovers=json.dumps(self.verify_clean.get("leftovers", [])))

    # ---- trial.json --------------------------------------------------------------------
    def _write_trial_json(self, final: bool = False) -> dict:
        cfg = self.cfg
        n = cfg.density
        microvms_ready = sum(1 for s in self.microvms if s.ready)
        microvms_failed = sum(1 for s in self.microvms if s.failed)
        tasks = [s.task for s in self.microvms if s.task]
        tasks_ok = sum(1 for t in tasks if t["ok"])
        by_cat = collections.Counter(t["failure_category"] for t in tasks)
        by_outcome = collections.Counter()
        for s in self.microvms:
            o = s.outcome or ("startup_timeout" if s.failed and "deadline" in s.driver_error else "")
            if o:
                by_outcome[o] += 1
        clean = bool(self.verify_clean and self.verify_clean.get("clean"))
        complete = final and not self._interrupted
        protocol_ok = (complete and len(self.microvms) == n and microvms_ready == n and len(tasks) == n
                       and tasks_ok == n and clean and self.error is None)
        if not complete or self.error or not self.microvms or microvms_ready == 0 or not clean:
            status = "failed"
        elif microvms_failed or tasks_ok < len(tasks) or len(tasks) < n:
            status = "degraded"
        else:
            status = "ok"

        step_rows: dict[str, list[float]] = collections.defaultdict(list)
        task_ms, wall_ms, overhead, failed_elapsed, bytes_, reqs = [], [], [], [], [], []
        for s in self.microvms:
            if not s.task:
                continue
            t = s.task
            if t["ok"]:
                # Timing percentiles describe the standard task, so only ok tasks count; a failed
                # task's task_ms is its elapsed-to-failure (guestd task.py _failure) and is
                # summarized separately as failed_task_elapsed_ms.
                if t["task_ms"] is not None:
                    task_ms.append(t["task_ms"])
                    overhead.append(t["wall_ms"] - t["task_ms"])
                wall_ms.append(t["wall_ms"])
            elif t["task_ms"] is not None:
                failed_elapsed.append(t["task_ms"])
        # per-step percentiles come from the rows already appended for this trial
        try:
            from .outputs import read_csv
            for r in read_csv(self.rundir.steps_csv):
                if r.get("trial_id") == self.trial_id and r.get("duration_ms"):
                    step_rows[r["name"]].append(float(r["duration_ms"]))
            for r in read_csv(self.rundir.tasks_csv):
                if r.get("trial_id") == self.trial_id:
                    if r.get("bytes_received"):
                        bytes_.append(float(r["bytes_received"]))
                    if r.get("request_count"):
                        reqs.append(float(r["request_count"]))
        except Exception:
            pass

        doc = {
            "run_id": cfg.run_id, "trial_id": self.trial_id, "trace_id": self.span.trace_id if self.span else "",
            "sequence": self.sequence, "trial_kind": cfg.trial_kind,
            "backend": cfg.backend, "density": n, "trial_number": self.trial_number, "fault": cfg.fault,
            "vcpus": cfg.vcpus, "mem_mib": cfg.mem_mib, "host_id": cfg.host_id,
            "hypervisor": cfg.hypervisor,
            "timeouts": cfg.timeouts.as_dict(),
            "fixture": {"check_url": cfg.fixture_check_url, "guest_base_url": cfg.guest_fixture_base_url(),
                        "products_source": self.products_source, "products": len(self.products)},
            "release_after_ready_s": float(cfg.release_after_ready_s),
            "chromium_extra_flags": list(cfg.chromium_extra_flags),
            "timestamps": dict(self.timestamps),
            "pre_trial": self.pre_trial,
            "sample_interval_ms": cfg.sample_interval_ms, "screenshot_each_step": bool(cfg.screenshot_each_step),
            "counts": {
                "microvms_requested": n, "microvms_created": len(self.microvms),
                "microvms_ready": microvms_ready, "microvms_failed_startup": microvms_failed,
                "microvms_by_outcome": dict(by_outcome),
                "tasks_dispatched": len(tasks), "tasks_ok": tasks_ok,
                "tasks_by_failure_category": dict(by_cat),
            },
            "status": status, "complete": complete, "phase": self.phase,
            "error": self.error,
            "verify_clean": self.verify_clean,
            "percentiles": {
                "steps": {name: {"count": len(v), "p50": percentile(v, 50), "p95": percentile(v, 95)}
                          for name, v in step_rows.items()},
                "task_ms": {"count": len(task_ms), "p50": percentile(task_ms, 50), "p95": percentile(task_ms, 95)},
                "wall_ms": {"count": len(wall_ms), "p50": percentile(wall_ms, 50), "p95": percentile(wall_ms, 95)},
                "harness_overhead_ms": summary(overhead),
                "failed_task_elapsed_ms": summary(failed_elapsed),
                "startup_ms": summary([to_float(s.info.get("startup_ms")) for s in self.microvms]),
                "cleanup_ms": summary([to_float(s.info.get("cleanup_ms")) for s in self.microvms]),
            },
            "means": {"bytes_received": mean(bytes_), "request_count": mean(reqs)},
            "microvms": [{
                "microvm_id": s.microvm_id, "slot": s.slot, "address": s.address, "state": s.state,
                "outcome": s.outcome, "startup_ms": s.info.get("startup_ms"), "cleanup_ms": s.info.get("cleanup_ms"),
                "ready": s.ready, "failed_startup": s.failed, "state_after_task": s.state_after_task,
                "error": s.info.get("error"), "driver_error": s.driver_error,
                # what the microVM's browser process runs with, read back in the guest (null: not reported)
                "chromium_flags": s.running_flags,
                "task": s.task,
            } for s in self.microvms],
            "written_at": time.time(),
        }
        doc["criteria"] = cfg.criteria
        doc["evaluation"] = None
        if final:
            with self._lock:
                task_rows, step_rows = list(self.task_rows), list(self.step_rows)
            doc["evaluation"] = evaluate_trial(doc, task_rows, step_rows, cfg.criteria)
        # ``protocol_ok`` says only that the protocol held (every microVM ready, every task ok,
        # verify-clean clean). ``passed`` is the trial's verdict against its criteria, equal to
        # evaluation.passed, and stays null until the trial is final.
        doc["protocol_ok"] = protocol_ok
        doc["passed"] = bool(doc["evaluation"].get("passed")) if doc["evaluation"] is not None else None
        write_json_atomic(self.rundir.trial_json_path(self.trial_id), doc)
        return doc


# ---- categorization ------------------------------------------------------------------------

def categorize(status: int, body, transport_error: str | None) -> tuple[str, str]:
    """Map a proxied task reply to a failure category from the closed set and an error string."""
    if transport_error:
        return "guest_unreachable", f"driver client: {transport_error}"
    b = body if isinstance(body, dict) else {}
    cat = b.get("failure_category")
    err = str(b.get("error") or "")
    if cat in schemas.FAILURE_CATEGORIES:
        if cat == "ok" and b.get("ok") is not True:
            return "assertion_failed", err or "guest reported failure_category ok with ok=false"
        return cat, err
    if status == 409:
        return "microvm_not_ready", err or "HTTP 409"
    if status == 0 or status >= 500:
        return "guest_unreachable", err or f"HTTP {status}: {_short(body)}"
    return "guest_unreachable", err or f"HTTP {status} with unknown failure_category {cat!r}: {_short(body)}"


GUEST_SAMPLE_SCALARS = ("cpu_total_ms", "cpu_idle_ms", "mem_available", "psi_cpu_some_total_us")
GUEST_GROUP_FIELDS = ("cpu_ms", "rss_bytes", "procs")


def guest_metric_rows(samples, trial_id: str, microvm_id: str, task_id: str,
                      guest_clock_ns, clock_offset_ns) -> list[dict]:
    """guest_metrics.csv rows (long format) from a task result's ``proc_samples``. ``t_ns`` shares the
    steps' zero (task receipt), so ts = (guest_clock_ns + t_ns + clock_offset_ns) / 1e9. Nulls are skipped."""
    rows: list[dict] = []
    for smp in samples or []:
        if not isinstance(smp, dict):
            continue
        ts = _step_ts(smp.get("t_ns"), guest_clock_ns, clock_offset_ns)
        base = {"ts": ts, "trial_id": trial_id, "microvm_id": microvm_id, "task_id": task_id}
        for k in GUEST_SAMPLE_SCALARS:
            v = smp.get(k)
            if _is_num(v):
                rows.append({**base, "metric": k, "value": v})
        groups = smp.get("groups") if isinstance(smp.get("groups"), dict) else {}
        for g in sorted(groups):
            vals = groups[g] if isinstance(groups[g], dict) else {}
            for f in GUEST_GROUP_FIELDS:
                v = vals.get(f)
                if _is_num(v):
                    rows.append({**base, "metric": f"{f}.{g}", "value": v})
    return rows


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pick(body: dict, *paths):
    for p in paths:
        cur = body
        for part in p.split("."):
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur is not None:
            return cur
    return None


def _int_or_none(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _step_ts(ns, guest_clock_ns, clock_offset_ns):
    """Step timestamps arrive as monotonic offsets from task receipt (guest_clock_ns, realtime).
    Converted to host Unix seconds with the clock offset. Absolute realtime ns pass through."""
    n = _int_or_none(ns)
    if n is None:
        return None
    if n > 10**17:  # already absolute realtime nanoseconds
        return (n + (clock_offset_ns or 0)) / 1e9
    if guest_clock_ns is None:
        return None
    return (guest_clock_ns + n + (clock_offset_ns or 0)) / 1e9


def _short(obj, n: int = 160) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + "..."
