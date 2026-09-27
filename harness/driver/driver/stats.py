"""Percentiles and means used by trial.json and the report."""
from __future__ import annotations

import math


def percentile(values, p: float):
    """Linear interpolation between closest ranks (numpy's default); None for no data."""
    vals = sorted(float(v) for v in values if v is not None and v != "")
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def mean(values):
    vals = [float(v) for v in values if v is not None and v != ""]
    return sum(vals) / len(vals) if vals else None


def summary(values) -> dict:
    vals = [float(v) for v in values if v is not None and v != ""]
    return {"n": len(vals), "p50": percentile(vals, 50), "p95": percentile(vals, 95),
            "mean": mean(vals), "min": min(vals) if vals else None, "max": max(vals) if vals else None}


def to_float(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def to_int(v, default=None):
    f = to_float(v)
    return default if f is None else int(f)
