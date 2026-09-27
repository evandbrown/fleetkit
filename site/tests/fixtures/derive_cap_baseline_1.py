#!/usr/bin/env python3
"""One-off: derive the cap-baseline-1 fixture, a campaign of one, from results/cap-baseline-1, following site/DATA.md.

This is a snapshot for the site's tests, and a check that DATA.md's mapping from older run directories works on a
real run. The dataset builder supersedes it. results/ is gitignored, so this only runs where the results were
downloaded. Every output field is built explicitly (an allowlist): nothing from the run directory is copied
through, and no id, address, URL or error text is written.

    python3 site/tests/fixtures/derive_cap_baseline_1.py
    node site/tests/fixtures/make-synthetic.mjs      # then rebuild index.json
"""
from __future__ import annotations

import bisect
import collections
import csv
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUN_DIR = REPO / "results/cap-baseline-1/host"
SRC = RUN_DIR / "capacity"
OUT = HERE / "data/campaigns/cap-baseline-1"
SCHEMA = "fleetkit-site-data/1"
CAMPAIGN = "cap-baseline-1"
SPEC = "baseline"
RUN = "baseline-r1"

STEPS = ["home", "search", "open_product", "add_to_cart", "verify_cart"]
STEP_LABEL = {"home": "home", "search": "search", "open_product": "open product", "add_to_cart": "add to cart",
              "verify_cart": "verify cart", "task": "task"}
ROLE = {"ladder": "ladder", "confirm": "boundary", "warmup": "warmup", "illustration": "illustration", "fault": "fault"}
SEVERITY = ["host_cpu", "microvm_cpu_allowance", "host_memory", "io", "steal"]
VERDICT = {"host_cpu": "host_cpu", "vm_cpu_quota": "microvm_cpu_allowance", "host_memory": "host_memory", "io": "io",
           "steal": "steal", "none": "none", "unknown": "unknown"}
# old threshold key -> (new key, label, verdict, op, old signal key in _verdicts)
RULES = [
    ("host_psi_cpu_some_pct", "host_cpu_pressure_pct", "Host CPU pressure", "host_cpu", ">="),
    ("host_cpu_util_mean_pct", "host_cpu_util_pct", "Host CPU utilisation", "host_cpu", ">="),
    ("vm_throttled_fraction_mean", "microvm_throttled_fraction", "MicroVM throttled fraction", "microvm_cpu_allowance", ">="),
    ("host_mem_available_min_fraction", "host_mem_available_fraction", "Host memory available", "host_memory", "<"),
    ("host_psi_memory_some_pct", "host_mem_pressure_pct", "Host memory pressure", "host_memory", ">="),
    ("host_psi_io_some_pct", "host_io_pressure_pct", "Host IO pressure", "io", ">="),
    ("host_steal_mean_pct", "host_steal_pct", "Steal", "steal", ">="),
]
MISSING = {"psi_cpu_some_pct": "host_cpu_pressure_pct", "cpu_util_mean_pct": "host_cpu_util_pct",
           "vm_throttled_fraction_mean": "microvm_throttled_fraction",
           "mem_available_min_fraction": "host_mem_available_fraction", "psi_memory_some_pct": "host_mem_pressure_pct",
           "psi_io_some_pct": "host_io_pressure_pct", "steal_mean_pct": "host_steal_pct"}
GUEST_FOLD = {"renderer": "renderer", "browser": "browser", "gpu": "other_chromium", "network": "other_chromium",
              "utility": "other_chromium", "zygote": "other_chromium", "chromium_other": "other_chromium",
              "guestd": "guestd", "other": "other"}
GUEST = ["renderer", "browser", "other_chromium", "guestd", "other"]
CONSUMERS = ["microvm_vcpus", "hypervisor", "hostd", "driver", "unattributed"]
GIB = 2 ** 30
MIB = 2 ** 20


def r4(v):
    return None if v is None else round(float(v), 4)


def sig3(v):
    if v is None:
        return None
    v = float(v)
    return 0 if v == 0 else float(f"{v:.3g}")


def num(v):
    """1000.0 -> 1000; keeps real fractions."""
    f = float(v)
    return int(f) if f.is_integer() else f


def rng(values, digits=4):
    v = [x for x in values if x is not None]
    return [round(min(v), digits), round(max(v), digits)] if v else None


def minute(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def fmt_int(v):
    return f"{round(v):,}"


def sha8(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8] if path.exists() else None


def rows(name):
    with open(SRC / name, newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def env_file(path):
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


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


def main():
    run = json.loads((SRC / "run.json").read_text())
    report = json.loads((SRC / "report.json").read_text())
    manifest = json.loads((SRC / "manifest.json").read_text())
    env = env_file(RUN_DIR / "capacity.env")
    inp, opts = run["inputs"], run["inputs"]["options"]
    hi, gi = inp["host_info"], inp["guest_info"]
    price = float(env["PRICE_PER_HOUR"])
    t0 = run["started_ts"]

    # ---- the spec, reconstructed from the run's recorded inputs (DATA.md, "Mapping older run directories")
    thresholds = report["trials"][0]["attribution"]["thresholds"]
    rule_defs = [{"key": new, "label": label, "verdict": verdict, "op": op, "threshold": num(thresholds[old])}
                 for old, new, label, verdict, op in RULES]
    first_trial = json.loads(next((SRC / "trials").glob("*/trial.json")).read_text())
    spec = {
        "host": {"instance_type": env["INSTANCE_TYPE_EXPECTED"]},
        "hypervisor": {"name": run["backend"], "version": manifest["firecracker_version"]},
        "microvm": {"vcpus": num(opts["vcpus"]), "mem_mib": num(opts["mem_mib"]),
                    "mem_overhead_mib": num(hi["firecracker"]["mem_overhead_mib"])},
        "workload": {"task": "shop-5-steps", "products": num(first_trial["fixture"]["products"])},
        "procedure": {"densities": [num(x) for x in inp["ladder"]], "boundary_trials": num(inp["confirm_repeats"]),
                      "warmup_trials": num(inp["warmup"]), "settle_s": num(inp["settle_s"]),
                      "illustration": bool(inp["illustration"]), "stop_at_first_failure": bool(inp["stop_at_first_miss"]),
                      "release": "all_ready"},
        "criteria": {"step_p50_ms": num(run["criteria"]["step_p50_target_ms"]),
                     "step_p95_ms": num(run["criteria"]["step_p95_target_ms"]),
                     "task_p95_ms": num(run["criteria"]["task_p95_target_ms"]),
                     "ready_limit_s": num(opts["ready_timeout_s"])},
        "attribution": {r["key"]: r["threshold"] for r in rule_defs},
        "measurement": {"host_hz": num(inp["metrics_hz"]), "guest_interval_ms": num(inp["sample_interval_ms"])},
        "support": {"instance_type": env["SUPPORT_INSTANCE_TYPE"]},
    }
    host = {
        "instance_type": manifest["instance_type"],
        "host_kind": "metal" if ".metal" in manifest["instance_type"] else "nested",
        "vcpus": num(hi["cpu_count"]),
        "cores": num(hi["cores_per_socket"]) * num(hi["sockets"]),
        "threads_per_core": num(hi["threads_per_core"]),
        "sockets": num(hi["sockets"]),
        "cpu_model": hi["cpu_model"],
        "mem_gib": round(hi["mem_total"] / GIB, 1),
        "kernel_release": hi["kernel_release"],
        "hypervisor_version": hi["firecracker"]["version"],
        "chromium_version": gi["chromium_version"].removeprefix("Chrome/"),
    }

    # ---- tables from the CSVs
    sessions = collections.defaultdict(dict)  # old trial id -> slot -> row
    for r in rows("sessions.csv"):
        sessions[r["trial_id"]][int(r["slot"])] = r
    tasks_by = collections.defaultdict(dict)
    for r in rows("tasks.csv"):
        tasks_by[r["trial_id"]][int(r["slot"])] = r
    task_slot = {r["task_id"]: (r["trial_id"], int(r["slot"])) for t in tasks_by.values() for r in t.values()}
    steps_by = collections.defaultdict(list)
    for r in rows("steps.csv"):
        steps_by[r["task_id"]].append(r)

    host_series = collections.defaultdict(list)  # (subject, metric) -> [(ts, value)]
    for r in rows("host_metrics.csv"):
        host_series[(r["session_id"], r["metric"])].append((float(r["ts"]), float(r["value"])))
    for v in host_series.values():
        v.sort()
    guest_rows = collections.defaultdict(lambda: collections.defaultdict(dict))  # task -> ts -> metric -> value
    for r in rows("guest_metrics.csv"):
        guest_rows[r["task_id"]][float(r["ts"])][r["metric"]] = float(r["value"])

    report_trials = {t["trial_id"]: t for t in report["trials"]}
    trial_dirs = sorted(p for p in (SRC / "trials").iterdir() if (p / "trial.json").exists())

    # ---- trials, in execution order
    numbers, nth, idmap = collections.Counter(), collections.Counter(), {}
    summaries, docs = [], []
    tasks = {k: [] for k in ["trial", "microvm", "product", "ok", "task_ms", "wall_ms", "failed_step",
                             "failure_category", "timing_valid", "img"]}
    steps = {k: [] for k in ["trial", "microvm", "step", "duration_ms", "ok"]}
    for order, d in enumerate(trial_dirs, 1):
        tj = json.loads((d / "trial.json").read_text())
        old = tj["trial_id"]
        role = ROLE[tj["kind"]]
        density = int(tj["level_n"])
        counts = role in ("ladder", "boundary")
        if counts:
            numbers[density] += 1
            number, tid = numbers[density], f"d{density}-t{numbers[density]}"
        else:
            nth[role] += 1
            number, tid = None, role if nth[role] == 1 else f"{role}-{nth[role]}"
        idmap[old] = tid
        ts = tj["timestamps"]
        cs = ts["create_start"]
        rel = lambda x: None if x is None else round((x - cs) * 1000)  # noqa: E731
        marks = {"all_ready_ms": rel(ts.get("all_ready")), "release_ms": rel(ts.get("barrier_release")),
                 "last_return_ms": rel(ts.get("last_task_return")), "clean_ms": rel(ts.get("verify_clean_pass"))}
        marks["end_ms"] = (marks["clean_ms"] or marks["last_return_ms"] or 0) + 1000
        ev = tj["evaluation"]
        checks = []
        for c in ev["checks"]:
            parts = c["name"].split()
            subject, stat = (parts[1], parts[2]) if parts[0] == "step" else (parts[0], parts[1])
            checks.append({"subject": subject, "stat": stat, "value_ms": r4(c["value"]), "target_ms": num(c["target"]),
                           "met": bool(c["met"])})
        cnt = tj["counts"]
        ready = {"microvms": cnt["sessions_ready"], "all_ready_ms": marks["all_ready_ms"],
                 "limit_ms": num(opts["ready_timeout_s"]) * 1000,
                 "met": cnt["sessions_ready"] == cnt["sessions_requested"] and marks["all_ready_ms"] is not None}
        task_counts = {"ok": cnt["tasks_ok"], "of": density}
        clean = bool((tj.get("verify_clean") or {}).get("clean"))
        failed = []
        if not ready["met"]:
            failed.append(f"{ready['microvms']} of {density} microVMs ready")
        if task_counts["ok"] < task_counts["of"]:
            failed.append(f"{task_counts['of'] - task_counts['ok']} of {task_counts['of']} tasks failed")
        failed += [f"{STEP_LABEL[c['subject']]} {c['stat']} {fmt_int(c['value_ms'])} ms > {fmt_int(c['target_ms'])} ms"
                   for c in checks if not c["met"]]
        if not clean:
            failed.append("something was left on the host")
        passed = bool(ev["passed"]) if counts else None

        rt = report_trials[old]
        at = rt["attribution"]
        h = at["host"]
        signals = {"host_cpu_pressure_pct": h.get("psi_cpu_some_pct"), "host_cpu_util_pct": h.get("cpu_util_mean_pct"),
                   "microvm_throttled_fraction": at.get("vm_throttled_fraction_mean"),
                   "host_mem_available_fraction": h.get("mem_available_min_fraction"),
                   "host_mem_pressure_pct": h.get("psi_memory_some_pct"), "host_io_pressure_pct": h.get("psi_io_some_pct"),
                   "host_steal_pct": h.get("steal_mean_pct")}
        rules = []
        for rd in rule_defs:
            v = signals[rd["key"]]
            fired = v is not None and (v >= rd["threshold"] if rd["op"] == ">=" else v < rd["threshold"])
            rules.append({"key": rd["key"], "value": r4(v), "fired": fired})
        verdicts = [VERDICT[v] for v in at["verdicts"]]
        window_s = at["window"]["seconds"]
        g = at["guest"]
        busy_guest = (g["busy_cpu_s"] or 0) - (g["idle_cpu_s"] or 0)
        share = dict.fromkeys(GUEST, 0.0)
        for grp, x in g["groups"].items():
            share[GUEST_FOLD[grp]] += x["cpu_s"]
        share["other"] += busy_guest - sum(x["cpu_s"] for x in g["groups"].values())
        attribution = {
            "window_ms": [marks["release_ms"], marks["last_return_ms"]],
            "verdicts": verdicts,
            "rules": rules,
            "host_cpu_s": {"microvm_vcpus": r4(at["vcpu_s"]), "hypervisor": r4(at["vmm_s"]), "hostd": r4(at["hostd_cpu_s"]),
                           "driver": r4(at["driver_cpu_s"]), "unattributed": r4(at["unattributed_cpu_s"]),
                           "busy": r4(h["busy_cpu_s"])},
            "guest_share": {k: r4(v / busy_guest) for k, v in share.items()} if busy_guest > 0 else None,
            "missing": [MISSING[m] for m in at.get("missing", []) if m in MISSING],
        }
        by_slot = {s["session_id"]: s for s in tj["sessions"]}
        vm_attr = {s["session_id"]: s for s in at["sessions"]}
        cost = rt["cost_usd_per_task"]
        summary = {
            "id": tid, "density": density, "number": number, "role": role, "counts": counts, "order": order,
            "passed": passed,
            "at_limit": bool(counts and passed and verdicts[0] not in ("none", "unknown")),
            "failed": failed if counts else [],
            "ready": ready, "tasks": task_counts, "checks": checks, "clean": clean, "marks": marks,
            "attribution": attribution,
            "host": {"cpu_util_pct": r4(h["cpu_util_mean_pct"]), "cpu_pressure_pct": r4(h["psi_cpu_some_pct"]),
                     "busy_cores": r4(h["busy_cpu_s"] / window_s),
                     "mem_used_gib": r4((1 - h["mem_available_min_fraction"]) * h["mem_total"] / GIB),
                     "steal_pct": r4(h["steal_mean_pct"])},
            "microvm_mem_peak_mib": rng([s["cgroup_memory_peak_bytes"] / MIB for s in at["sessions"]]),
            "cost_per_1000_tasks": {"execution": r4(cost["execution_only"] * 1000), "observed": r4(cost["observed"] * 1000),
                                    "fixture": r4(cost["fixture_serving_estimate"] * 1000)} if counts else None,
        }
        if role == "warmup":
            summary["excluded_because"] = ("the first microVM after setup reads its root filesystem from disk; "
                                           "later ones read it from memory.")
        elif role == "illustration":
            summary["excluded_because"] = "screenshots after every step add time inside the task."
        summaries.append(summary)

        # tables and the trial document
        lanes, lim_vms, vm_series, guest_series = [], [], [], []
        for slot, srow in sorted(sessions[old].items()):
            idx = slot + 1
            trow = tasks_by[old].get(slot)
            tsess = next((s for s in tj["sessions"] if s["slot"] == slot), {})
            sid = srow["session_id"]
            f = lambda k: float(srow[k]) if srow.get(k) else None  # noqa: E731
            lane = {"index": idx, "product": trow["product_id"] if trow else "", "outcome": srow["outcome"],
                    "ready_ms": rel(f("ready_ts"))}
            if srow.get("kernel_start_ts"):
                lane["boot"] = {"process_started_ms": rel(f("process_started_ts")), "kernel_start_ms": rel(f("kernel_start_ts")),
                                "guestd_start_ms": rel(f("guestd_start_ts")), "chromium_launch_ms": rel(f("chromium_launch_ts")),
                                "chromium_ready_ms": rel(f("chromium_ready_ts"))}
            lane_steps = []
            if trow:
                ok = trow["ok"] == "true"
                task = tsess.get("task") or {}
                lane["task"] = {"dispatch_ms": rel(float(trow["dispatch_ts"])),
                                "return_ms": rel(task.get("return_ts") or float(trow["dispatch_ts"]) + float(trow["wall_ms"]) / 1000),
                                "task_ms": r4(float(trow["task_ms"])), "wall_ms": r4(float(trow["wall_ms"])), "ok": ok}
                if not ok:
                    lane["task"]["failed_step"] = trow["failed_step"] or None
                    lane["task"]["failure_category"] = trow["failure_category"].replace("session_", "microvm_")
                lane["task"].update({"bytes": int(trow["bytes_received"] or 0), "requests": int(trow["request_count"] or 0),
                                     "chromium_rss_mib": round(float(trow["chromium_rss"] or 0) / MIB),
                                     "clock_offset_ms": round(float(trow["clock_offset_ns"] or 0) / 1e6, 1),
                                     "timing_valid": trow["timing_valid"] == "true"})
                img = sha8(SRC / trow["screenshot_path"]) if trow.get("screenshot_path") else None
                if img:
                    lane["img"] = img
                tasks["trial"].append(tid)
                tasks["microvm"].append(idx)
                tasks["product"].append(trow["product_id"])
                tasks["ok"].append(ok)
                tasks["task_ms"].append(r4(float(trow["task_ms"])) if trow["task_ms"] else None)
                tasks["wall_ms"].append(r4(float(trow["wall_ms"])) if trow["wall_ms"] else None)
                tasks["failed_step"].append(trow["failed_step"] or None)
                tasks["failure_category"].append(None if trow["failure_category"] == "ok"
                                                 else trow["failure_category"].replace("session_", "microvm_"))
                tasks["timing_valid"].append(trow["timing_valid"] == "true")
                tasks["img"].append(img)
                for s in sorted(steps_by[trow["task_id"]], key=lambda x: int(x["step_index"])):
                    st = {"name": s["name"], "start_ms": rel(float(s["dispatch_ts"])), "end_ms": rel(float(s["settle_ts"])),
                          "ok": not s["error"]}
                    if s.get("bytes_received"):
                        st["bytes"] = int(s["bytes_received"])
                        st["requests"] = int(s["request_count"])
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
            va = vm_attr.get(sid)
            if va:
                lim_vms.append({"index": idx, "vcpu_s": r4(va["vcpu_s"]), "hypervisor_s": r4(va["vmm_s"]),
                                "throttled_fraction": r4(va["throttled_fraction"]),
                                "cpu_pressure_pct": r4(va["cgroup_cpu_pressure_some_pct"]),
                                "mem_peak_mib": r4(va["cgroup_memory_peak_bytes"] / MIB)})
            # per-microVM series: counters differenced into rates
            vs = {"index": idx, "t_ms": [], "vcpu_cores": [], "hypervisor_cores": [], "throttled_fraction": [], "mem_mib": []}
            vcpu, vmm = host_series[(sid, "cpu_vcpu_usec")], host_series[(sid, "cpu_vmm_usec")]
            thr, mem = host_series[(sid, "cpu_throttled_usec")], dict(host_series[(sid, "cgroup_memory_current")])
            for (ta, va_), (tb, vb) in zip(vcpu, vcpu[1:]):
                dtu = (tb - ta) * 1e6
                vs["t_ms"].append(rel(tb))
                vs["vcpu_cores"].append(sig3(max(0.0, (vb - va_) / dtu)))
                vs["hypervisor_cores"].append(sig3(max(0.0, (value_at(vmm, tb) - value_at(vmm, ta)) / dtu)))
                vs["throttled_fraction"].append(sig3(max(0.0, (value_at(thr, tb) - value_at(thr, ta)) / dtu)))
                vs["mem_mib"].append(sig3(mem.get(tb, value_at(host_series[(sid, "cgroup_memory_current")], tb)) / MIB))
            vm_series.append(vs)
            if trow:
                gs = {"index": idx, "t_ms": [], **{k: [] for k in GUEST}}
                interval = float(inp["sample_interval_ms"])
                for gts, m in sorted(guest_rows[trow["task_id"]].items()):
                    folded = dict.fromkeys(GUEST, 0.0)
                    for k, v in m.items():
                        if k.startswith("cpu_ms."):
                            folded[GUEST_FOLD[k[len("cpu_ms."):]]] += v
                    busy = m.get("cpu_total_ms", 0) - m.get("cpu_idle_ms", 0)
                    folded["other"] += max(0.0, busy - sum(v for k, v in m.items() if k.startswith("cpu_ms.")))
                    gs["t_ms"].append(rel(gts))
                    for k in GUEST:
                        gs[k].append(sig3(folded[k] / interval))
                guest_series.append(gs)

        window = [-int(num(inp["settle_s"])) * 1000, marks["end_ms"]]
        lo, hi_t = cs + window[0] / 1000, cs + window[1] / 1000
        hs = {"t_ms": [], "cpu_util_pct": [], "mem_used_gib": [], "cpu_pressure_pct": [], "mem_pressure_pct": [],
              "io_pressure_pct": [], "steal_pct": [], "cores": {k: [] for k in CONSUMERS}}
        util = host_series[("host", "cpu_util")]
        vm_ids = [r["session_id"] for r in sessions[old].values()]
        ncpu = host["vcpus"]
        mem_total = dict(host_series[("host", "mem_total")])
        mem_av = dict(host_series[("host", "mem_available")])
        steal = dict(host_series[("host", "steal")])
        for (ta, _), (tb, ub) in zip(util, util[1:]):
            if not lo <= tb <= hi_t:
                continue
            dtu = (tb - ta) * 1e6
            rate = lambda key: max(0.0, (value_at(host_series[key], tb) - value_at(host_series[key], ta)) / dtu)  # noqa: E731
            hs["t_ms"].append(rel(tb))
            hs["cpu_util_pct"].append(sig3(ub))
            hs["mem_used_gib"].append(sig3((mem_total[tb] - mem_av[tb]) / GIB))
            hs["cpu_pressure_pct"].append(sig3(100 * rate(("host", "psi_cpu_some_total"))))
            hs["mem_pressure_pct"].append(sig3(100 * rate(("host", "psi_memory_some_total"))))
            hs["io_pressure_pct"].append(sig3(100 * rate(("host", "psi_io_some_total"))))
            hs["steal_pct"].append(sig3(steal[tb]))
            vm = sum(rate((v, "cpu_vcpu_usec")) for v in vm_ids if host_series[(v, "cpu_vcpu_usec")])
            hyp = sum(rate((v, "cpu_vmm_usec")) for v in vm_ids if host_series[(v, "cpu_vmm_usec")])
            hostd, drv = rate(("host", "hostd_cpu_usec")), rate(("driver", "driver_cpu_usec"))
            hs["cores"]["microvm_vcpus"].append(sig3(vm))
            hs["cores"]["hypervisor"].append(sig3(hyp))
            hs["cores"]["hostd"].append(sig3(hostd))
            hs["cores"]["driver"].append(sig3(drv))
            hs["cores"]["unattributed"].append(sig3(max(0.0, ub / 100 * ncpu - vm - hyp - hostd - drv)))
        fixture = {"t_ms": [], "ms": []}
        for fts, v in host_series[("fixture", "fixture_rtt_ms")]:
            if lo <= fts <= hi_t:
                fixture["t_ms"].append(rel(fts))
                fixture["ms"].append(sig3(v))
        doc = {"schema": SCHEMA, "campaign": CAMPAIGN, "run": RUN, "id": tid, "density": density, "number": number,
               "role": role, "window_ms": window, "marks": marks,
               "settle": {"seconds": num(tj["pre_trial"]["settle_s"]), "cpu_util_mean_pct": r4(tj["pre_trial"]["cpu_util_mean"])},
               "microvms": lanes,
               "series": {"host": hs, "microvms": vm_series, "guest": guest_series, "fixture_rtt": fixture},
               "limit": {"verdicts": verdicts, "microvms": lim_vms}}
        if role == "illustration":
            shots = (tj["sessions"][0].get("task") or {}).get("step_screenshots") or []
            frames = []
            for p in shots:
                step = next(s for s in STEPS if p.endswith(f"-{s}.jpg"))
                frames.append({"step": step, "img": sha8(SRC / p)})
            doc["filmstrip"] = {"microvm": 1, "frames": frames}
        docs.append(doc)

    # ---- by density (rule 3) and the run's result (rule 4), checked against the old report
    densities = spec["procedure"]["densities"]
    by_density = []
    for dens in densities:
        at = sorted((t for t in summaries if t["counts"] and t["density"] == dens), key=lambda t: t["number"])
        passed = sum(1 for t in at if t["passed"])
        result = "not_run" if not at else "passed" if passed == len(at) else "failed"
        row = {"density": dens, "result": result, "passed": passed, "trials": len(at), "trial_ids": [t["id"] for t in at],
               "checks": [{"subject": c["subject"], "stat": c["stat"], "target_ms": c["target_ms"],
                           "range": rng([t["checks"][k]["value_ms"] for t in at]),
                           "met": sum(1 for t in at if t["checks"][k]["met"])} for k, c in enumerate(at[0]["checks"])] if at else [],
               "host": {"cpu_util_pct": rng([t["host"]["cpu_util_pct"] for t in at]),
                        "cpu_pressure_pct": rng([t["host"]["cpu_pressure_pct"] for t in at]),
                        "busy_cores": rng([t["host"]["busy_cores"] for t in at]),
                        "mem_used_gib": rng([t["host"]["mem_used_gib"] for t in at])} if at else None,
               "vcpus_allocated": dens * spec["microvm"]["vcpus"],
               "mem_allocated_gib": r4(dens * (spec["microvm"]["mem_mib"] + spec["microvm"]["mem_overhead_mib"]) / 1024),
               "verdicts": union([t["attribution"]["verdicts"] for t in at]) if at else []}
        if result == "passed":
            row["cost_per_1000_tasks"] = {k: rng([t["cost_per_1000_tasks"][k] for t in at])
                                          for k in ("execution", "observed", "fixture")}
        old_level = next((lv for lv in report["levels"] if lv["level_n"] == dens), None)
        if old_level is not None and old_level["passed"] != (result == "passed"):
            raise SystemExit(f"density {dens}: recomputed {result}, the old report says passed={old_level['passed']}")
        by_density.append(row)

    tested = None
    for b in by_density:
        if b["result"] != "passed":
            break
        tested = b["density"]
    failed_ds = [b["density"] for b in by_density if b["result"] == "failed"]
    first_failed = min(failed_ds) if failed_ds else None
    boundary = run["plan"]["boundary"]
    if (tested, first_failed) != (boundary["last_pass"], boundary["first_miss"]):
        raise SystemExit(f"result {tested}/{first_failed} differs from the old plan {boundary}")
    at_t = next(b for b in by_density if b["density"] == tested)
    at_f = next(b for b in by_density if b["density"] == first_failed)
    ft = [t for t in summaries if t["id"] in at_f["trial_ids"]]
    pt = [t for t in summaries if t["id"] in at_t["trial_ids"]]
    rv = lambda ts, key: [next(r["value"] for r in t["attribution"]["rules"] if r["key"] == key) for t in ts]  # noqa: E731
    sep = next((rd for rd in rule_defs if rd["op"] == ">=" and max(rv(pt, rd["key"])) < rd["threshold"] <= min(rv(ft, rd["key"]))), None)
    sums = {k: sum(t["attribution"]["host_cpu_s"][k] for t in ft) for k in CONSUMERS}
    shares = {k: sum(t["attribution"]["guest_share"][k] for t in ft) for k in GUEST}
    top = max(GUEST, key=lambda k: shares[k])
    limit = {"density": first_failed, "verdicts": at_f["verdicts"],
             "trials_with_verdict": sum(1 for t in ft if at_f["verdicts"][0] in t["attribution"]["verdicts"]),
             "trials": len(ft)}
    if sep:
        limit["separated_by"] = {"rule": sep["key"], "passing": rng(rv(pt, sep["key"])), "failing": rng(rv(ft, sep["key"])),
                                 "threshold": sep["threshold"]}
    limit.update({"host_consumer": max(CONSUMERS, key=lambda k: sums[k]), "guest_group": top,
                  "guest_share": rng([t["attribution"]["guest_share"][top] for t in ft])})
    result = {"tested_successfully": tested, "first_failed": first_failed,
              "not_tried": [tested + 1, first_failed - 1] if first_failed - tested > 1 else None,
              "not_run": [b["density"] for b in by_density if b["result"] == "not_run"],
              "per_host_vcpu": r4(tested / host["vcpus"]),
              "vcpus_allocated_per_host_vcpu": r4(tested * spec["microvm"]["vcpus"] / host["vcpus"]),
              "cost_per_1000_tasks": at_t["cost_per_1000_tasks"], "limit": limit}

    # ---- the run: overview at 1 Hz, support host strip
    buckets = collections.defaultdict(list)
    for ts_, v in host_series[("host", "cpu_util")]:
        if ts_ >= t0:
            buckets[int(ts_ - t0)].append(v)
    overview = {"t_s": sorted(buckets), "cpu_util_pct": [sig3(sum(buckets[s]) / len(buckets[s])) for s in sorted(buckets)],
                "bands": []}
    for d, doc in zip(trial_dirs, docs):
        cs = json.loads((d / "trial.json").read_text())["timestamps"]["create_start"]
        overview["bands"].append({"trial": doc["id"], "start_s": round(cs - t0, 1), "end_s": round(cs - t0 + doc["marks"]["end_ms"] / 1000, 1)})
    support = {"t_s": [], "cpu_util_pct": []}
    sup = RUN_DIR / "support/support-metrics.csv"
    if sup.exists():
        with open(sup, newline="") as fh:
            for r in csv.DictReader(fh):
                if r["metric"] == "cpu_util" and t0 <= float(r["ts"]) <= run["ended_ts"]:
                    support["t_s"].append(round(float(r["ts"]) - t0, 1))
                    support["cpu_util_pct"].append(sig3(float(r["value"])))

    commit = manifest["git_commit"]
    entry = {"id": RUN, "spec": SPEC, "replica": 1, "status": "complete", "started": minute(t0),
             "duration_s": round(run["ended_ts"] - t0), "host": host, "result": result,
             "by_density": [{k: b[k] for k in ("density", "result", "passed", "trials")} for b in by_density]}
    run_doc = {"schema": SCHEMA, **entry, "campaign": CAMPAIGN,
               "code": {"commit": commit.removesuffix("-dirty"), "dirty": commit.endswith("-dirty")},
               "has": {"host_hz": 5, "boot_phases": True, "per_microvm_cpu": True, "guest_series": True, "attribution": True,
                       "cost": True, "filmstrip": True, "fixture_rtt": True},
               "by_density": by_density, "trials": summaries, "tasks": tasks, "steps": steps, "overview": overview}
    if support["t_s"]:
        run_doc["support"] = support

    # ---- the campaign of one
    question = ("How many 2 vCPU / 2 GiB Firecracker microVMs can one m8i.4xlarge worker host run at once, "
                "with every shopping task inside its latency targets?")
    home = next(c for c in at_f["checks"] if c["subject"] == "home" and c["stat"] == "p50")
    src = f"campaigns/{CAMPAIGN}/runs/{RUN}.json"
    answer = [
        {"term": "density", "t": "Density"}, {"t": " "}, {"v": str(tested), "cls": "measured", "src": f"{src} › result.tested_successfully"},
        {"t": " was "}, {"term": "tested_successfully"}, {"t": f" on one {host['instance_type']} worker host ({host['vcpus']} vCPUs = "
         f"{host['cores']} cores × {host['threads_per_core']} threads, {host['host_kind']}): "},
        {"v": f"{at_t['passed']} of {at_t['trials']}", "cls": "measured", "src": f"{src} › by_density[{tested}]"}, {"t": " trials passed. At "},
        {"v": str(first_failed), "cls": "measured", "src": f"{src} › result.first_failed"},
        {"t": f", the home page's median missed its {fmt_int(home['target_ms'])} ms target in "},
        {"v": f"{at_f['trials'] - home['met']} of {at_f['trials']}", "cls": "measured", "src": f"{src} › by_density[{first_failed}].checks"},
        {"t": " trials ("}, {"v": f"{fmt_int(home['range'][0])}–{fmt_int(home['range'][1])} ms", "cls": "measured",
                            "src": f"{src} › by_density[{first_failed}].checks › home p50"},
        {"t": f"). Densities {result['not_tried'][0]} to {result['not_tried'][1]} were not tried."},
    ]
    if sep:
        answer += [{"t": " What separated them was host "}, {"term": "cpu_pressure", "t": "CPU pressure"}, {"t": ": "},
                   {"v": f"{limit['separated_by']['passing'][0]:.1f}–{limit['separated_by']['passing'][1]:.1f}%", "cls": "measured",
                    "src": f"{src} › result.limit.separated_by.passing"},
                   {"t": f" at {tested}, "},
                   {"v": f"{limit['separated_by']['failing'][0]:.1f}–{limit['separated_by']['failing'][1]:.1f}%", "cls": "measured",
                    "src": f"{src} › result.limit.separated_by.failing"},
                   {"t": f" at {first_failed}, against a rule of at least {sep['threshold']}%."}]
    spec_fields = json.loads((HERE / "spec-fields.json").read_text())
    outcome = {"spec": SPEC, "runs": [RUN], "tested_successfully": [tested], "per_host_vcpu": [result["per_host_vcpu"]],
               "cost_per_1000_tasks": [{"execution": result["cost_per_1000_tasks"]["execution"],
                                        "observed": result["cost_per_1000_tasks"]["observed"]}]}
    campaign = {
        "schema": SCHEMA, "id": CAMPAIGN, "title": "Capacity baseline", "question": question,
        "started": minute(t0), "ended": minute(run["ended_ts"]), "status": "complete", "before_campaigns": True,
        "provenance": "reconstructed",
        "preregistration": {"path": "docs/capacity-experiment.md", "commit": commit.removesuffix("-dirty")},
        "definition": {"format": "fleetkit-campaign/1", "id": CAMPAIGN, "question": question, "base": spec,
                       "specs": [{"name": SPEC, "label": "m8i.4xlarge, Firecracker, 2 vCPU / 2 GiB", "changes": {}}],
                       "replicas": 1},
        "spec_fields": spec_fields,
        "specs": [{"name": SPEC, "label": "m8i.4xlarge, Firecracker, 2 vCPU / 2 GiB", "changes": [], "spec": spec,
                   "instance_type": spec["host"]["instance_type"], "host_kind": host["host_kind"],
                   "hypervisor": spec["hypervisor"]["name"], "microvm": spec["microvm"],
                   "densities": densities, "boundary_trials": spec["procedure"]["boundary_trials"],
                   "criteria": spec["criteria"], "rules": rule_defs, "price_usd_per_hour": price}],
        "runs": [entry],
        "outcomes": [outcome],
        "answer": answer,
        "does_not_show": [
            [{"t": "Other hosts: this is nested virtualization on one m8i.4xlarge, and nothing here extrapolates to metal."}],
            [{"t": f"Densities {result['not_tried'][0]} to {result['not_tried'][1]}, which were not tried."}],
            [{"t": "A precise tail: over at most 12 tasks, "}, {"term": "p95"}, {"t": " is effectively the slowest task."}],
            [{"t": "Settled browsers: tasks started as soon as every microVM reported ready, while browsers may still "
                   "have been finishing startup."}],
        ],
        "next": [{"text": "Narrow the boundary.", "motivated_by": f"densities {result['not_tried'][0]} to {result['not_tried'][1]} were not tried",
                  "changes": {"procedure.densities": [1, 2, 4, 8, 9, 10, 11, 12]}}],
    }

    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "runs" / RUN).mkdir(parents=True)
    (OUT / "campaign.json").write_text(json.dumps(campaign, indent=2, ensure_ascii=False) + "\n")
    (OUT / "runs" / f"{RUN}.json").write_text(json.dumps(run_doc, separators=(",", ":"), ensure_ascii=False) + "\n")
    for doc in docs:
        (OUT / "runs" / RUN / f"{doc['id']}.json").write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"wrote {OUT.relative_to(REPO)}: {len(summaries)} trials; tested successfully {tested}, first failed {first_failed}")


def union(lists):
    seen = {v for lst in lists for v in lst}
    fired = [v for v in SEVERITY if v in seen]
    if fired:
        return fired
    return ["unknown"] if "unknown" in seen or not seen else ["none"]


if __name__ == "__main__":
    main()
