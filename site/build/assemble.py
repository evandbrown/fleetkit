"""Builds one run's published documents (site/DATA.md) field by field from a RunSource.

Every output object is constructed here from named fields; nothing from a run directory is copied through.
The rules come from rules.py.
"""
from __future__ import annotations

import bisect
import collections
import datetime as dt
import re

import rules as R
import source as S
from rules import num, r4, rng, sig3

SCHEMA = "fleetkit-site-data/2"
GIB, MIB = 2 ** 30, 2 ** 20
STEP_LABEL = {"home": "home", "search": "search", "open_product": "open product", "add_to_cart": "add to cart",
              "verify_cart": "verify cart", "task": "task"}
GUEST_FOLD = {"renderer": "renderer", "browser": "browser", "gpu": "other_chromium", "network": "other_chromium",
              "utility": "other_chromium", "zygote": "other_chromium", "chromium_other": "other_chromium",
              "guestd": "guestd", "other": "other"}
EXCLUDED = {
    "warmup": "The first microVM after setup reads its disk from storage; later ones read it from memory.",
    "illustration": "It takes a screenshot after every step, which adds time inside the task.",
}


class BuildError(Exception):
    """A document would break DATA.md, or the source disagrees with itself: refused, not repaired."""


def minute(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def fmt_int(v) -> str:
    return f"{round(v):,}"


def value_at(series, t):
    """A cumulative counter at t by linear interpolation, clamped to its first and last samples."""
    if not series:
        return None
    times = [x for x, _ in series]
    i = bisect.bisect_right(times, t)
    if i == 0:
        return series[0][1]
    if i == len(series):
        return series[-1][1]
    (t0, v0), (t1, v1) = series[i - 1], series[i]
    return v1 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def hypervisor_info(src: S.RunSource) -> dict:
    hi = src.run["observed"]["host_info"]
    return hi.get("hypervisor") or hi.get(src.run["backend"]) or {}


def rule_defs(src: S.RunSource) -> list[dict]:
    """The attribution rules, with the thresholds the harness judged this run by."""
    th = next((t.report["attribution"]["thresholds"] for t in src.trials
               if (t.report.get("attribution") or {}).get("thresholds")), None)
    if th is None:
        return []
    return [{"key": key, "label": label, "verdict": verdict, "op": op, "threshold": num(th[old])}
            for old, _, key, label, verdict, op in S.RULES if old in th]


def host_facts(src: S.RunSource, host_kind: str) -> dict:
    """What the run measured about its worker host; the host kind follows from the instance type."""
    hi, gi = src.run["observed"]["host_info"], src.run["observed"]["guest_info"]
    return {
        "instance_type": src.manifest["instance_type"],
        "host_kind": host_kind,
        "vcpus": int(hi["cpu_count"]),
        "cores": int(hi["cores_per_socket"]) * int(hi["sockets"]),
        "threads_per_core": int(hi["threads_per_core"]),
        "sockets": int(hi["sockets"]),
        "cpu_model": str(hi["cpu_model"]),
        "mem_gib": round(hi["mem_total"] / GIB, 1),
        "kernel_release": str(hi["kernel_release"]),
        "hypervisor_version": str(hypervisor_info(src).get("version", "")),
        "chromium_version": str(gi["chromium_version"]).removeprefix("Chrome/"),
    }


COMMIT = re.compile(r"^[0-9a-f]{40}$")


def harness_commit(src: S.RunSource, run_id: str, log) -> str | None:
    """The commit of the harness code the run used (D73), as the run recorded it: run.json's harness.git_commit, or,
    for a run recorded before run.json had a harness section (cap-baseline-1), the commit its manifest and its old
    run.json record. None when it recorded none, or recorded one with uncommitted changes ("-dirty"): the code at
    that commit isn't exactly what ran, so nothing is linked rather than something close."""
    recorded = {str(v) for v in ((src.run.get("harness") or {}).get("git_commit"),
                                 (src.run.get("spec") or {}).get("git_commit"),
                                 src.manifest.get("git_commit")) if v}
    if len(recorded) > 1:
        raise BuildError(f"{run_id}: the run records different harness commits: {sorted(recorded)}")
    if not recorded:
        log(f"  {run_id}: no harness commit recorded; none linked")
        return None
    commit = recorded.pop()
    if commit.endswith("-dirty"):
        log(f"  {run_id}: its harness had uncommitted changes ({commit[:7]}-dirty); none linked")
        return None
    if not COMMIT.match(commit):
        raise BuildError(f"{run_id}: harness commit {commit!r} isn't a full commit id")
    return commit


def trial_ids(trials: list[S.TrialSource]) -> list[tuple[str, int | None]]:
    """Each published trial's id and number, in execution order (DATA.md, Ids): a trial that counts is numbered
    from 1 within its density ("d8-t2"); one that doesn't is labelled by its role ("warmup", then "warmup-2")."""
    numbers, nth, out = collections.Counter(), collections.Counter(), []
    for t in trials:
        if t.role in R.COUNTING:
            numbers[t.density] += 1
            out.append((f"d{t.density}-t{numbers[t.density]}", numbers[t.density]))
        else:
            nth[t.role] += 1
            out.append((t.role if nth[t.role] == 1 else f"{t.role}-{nth[t.role]}", None))
    return out


def build_run(src: S.RunSource, *, campaign: str, run_id: str, spec_name: str, replica: int, spec: dict,
              host_of: dict, images, log) -> dict:
    """The run's RunEntry, RunDoc and TrialDocs, and the rules it was judged by."""
    run = src.run
    t0, t_end = float(run["started_ts"]), float(run["ended_ts"])
    host = host_facts(src, host_of["host_kind"])
    price = host_of["price_usd_per_hour"]
    crit = spec["criteria"]
    rds = rule_defs(src)
    ncpu = host["vcpus"]
    interval_ms = float(src.run["spec"].get("sample_interval_ms") or 200)
    interval_s = interval_ms / 1000
    util = src.host.get(("host", "cpu_util"), [])      # the host's busy samples, the whole run
    settle_s = spec["procedure"]["settle_s"]
    overhead_mib = num(hypervisor_info(src).get("mem_overhead_mib", 0))

    ids = trial_ids(src.trials)
    summaries, docs = [], []
    tasks = {k: [] for k in ["trial", "microvm", "product", "ok", "task_ms", "wall_ms", "failed_step",
                             "failure_category", "timing_valid", "img"]}
    steps = {k: [] for k in ["trial", "microvm", "step", "duration_ms", "ok"]}
    clamped = 0
    for order, ts_ in enumerate(src.trials, 1):
        tj, rt, role, density = ts_.doc, ts_.report, ts_.role, ts_.density
        counts = role in R.COUNTING
        tid, number = ids[order - 1]
        stamps = tj["timestamps"]
        cs = float(stamps["create_start"])

        def rel(x, cs=cs):
            return None if x is None or x == "" else round((float(x) - cs) * 1000)

        marks = {"all_ready_ms": rel(stamps.get("all_ready")), "release_ms": rel(stamps.get("barrier_release")),
                 "last_return_ms": rel(stamps.get("last_task_return")), "clean_ms": rel(stamps.get("verify_clean_pass"))}
        marks["end_ms"] = (marks["clean_ms"] or marks["last_return_ms"] or marks["all_ready_ms"] or 0) + 1000

        evaluation = rt.get("evaluation") or tj.get("evaluation") or {"checks": [], "passed": False}
        checks = []
        for c in evaluation["checks"]:
            parts = c["name"].split()
            subject, stat = (parts[1], parts[2]) if parts[0] == "step" else (parts[0], parts[1])
            checks.append({"subject": subject, "stat": stat, "value_ms": r4(c["value"]), "target_ms": num(c["target"]),
                           "met": bool(c["met"])})
        cnt = tj["counts"]
        limit_ms = int(crit["ready_timeout_s"] * 1000)
        n_ready = int(cnt["microvms_ready"])
        ready = {"microvms": n_ready, "all_ready_ms": marks["all_ready_ms"], "limit_ms": limit_ms,
                 "met": n_ready == density and int(cnt["microvms_requested"]) == density
                 and marks["all_ready_ms"] is not None and marks["all_ready_ms"] <= limit_ms}
        task_counts = {"ok": int(cnt["tasks_ok"]), "of": density}
        clean = bool((tj.get("verify_clean") or {}).get("clean"))
        failed = []
        if not ready["met"]:
            failed.append(f"{n_ready} of {density} microVMs ready" if n_ready < density
                          else f"all ready at {marks['all_ready_ms'] / 1000:.1f} s, over the {crit['ready_timeout_s']} s limit")
        if task_counts["ok"] < task_counts["of"]:
            failed.append(f"{task_counts['of'] - task_counts['ok']} of {task_counts['of']} tasks failed")
        failed += [f"{STEP_LABEL[c['subject']]} {c['stat']} {fmt_int(c['value_ms'])} ms > {fmt_int(c['target_ms'])} ms"
                   for c in checks if not c["met"]]
        if not clean:
            failed.append("something was left on the host")
        passed = None
        if counts:
            passed = bool(evaluation["passed"])
            if passed != R.trial_passes(ready["met"], task_counts["ok"], task_counts["of"], checks, clean):
                raise BuildError(f"{run_id} {tid}: the harness says passed={passed}, rule 1 says otherwise")

        # attribution: the rule's signals, verdicts recomputed and checked against the harness's
        at = rt.get("attribution")
        attribution, host_summary, mem_peak, lim_vms = None, None, None, []
        vm_attr = {}
        if at and at.get("host"):
            h = at["host"]
            throttled = at.get("vm_throttled_fraction_mean", at.get("microvm_throttled_fraction_mean"))
            signals = {key: (throttled if sig == "vm_throttled_fraction_mean" else h.get(sig))
                       for _, sig, key, *_ in S.RULES}
            verdicts = R.verdicts_from_rules(rds, signals)
            theirs = [S.VERDICTS.get(v, v) for v in at.get("verdicts") or []]
            if theirs and theirs != verdicts:
                raise BuildError(f"{run_id} {tid}: verdicts {verdicts} recomputed, the harness says {theirs}")
            g = at.get("guest") or {}
            total, idle = g.get("busy_cpu_s"), g.get("idle_cpu_s")   # the harness's busy_cpu_s is total guest CPU
            share = None
            if total is not None and idle is not None:
                folded = dict.fromkeys(R.GUEST, 0.0)
                for grp, x in (g.get("groups") or {}).items():
                    folded[GUEST_FOLD.get(grp, "other")] += x["cpu_s"]
                busy = total - idle
                folded["other"] += max(0.0, busy - sum(x["cpu_s"] for x in (g.get("groups") or {}).values()))
                denom = max(busy, sum(folded.values()))
                share = {k: r4(v / denom) for k, v in folded.items()} if denom > 0 else None
            window_s = at["window"]["seconds"]
            attribution = {
                "window_ms": [marks["release_ms"], marks["last_return_ms"]],
                "verdicts": verdicts,
                "rules": [{"key": rd["key"], "value": r4(signals[rd["key"]]),
                           "fired": R.fired(signals[rd["key"]], rd["op"], rd["threshold"])} for rd in rds],
                "host_cpu_s": {"microvm_vcpus": r4(at["vcpu_s"]), "hypervisor": r4(at["hypervisor_s"]),
                               "hostd": r4(at["hostd_cpu_s"]), "driver": r4(at["driver_cpu_s"]),
                               "unattributed": r4(at["unattributed_cpu_s"]), "busy": r4(h["busy_cpu_s"])},
                "guest_share": share,
                "missing": [S.SIGNAL_TO_RULE[m] for m in at.get("missing") or [] if m in S.SIGNAL_TO_RULE],
            }
            host_summary = {"cpu_util_pct": r4(h["cpu_util_mean_pct"]), "cpu_pressure_pct": r4(h.get("psi_cpu_some_pct")),
                            "busy_cores": r4(h["busy_cpu_s"] / window_s) if window_s else None,
                            "mem_used_gib": r4((1 - h["mem_available_min_fraction"]) * h["mem_total"] / GIB),
                            "steal_pct": r4(h.get("steal_mean_pct"))}
            vm_attr = {x["microvm_id"]: x for x in at.get("microvms") or []}
            mem_peak = rng([x["cgroup_memory_peak_bytes"] / MIB for x in vm_attr.values()
                            if x.get("cgroup_memory_peak_bytes") is not None])

        vm_rows = src.microvm_rows.get(ts_.id, {})
        cost = None
        if counts and marks["release_ms"] is not None and marks["last_return_ms"] is not None and marks["clean_ms"] is not None:
            exec_s = float(stamps["last_task_return"]) - float(stamps["barrier_release"])
            obs_s = float(stamps["verify_clean_pass"]) - cs
            # A warm start waits between all ready and the release. The charged burst leaves the wait out of the
            # observed window and adds back the host CPU used during it (rule 5).
            rws, rwe = stamps.get("release_wait_start"), stamps.get("release_wait_end")
            wait_s = float(rwe) - float(rws) if rws and rwe else 0.0
            wait_busy = R.host_busy_s(util, float(rws), float(rwe), interval_s) * ncpu if wait_s else 0.0
            # Host CPU per task over the microVMs' whole lives, first create to last destroy (rule 5, steady state).
            # A microVM without a destroy time lives until its last sample.
            created = [float(m["created_ts"]) for m in vm_rows.values() if m.get("created_ts")]
            destroyed = [float(m["destroyed_ts"]) if m.get("destroyed_ts")
                         else (src.host.get((m["microvm_id"], "cpu_usage_usec")) or [(None, None)])[-1][0]
                         for m in vm_rows.values()]
            per_task = None
            if created and destroyed and None not in destroyed:
                t_first, t_last = min(created), max(destroyed)
                if R.host_samples_cover(util, t_first, t_last, interval_s):
                    per_task = R.host_busy_s(util, t_first, t_last, interval_s) * ncpu / density
            # The model's inputs are published at summary precision, and the steady-state cost is computed from
            # the published values, so the contract can recompute it. Whether a trial gets one is decided per
            # density, once every trial there is known (below).
            busy_fraction = (r4(host_summary["cpu_util_pct"] / 100) if host_summary and host_summary["cpu_util_pct"] is not None
                             else None)
            cost = {"execution": r4(R.cost_per_1000(price, exec_s, density)),
                    "observed": r4(R.cost_per_1000(price, obs_s, density)),
                    "observed_charged": r4(R.observed_charged_per_1000(price, obs_s, wait_s, wait_busy, ncpu, density)),
                    "steady_state": None,
                    "host_cpu_per_task_s": r4(per_task),
                    "host_busy_fraction": busy_fraction}
            theirs = rt.get("cost_usd_per_task") or {}
            if theirs.get("execution_only"):
                expect = theirs["execution_only"] * 1000
                if abs(cost["execution"] - expect) > 0.01 * expect + 1e-4:
                    raise BuildError(f"{run_id} {tid}: execution cost {cost['execution']} differs from the harness's {expect:.4f}")

        summary = {
            "id": tid, "density": density, "number": number, "role": role, "counts": counts, "order": order,
            "passed": passed,
            "at_limit": bool(counts and passed and attribution and attribution["verdicts"][0] not in ("none", "unknown")),
            "failed": failed if counts else [],
            "ready": ready, "tasks": task_counts, "checks": checks, "clean": clean, "marks": marks,
            "attribution": attribution, "host": host_summary, "microvm_mem_peak_mib": mem_peak,
            "cost_per_1000_tasks": cost,
        }
        if not counts:
            summary["excluded_because"] = EXCLUDED[role]
        summaries.append(summary)

        # microVM lanes, the run's task and step columns, the trial's series
        lanes, vm_series, guest_series = [], [], []
        task_rows = src.task_rows.get(ts_.id, {})
        by_slot = {int(m["slot"]): m for m in tj.get("microvms") or []}
        vm_ids = []
        for slot, srow in sorted(vm_rows.items()):
            idx = slot + 1
            trow = task_rows.get(slot)
            vm_ids.append(srow["microvm_id"])

            def f(k, srow=srow):
                return float(srow[k]) if srow.get(k) else None

            lane = {"index": idx, "product": trow["product_id"] if trow else "", "outcome": srow["outcome"],
                    "ready_ms": rel(f("ready_ts"))}
            if srow.get("kernel_start_ts"):
                lane["boot"] = {"process_started_ms": rel(f("process_started_ts")), "kernel_start_ms": rel(f("kernel_start_ts")),
                                "guestd_start_ms": rel(f("guestd_start_ts")), "chromium_launch_ms": rel(f("chromium_launch_ts")),
                                "chromium_ready_ms": rel(f("chromium_ready_ts"))}
            lane_steps = []
            img = None
            if trow:
                ok = trow["ok"] == "true"
                task = (by_slot.get(slot) or {}).get("task") or {}
                dispatch = float(trow["dispatch_ts"])
                ret = task.get("return_ts") or dispatch + float(trow["wall_ms"]) / 1000
                lane["task"] = {"dispatch_ms": rel(dispatch), "return_ms": rel(ret), "task_ms": r4(float(trow["task_ms"])),
                                "wall_ms": r4(float(trow["wall_ms"])), "ok": ok}
                if not ok:
                    if trow.get("failed_step"):
                        lane["task"]["failed_step"] = trow["failed_step"]
                    lane["task"]["failure_category"] = trow["failure_category"]
                lane["task"].update({"bytes": int(trow["bytes_received"] or 0), "requests": int(trow["request_count"] or 0),
                                     "chromium_rss_mib": round(float(trow["chromium_rss"] or 0) / MIB),
                                     "clock_offset_ms": round(float(trow["clock_offset_ns"] or 0) / 1e6, 1),
                                     "timing_valid": trow["timing_valid"] == "true"})
                if trow.get("screenshot_path"):
                    img = images.add(src.dir / trow["screenshot_path"])
                if img:
                    lane["img"] = img
                tasks["trial"].append(tid)
                tasks["microvm"].append(idx)
                tasks["product"].append(trow["product_id"])
                tasks["ok"].append(ok)
                tasks["task_ms"].append(r4(float(trow["task_ms"])) if trow["task_ms"] else None)
                tasks["wall_ms"].append(r4(float(trow["wall_ms"])) if trow["wall_ms"] else None)
                tasks["failed_step"].append(trow["failed_step"] or None)
                tasks["failure_category"].append(None if trow["failure_category"] in ("ok", "") else trow["failure_category"])
                tasks["timing_valid"].append(trow["timing_valid"] == "true")
                tasks["img"].append(img)
                task_steps = sorted(src.step_rows.get(trow["task_id"], []), key=lambda x: int(x["step_index"]))
                # Steps are recorded on the host clock (guest clock + offset). If they weren't, they'd fall outside
                # the task's own dispatch-to-return span by about the offset.
                for s in task_steps:
                    a = (float(s["dispatch_ts"]) - dispatch) * 1000
                    b = (float(s["settle_ts"]) - dispatch) * 1000
                    if a < -5 or b > float(trow["wall_ms"]) + 5:
                        raise BuildError(f"{run_id} {tid} microVM {idx}: step {s['name']} runs {a:.0f}–{b:.0f} ms "
                                         f"after dispatch, outside the task's {float(trow['wall_ms']):.0f} ms; "
                                         "the step clock isn't corrected")
                for s in task_steps:
                    st = {"name": s["name"], "start_ms": rel(s["dispatch_ts"]), "end_ms": rel(s["settle_ts"]),
                          "ok": not s["error"]}
                    if s.get("bytes_received"):
                        st["bytes"] = int(s["bytes_received"])
                        st["requests"] = int(s["request_count"] or 0)
                    lane_steps.append(st)
                    steps["trial"].append(tid)
                    steps["microvm"].append(idx)
                    steps["step"].append(s["name"])
                    steps["duration_ms"].append(r4(float(s["duration_ms"])))
                    steps["ok"].append(not s["error"])
            lane["steps"] = lane_steps
            destroyed = f("destroyed_ts")
            lane["destroy"] = ({"start_ms": rel(destroyed - float(srow["cleanup_ms"]) / 1000), "end_ms": rel(destroyed)}
                               if destroyed and srow.get("cleanup_ms") else None)
            lanes.append(lane)

            va = vm_attr.get(srow["microvm_id"])
            if va:
                lim_vms.append({"index": idx, "vcpu_s": r4(va["vcpu_s"]), "hypervisor_s": r4(va["hypervisor_s"]),
                                "throttled_fraction": r4(va["throttled_fraction"]),
                                "cpu_pressure_pct": r4(va.get("cgroup_cpu_pressure_some_pct")),
                                "mem_peak_mib": r4(va["cgroup_memory_peak_bytes"] / MIB)})
            sid = srow["microvm_id"]
            vcpu = src.host.get((sid, "cpu_vcpu_usec"), [])
            if vcpu:
                hyp = src.host.get((sid, "cpu_hypervisor_usec"), [])
                thr = src.host.get((sid, "cpu_throttled_usec"), [])
                mem = src.host.get((sid, "cgroup_memory_current"), [])
                vs = {"index": idx, "t_ms": [], "vcpu_cores": [], "hypervisor_cores": [], "throttled_fraction": [],
                      "mem_mib": []}
                for (ta, va_), (tb, vb) in zip(vcpu, vcpu[1:]):
                    dtu = (tb - ta) * 1e6
                    if dtu <= 0:
                        continue
                    vs["t_ms"].append(rel(tb))
                    vs["vcpu_cores"].append(sig3(max(0.0, (vb - va_) / dtu)))
                    vs["hypervisor_cores"].append(sig3(max(0.0, (value_at(hyp, tb) - value_at(hyp, ta)) / dtu)) if hyp else 0)
                    vs["throttled_fraction"].append(sig3(max(0.0, (value_at(thr, tb) - value_at(thr, ta)) / dtu)) if thr else 0)
                    vs["mem_mib"].append(sig3(value_at(mem, tb) / MIB) if mem else 0)
                vm_series.append(vs)
            if trow and src.guest.get(trow["task_id"]):
                gs = {"index": idx, "t_ms": [], **{k: [] for k in R.GUEST}}
                for gts, m in sorted(src.guest[trow["task_id"]].items()):
                    folded = dict.fromkeys(R.GUEST, 0.0)
                    for k, v in m.items():
                        if k.startswith("cpu_ms."):
                            folded[GUEST_FOLD.get(k[len("cpu_ms."):], "other")] += v
                    busy = m.get("cpu_total_ms", 0) - m.get("cpu_idle_ms", 0)
                    folded["other"] += max(0.0, busy - sum(v for k, v in m.items() if k.startswith("cpu_ms.")))
                    gs["t_ms"].append(rel(gts))
                    for k in R.GUEST:
                        gs[k].append(sig3(folded[k] / interval_ms))
                guest_series.append(gs)
        if len(lanes) != density:
            raise BuildError(f"{run_id} {tid}: {len(lanes)} microVMs recorded at density {density}")

        window = [-int(settle_s * 1000), marks["end_ms"]]
        lo, hi_t = cs + window[0] / 1000, cs + window[1] / 1000
        hs = {"t_ms": [], "cpu_util_pct": [], "mem_used_gib": [], "cpu_pressure_pct": [], "mem_pressure_pct": [],
              "io_pressure_pct": [], "steal_pct": [], "cores": {k: [] for k in R.CONSUMERS}}
        mem_total = dict(src.host.get(("host", "mem_total"), []))
        mem_av = dict(src.host.get(("host", "mem_available"), []))
        steal = dict(src.host.get(("host", "steal"), []))
        for (ta, _), (tb, ub) in zip(util, util[1:]):
            if not lo <= tb <= hi_t or tb <= ta:
                continue
            dtu = (tb - ta) * 1e6

            def rate(key, ta=ta, tb=tb, dtu=dtu):
                s = src.host.get(key)
                return max(0.0, (value_at(s, tb) - value_at(s, ta)) / dtu) if s else 0.0

            hs["t_ms"].append(rel(tb))
            hs["cpu_util_pct"].append(sig3(ub))
            hs["mem_used_gib"].append(sig3((mem_total[tb] - mem_av[tb]) / GIB) if tb in mem_total and tb in mem_av else None)
            hs["cpu_pressure_pct"].append(sig3(100 * rate(("host", "psi_cpu_some_total"))))
            hs["mem_pressure_pct"].append(sig3(100 * rate(("host", "psi_memory_some_total"))))
            hs["io_pressure_pct"].append(sig3(100 * rate(("host", "psi_io_some_total"))))
            hs["steal_pct"].append(sig3(steal.get(tb, 0)))
            vm = sum(rate((v, "cpu_vcpu_usec")) for v in vm_ids)
            hyp = sum(rate((v, "cpu_hypervisor_usec")) for v in vm_ids)
            hostd, drv = rate(("host", "hostd_cpu_usec")), rate(("driver", "driver_cpu_usec"))
            rest = ub / 100 * ncpu - vm - hyp - hostd - drv
            clamped += rest < 0
            for k, v in zip(R.CONSUMERS, (vm, hyp, hostd, drv, max(0.0, rest))):
                hs["cores"][k].append(sig3(v))
        series = {"host": hs}
        if vm_series:
            series["microvms"] = vm_series
        if guest_series:
            series["guest"] = guest_series
        doc = {"schema": SCHEMA, "campaign": campaign, "run": run_id, "id": tid, "density": density,
               "number": number, "role": role, "window_ms": window, "marks": marks}
        pre = tj.get("pre_trial")
        if pre:
            doc["settle"] = {"seconds": num(pre["settle_s"]), "cpu_util_mean_pct": r4(pre["cpu_util_mean"])}
        doc.update({"full_size_screenshots": False, "microvms": lanes, "series": series,
                    "limit": {"verdicts": attribution["verdicts"] if attribution else ["unknown"], "microvms": lim_vms}})
        if role == "illustration":
            shots = ((tj.get("microvms") or [{}])[0].get("task") or {}).get("step_screenshots") or []
            frames = []
            for p in shots:
                step = next((s for s in R.STEPS if p.endswith(f"-{s}.jpg")), None)
                img = images.add(src.dir / p)
                if step and img:
                    frames.append({"step": step, "img": img})
            if frames:
                doc["filmstrip"] = {"microvm": 1, "frames": frames}
        docs.append(doc)
    if clamped:
        log(f"  {run_id}: unattributed host CPU clamped at 0 in {clamped} samples")

    # Rule 5, steady state: a density that kept the host full (its trials averaged at least 85% busy over the task
    # window) gives each of its trials with a known host CPU per task a steady-state cost.
    densities = spec["densities"]
    for dens in densities:
        at = [t for t in summaries if t["counts"] and t["cost_per_1000_tasks"] and t["density"] == dens]
        if R.host_full([t["cost_per_1000_tasks"]["host_busy_fraction"] for t in at]):
            for t in at:
                per_task = t["cost_per_1000_tasks"]["host_cpu_per_task_s"]
                if per_task is not None:
                    t["cost_per_1000_tasks"]["steady_state"] = r4(R.steady_state_per_1000(price, ncpu, per_task))

    # by density (rule 3), checked against the harness's per-density results
    briefs = R.density_briefs(densities, summaries)
    theirs = {int(x["density"]): x.get("passed") for x in src.report.get("densities") or []}
    by_density = []
    for b in briefs:
        dens = b["density"]
        at = sorted((t for t in summaries if t["counts"] and t["density"] == dens), key=lambda t: t["number"])
        row = dict(b)
        row["trial_results"] = [t["passed"] for t in at]
        row["trial_ids"] = [t["id"] for t in at]
        row["checks"] = ([{"subject": c["subject"], "stat": c["stat"], "target_ms": c["target_ms"],
                           "range": rng([t["checks"][k]["value_ms"] for t in at]),
                           "met": sum(1 for t in at if t["checks"][k]["met"])} for k, c in enumerate(at[0]["checks"])]
                         if at else [])
        hosts = [t["host"] for t in at if t["host"]]
        row["host"] = ({"cpu_util_pct": rng([h["cpu_util_pct"] for h in hosts]),
                        "cpu_pressure_pct": rng([h["cpu_pressure_pct"] for h in hosts]),
                        "busy_cores": rng([h["busy_cores"] for h in hosts]),
                        "mem_used_gib": rng([h["mem_used_gib"] for h in hosts])} if hosts else None)
        row["vcpus_allocated"] = dens * spec["microvm"]["vcpus"]
        row["mem_allocated_gib"] = r4(dens * (spec["microvm"]["memory_mib"] + overhead_mib) / 1024)
        row["verdicts"] = R.union_verdicts([t["attribution"]["verdicts"] for t in at if t["attribution"]]) if at else []
        if b["result"] == "passed" and all(t["cost_per_1000_tasks"] for t in at):
            costs = [t["cost_per_1000_tasks"] for t in at]
            row["cost_per_1000_tasks"] = {k: rng([c[k] for c in costs]) for k in ("execution", "observed", "observed_charged")}
            # The steady-state cost at a density needs every trial there to have one (the host full in each).
            row["cost_per_1000_tasks"]["steady_state"] = (rng([c["steady_state"] for c in costs])
                                                          if all(c["steady_state"] is not None for c in costs) else None)
        if dens in theirs and theirs[dens] is not None and bool(theirs[dens]) != (b["result"] == "passed"):
            raise BuildError(f"{run_id} density {dens}: recomputed {b['result']}, the harness says passed={theirs[dens]}")
        by_density.append(row)

    plan = run.get("plan") or {}
    # Stopped early: the run ended before its procedure finished (its shutdown timer, an interruption). A run the
    # harness stopped because a density couldn't be tested cleanly finished as the method says: that density and
    # those above it are not tested (D63), which by_density already shows.
    boundary = plan.get("boundary") or {}
    # An interruption after both ends of the boundary were tested in full cut only what comes after them (the
    # illustration, which measures nothing), so the measurement finished and the run didn't stop early (D63).
    needed = 1 + int(plan.get("boundary_trials") or 0)
    measured = (plan.get("stop_reason") == "interrupted" and plan.get("stop_at_first_miss") is True
                and boundary.get("first_miss") is not None and boundary.get("first_miss_trials", 0) >= needed
                and boundary.get("last_pass") is not None and boundary.get("last_pass_trials", 0) >= needed)
    stopped_early = plan.get("complete") is not True and plan.get("stop_reason") != "not_clean" and not measured
    core = R.result_core(briefs)
    tested, first_failed = core["tested_successfully"], core["first_failed"]
    if (plan.get("complete") is True or measured) and boundary and \
            (tested, first_failed) != (boundary.get("last_pass"), boundary.get("first_miss")):
        raise BuildError(f"{run_id}: result {tested}/{first_failed} differs from the run's plan {boundary}")
    at_t = next((b for b in by_density if b["density"] == tested), None)
    # Rule 10: full-size screenshots only for the trials a reader opens to see them; every other trial's are
    # thumbnails only, which keeps the dataset within its budget as campaigns accumulate.
    full = R.full_size_trials(summaries, tested, first_failed)
    for d in docs:
        d["full_size_screenshots"] = d["id"] in full
        if d["full_size_screenshots"]:
            for sha in [m["img"] for m in d["microvms"] if m.get("img")] + \
                       [fr["img"] for fr in (d.get("filmstrip") or {}).get("frames", [])]:
                images.full_size(sha)
    result = {**core,
              "per_host_vcpu": r4(tested / ncpu) if tested is not None else None,
              "midpoint_per_host_vcpu": r4(R.midpoint(tested, first_failed, ncpu)) if first_failed is not None else None,
              "cost_per_1000_tasks": (at_t or {}).get("cost_per_1000_tasks"),
              "limit": run_limit(by_density, summaries, rds, tested, first_failed)}

    entry = {"id": run_id, "spec": spec_name, "replica": replica, "stopped_early": stopped_early,
             "started": minute(t0), "duration_s": round(t_end - t0),
             "harness_commit": harness_commit(src, run_id, log), "host": host, "result": result,
             "by_density": [{k: b[k] for k in ("density", "result", "passed", "trials", "trial_results")}
                            for b in by_density]}
    has = {"boot_phases": any("boot" in lane for d in docs for lane in d["microvms"]),
           "per_microvm_cpu": any(d["series"].get("microvms") for d in docs),
           "guest_series": any(d["series"].get("guest") for d in docs),
           "attribution": any(s["attribution"] for s in summaries),
           "filmstrip": any("filmstrip" in d for d in docs)}
    run_doc = {"schema": SCHEMA, **entry, "campaign": campaign, "has": has, "by_density": by_density,
               "trials": summaries, "tasks": tasks, "steps": steps}
    return {"entry": entry, "doc": run_doc, "trials": docs, "summaries": summaries, "rules": rds, "source": src}


def missed(at_f: dict, trials: list[dict]) -> list[str]:
    """The criteria the trials at the first failing density missed, in plain words, most often missed first."""
    n = len(trials)
    of = lambda k: f" ({k} of {n} trial{'' if n == 1 else 's'})"  # noqa: E731
    out = []
    for c in sorted(at_f["checks"], key=lambda c: c["met"]):
        if c["met"] < n:
            what = "the whole task" if c["subject"] == "task" else f"the {STEP_LABEL[c['subject']]} step"
            stat = "median" if c["stat"] == "p50" else "p95"
            lo, hi = c["range"]
            vals = fmt_int(lo) if round(lo) == round(hi) else f"{fmt_int(lo)}–{fmt_int(hi)}"
            out.append(f"{what}'s {stat} took {vals} ms against {fmt_int(c['target_ms'])}{of(n - c['met'])}")
    for bad, words in ((lambda t: not t["ready"]["met"], "not every microVM was ready in time"),
                       (lambda t: t["tasks"]["ok"] < t["tasks"]["of"], "a task failed"),
                       (lambda t: not t["clean"], "something was left on the host")):
        k = sum(1 for t in trials if bad(t))
        if k:
            out.append(words + of(k))
    return out


def run_limit(by_density, summaries, rds, tested, first_failed):
    """What limited the run at the first failing density (DATA.md, RunLimit)."""
    if first_failed is None:
        return None
    at_f = next(b for b in by_density if b["density"] == first_failed)
    ft = [t for t in summaries if t["id"] in at_f["trial_ids"]]
    fa = [t for t in ft if t["attribution"]]
    limit = {"density": first_failed, "verdicts": at_f["verdicts"] or ["unknown"],
             "trials_with_verdict": sum(1 for t in fa if (at_f["verdicts"] or ["unknown"])[0] in t["attribution"]["verdicts"]),
             "trials": len(ft), "missed": missed(at_f, ft)}
    if tested is not None:
        at_t = next(b for b in by_density if b["density"] == tested)
        pt = [t for t in summaries if t["id"] in at_t["trial_ids"] and t["attribution"]]

        def values(ts, key):
            return [next(r["value"] for r in t["attribution"]["rules"] if r["key"] == key) for t in ts]

        sep = R.separating_rule(rds, {rd["key"]: values(pt, rd["key"]) for rd in rds},
                                {rd["key"]: values(fa, rd["key"]) for rd in rds})
        if sep:
            limit["separated_by"] = {"rule": sep["key"], "passing": rng(values(pt, sep["key"])),
                                     "failing": rng(values(fa, sep["key"])), "threshold": sep["threshold"]}
    if fa:
        sums = {k: sum(t["attribution"]["host_cpu_s"][k] or 0 for t in fa) for k in R.CONSUMERS}
        limit["host_consumer"] = max(R.CONSUMERS, key=lambda k: sums[k])
        shared = [t for t in fa if t["attribution"]["guest_share"]]
        if shared:
            shares = {k: sum(t["attribution"]["guest_share"][k] for t in shared) for k in R.GUEST}
            top = max(R.GUEST, key=lambda k: shares[k])
            limit["guest_group"] = top
            limit["guest_share"] = rng([t["attribution"]["guest_share"][top] for t in shared])
    return limit
