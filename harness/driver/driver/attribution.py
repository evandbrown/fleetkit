"""Per-trial attribution for ``driver report``: what ran short during the task window, and which
process used the CPU.

The window is the trial's task window, ``barrier_release`` to ``last_task_return``. Numbers are
**measured**, from host_metrics.csv (hostd's /host/metrics, the driver's own ``driver`` rows) and
guest_metrics.csv (the guests' proc_samples); the verdict is **rule-based, from measured signals**:

* gauges (cpu_util, steal, mem_available) are summarized over the samples inside the window;
* cumulative counters (PSI ``*_total`` µs, CPU µs of hostd, the driver and each microVM's vCPU and
  hypervisor threads, cgroup throttled µs and pressure) are read at both window edges by linear interpolation
  between the neighbouring samples, and their difference is divided by the window.

Verdicts, most severe first (several can hold): ``host_cpu`` (host PSI cpu some >= 20% of the window
or mean cpu_util >= 90%), ``vm_cpu_quota`` (mean per-microVM throttled fraction >= 10%), ``host_memory``
(min mem_available < 10% of mem_total, or memory PSI some >= 5%), ``io`` (io PSI some >= 10%),
``steal`` (mean steal >= 5%); else ``none``, or ``unknown`` when a signal a rule needs is missing
(the docker backend, a host without PSI, a run from before these fields existed).
"""
from __future__ import annotations

import bisect
import collections

from .stats import mean, to_float

NUMBERS_LABEL = "measured"
VERDICT_LABEL = "rule-based, from measured signals"
EDGE_CLAMP_S = 2.0  # a counter's value at a window edge may come from a sample at most this far away

THRESHOLDS = {
    "host_psi_cpu_some_pct": 20.0,
    "host_cpu_util_mean_pct": 90.0,
    "vm_throttled_fraction_mean": 0.10,
    "host_mem_available_min_fraction": 0.10,
    "host_psi_memory_some_pct": 5.0,
    "host_psi_io_some_pct": 10.0,
    "host_steal_mean_pct": 5.0,
}
VERDICTS = ("host_cpu", "vm_cpu_quota", "host_memory", "io", "steal")


# ---- loading ------------------------------------------------------------------------------------

def index_host_metrics(rows) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """host_metrics rows -> {(subject, metric): [(ts, value), ...] sorted by ts}. Pass rows from
    ``RunDir.iter_rows("host_metrics")`` so a run directory from before the glossary reads the same."""
    out: dict[tuple[str, str], list[tuple[float, float]]] = collections.defaultdict(list)
    for r in rows:
        ts, v = to_float(r.get("ts")), to_float(r.get("value"))
        if ts is None or v is None:
            continue
        out[(str(r.get("subject") or ""), str(r.get("metric") or ""))].append((ts, v))
    for series in out.values():
        series.sort()
    return dict(out)


def load_host_series(rundir) -> dict:
    return index_host_metrics(rundir.iter_rows("host_metrics"))


def sum_guest_metrics(rows) -> dict[str, dict[str, float]]:
    """guest_metrics rows -> {trial_id: {"cpu_total_ms": Σ, "cpu_idle_ms": Σ, "cpu_ms.<group>": Σ, "samples": n}}."""
    out: dict[str, dict[str, float]] = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in rows:
        m = str(r.get("metric") or "")
        if m not in ("cpu_total_ms", "cpu_idle_ms") and not m.startswith("cpu_ms."):
            continue
        v = to_float(r.get("value"))
        if v is None:
            continue
        agg = out[str(r.get("trial_id") or "")]
        agg[m] += v
        if m == "cpu_total_ms":
            agg["samples"] += 1
    return {k: dict(v) for k, v in out.items()}


def load_guest_sums(rundir) -> dict:
    return sum_guest_metrics(rundir.iter_rows("guest_metrics"))


# ---- series helpers -------------------------------------------------------------------------------

def value_at(series, t: float, clamp_s: float = EDGE_CLAMP_S):
    """Linear interpolation of a cumulative series at t; the nearest sample if t lies outside the
    series by at most clamp_s; None otherwise."""
    if not series:
        return None
    times = [ts for ts, _ in series]
    i = bisect.bisect_right(times, t)
    if i == 0:
        return series[0][1] if series[0][0] - t <= clamp_s else None
    if i == len(series):
        return series[-1][1] if t - series[-1][0] <= clamp_s else None
    (t0, v0), (t1, v1) = series[i - 1], series[i]
    if t1 == t0:
        return v1
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def delta(series, start: float, end: float, clamp_s: float = EDGE_CLAMP_S):
    a, b = value_at(series, start, clamp_s), value_at(series, end, clamp_s)
    if a is None or b is None or b < a:  # missing, or the counter reset (daemon restart)
        return None
    return b - a


def gauge_values(series, start: float, end: float, clamp_s: float = EDGE_CLAMP_S) -> list[float]:
    """Values sampled inside [start, end]; for a window shorter than the sampling period, the first
    sample after it (within clamp_s)."""
    vals = [v for ts, v in (series or []) if start <= ts <= end]
    if not vals:
        after = [v for ts, v in (series or []) if end < ts <= end + clamp_s]
        vals = after[:1]
    return vals


def _last(series):
    return series[-1][1] if series else None


def _pct(num, window_s):
    return None if num is None or not window_s else 100.0 * num / (window_s * 1e6)


# ---- attribution ----------------------------------------------------------------------------------

def attribute_trial(trial: dict, series: dict, guest: dict | None = None, microvm_ids=None,
                    cpu_count=None, mem_total=None) -> dict:
    """Attribution over one trial's task window; every number is null when its input is missing."""
    ts = trial.get("timestamps") or {}
    start, end = to_float(ts.get("barrier_release")), to_float(ts.get("last_task_return"))
    out = {"labels": {"numbers": NUMBERS_LABEL, "verdict": VERDICT_LABEL}, "thresholds": dict(THRESHOLDS)}
    if start is None or end is None or end <= start:
        out.update({"window": {"start_ts": start, "end_ts": end, "seconds": None}, "host": {}, "microvms": [],
                    "guest": _guest(guest), "verdicts": ["unknown"], "verdict": "unknown",
                    "missing": ["task window"]})
        return out
    w = end - start
    series = series or {}

    def s(subject, metric):
        return series.get((subject, metric)) or []

    cpu = gauge_values(s("host", "cpu_util"), start, end)
    steal = gauge_values(s("host", "steal"), start, end)
    mem_av = gauge_values(s("host", "mem_available"), start, end)
    n_cpu = to_float(_last(s("host", "cpu_count"))) or to_float(cpu_count)
    m_total = to_float(_last(s("host", "mem_total"))) or to_float(mem_total)
    host = {
        "cpu_util_mean_pct": mean(cpu), "cpu_util_max_pct": max(cpu) if cpu else None,
        "steal_mean_pct": mean(steal), "steal_max_pct": max(steal) if steal else None,
        "psi_cpu_some_pct": _pct(delta(s("host", "psi_cpu_some_total"), start, end), w),
        "psi_memory_some_pct": _pct(delta(s("host", "psi_memory_some_total"), start, end), w),
        "psi_io_some_pct": _pct(delta(s("host", "psi_io_some_total"), start, end), w),
        "mem_available_min": min(mem_av) if mem_av else None, "mem_total": m_total,
        "cpu_count": n_cpu, "samples": len(cpu),
    }
    host["mem_available_min_fraction"] = (host["mem_available_min"] / m_total
                                          if host["mem_available_min"] is not None and m_total else None)
    host["busy_cpu_s"] = (host["cpu_util_mean_pct"] / 100.0 * n_cpu * w
                          if host["cpu_util_mean_pct"] is not None and n_cpu else None)

    if microvm_ids is None:
        microvm_ids = [x.get("microvm_id") for x in trial.get("microvms") or [] if x.get("ready")]
    microvms = []
    for mid in [x for x in microvm_ids if x]:
        vcpu = delta(s(mid, "cpu_vcpu_usec"), start, end)
        hyp = delta(s(mid, "cpu_hypervisor_usec"), start, end)
        thr = delta(s(mid, "cpu_throttled_usec"), start, end)
        peak = gauge_values(s(mid, "cgroup_memory_peak"), start, end) or \
            gauge_values(s(mid, "cgroup_memory_current"), start, end)
        microvms.append({
            "microvm_id": mid,
            "vcpu_s": vcpu / 1e6 if vcpu is not None else None,
            "hypervisor_s": hyp / 1e6 if hyp is not None else None,
            "cpu_s": (vcpu + hyp) / 1e6 if vcpu is not None and hyp is not None else None,
            "throttled_fraction": thr / (w * 1e6) if thr is not None else None,
            "cgroup_cpu_pressure_some_pct": _pct(delta(s(mid, "cpu_pressure_some_total_us"), start, end), w),
            "cgroup_memory_peak_bytes": max(peak) if peak else None,
        })
    thr_vals = [x["throttled_fraction"] for x in microvms if x["throttled_fraction"] is not None]
    hostd = delta(s("host", "hostd_cpu_usec"), start, end)
    hostd_s = hostd / 1e6 if hostd is not None else None
    drv = delta(s("driver", "driver_cpu_usec"), start, end)
    drv_s = drv / 1e6 if drv is not None else None
    cpu_s = [x["cpu_s"] for x in microvms]
    microvms_cpu = sum(cpu_s) if microvms and all(v is not None for v in cpu_s) else None
    parts = (host["busy_cpu_s"], microvms_cpu, hostd_s, drv_s)
    out.update({
        "window": {"start_ts": start, "end_ts": end, "seconds": w},
        "host": host,
        "microvms": microvms,
        "vm_throttled_fraction_mean": mean(thr_vals) if thr_vals and len(thr_vals) == len(microvms) else None,
        "vcpu_s": sum(x["vcpu_s"] for x in microvms) if microvms and all(x["vcpu_s"] is not None for x in microvms) else None,
        "hypervisor_s": (sum(x["hypervisor_s"] for x in microvms)
                         if microvms and all(x["hypervisor_s"] is not None for x in microvms) else None),
        "microvms_cpu_s": microvms_cpu,
        "hostd_cpu_s": hostd_s,
        "driver_cpu_s": drv_s,
        "guest": _guest(guest),
    })
    # busy host CPU that no microVM, hostd or the driver accounts for: the kernel, other daemons, the
    # fixture or collector if they share the host
    out["unattributed_cpu_s"] = (parts[0] - parts[1] - parts[2] - parts[3]
                                 if all(p is not None for p in parts) else None)
    verdicts, missing = _verdicts(host, out["vm_throttled_fraction_mean"])
    out["verdicts"] = verdicts
    out["verdict"] = verdicts[0]
    out["missing"] = missing
    return out


def _guest(g: dict | None) -> dict:
    g = g or {}
    busy = g.get("cpu_total_ms")
    groups = {}
    for k, v in g.items():
        if k.startswith("cpu_ms."):
            groups[k[len("cpu_ms."):]] = {"cpu_s": v / 1000.0, "share": (v / busy) if busy else None}
    top = max(groups, key=lambda k: groups[k]["cpu_s"]) if groups else None
    return {
        "busy_cpu_s": busy / 1000.0 if busy is not None else None,
        "idle_cpu_s": g["cpu_idle_ms"] / 1000.0 if g.get("cpu_idle_ms") is not None else None,
        "samples": int(g.get("samples") or 0),
        "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1]["cpu_s"])),
        "top_group": top,
        "top_group_share": groups[top]["share"] if top else None,
    }


def _verdicts(host: dict, thr_mean) -> tuple[list[str], list[str]]:
    T = THRESHOLDS
    sig = {
        "psi_cpu_some_pct": host.get("psi_cpu_some_pct"), "cpu_util_mean_pct": host.get("cpu_util_mean_pct"),
        "vm_throttled_fraction_mean": thr_mean, "mem_available_min_fraction": host.get("mem_available_min_fraction"),
        "psi_memory_some_pct": host.get("psi_memory_some_pct"), "psi_io_some_pct": host.get("psi_io_some_pct"),
        "steal_mean_pct": host.get("steal_mean_pct"),
    }

    def ge(k, t):
        return sig[k] is not None and sig[k] >= t

    fired = []
    if ge("psi_cpu_some_pct", T["host_psi_cpu_some_pct"]) or ge("cpu_util_mean_pct", T["host_cpu_util_mean_pct"]):
        fired.append("host_cpu")
    if ge("vm_throttled_fraction_mean", T["vm_throttled_fraction_mean"]):
        fired.append("vm_cpu_quota")
    if ((sig["mem_available_min_fraction"] is not None
         and sig["mem_available_min_fraction"] < T["host_mem_available_min_fraction"])
            or ge("psi_memory_some_pct", T["host_psi_memory_some_pct"])):
        fired.append("host_memory")
    if ge("psi_io_some_pct", T["host_psi_io_some_pct"]):
        fired.append("io")
    if ge("steal_mean_pct", T["host_steal_mean_pct"]):
        fired.append("steal")
    missing = [k for k, v in sig.items() if v is None]
    if fired:
        return fired, missing
    return (["unknown"] if missing else ["none"]), missing


def summarize_verdicts(attributions) -> list[str]:
    """Union of the trials' verdicts in severity order (for a density's row)."""
    seen = {v for a in attributions for v in (a.get("verdicts") or [])}
    ordered = [v for v in VERDICTS if v in seen]
    if ordered:
        return ordered
    return ["unknown"] if "unknown" in seen or not seen else ["none"]
