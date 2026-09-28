"""Failures outside the experiment (docs/method.md, "A failure outside the experiment is not a result").

A trial is clean unless something the spec doesn't test got in its way:

* the fixture didn't answer the check before the trial or before the tasks started;
* the host daemon failed (an error talking to it, no answer to a task request, or it restarted
  during the trial), or the driver itself raised;
* the support host reported its fixture unhealthy or itself overloaded during the trial,
  measured on the support host (experiments/launcher/support_health.py), not from the worker
  host, whose own load is what the experiment measures;
* a microVM's Chromium wasn't running the flags the spec says (base + extra flags, read back from
  its browser process), or its guest didn't boot with the spec's console (read back from its kernel
  command line): the harness didn't set up what the spec tests;
* Chromium in a microVM reported that the guest's network changed under a navigation
  (``net::ERR_NETWORK_CHANGED``): the guest's network setup moved, which load doesn't cause.
  Seen once in nested-hv-1 (Cloud Hypervisor, 230 ms into a home step). Other navigation
  errors, such as timeouts, stay results.

A trial that isn't clean is not a result: the driver sets it aside (``ops/set-aside/``) and runs
it again once. If that isn't clean either, the density is not tested and the run goes no higher.
Every cause goes in ``ops.jsonl`` and ``driver.log``, never in the results.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

# Transport errors that mean the host daemon itself didn't answer (it crashed or restarted),
# as opposed to a slow answer, which the worker host's own load can cause.
_NO_ANSWER = re.compile(r"refused|reset|aborted|broken pipe|remote end closed|no route|unreachable", re.I)


def outside_cause(doc: dict | None, *, harness_error: str | None = None, trial_s: float | None = None,
                  health_after: dict | None = None, health_error: str | None = None,
                  support: dict | None = None) -> str | None:
    """Why this trial isn't a result, or None when it's clean.

    ``doc`` is the trial's trial.json; ``health_after`` the host daemon's /health answer right
    after the trial (``health_error`` when it gave none), ``trial_s`` how long the trial took;
    ``support`` the support host's report for the trial's window (None when it isn't known)."""
    if harness_error:
        return f"harness: {harness_error}"
    d = doc if isinstance(doc, dict) else {}
    err = str(d.get("error") or "")
    if err.startswith("fixture check failed"):
        return f"fixture: {err}"
    if err.startswith(("host daemon:", "chromium flags:", "guest console:")):
        return err
    vc = d.get("verify_clean") if isinstance(d.get("verify_clean"), dict) else {}
    if vc.get("error"):
        return f"host daemon: verify-clean got no answer: {vc['error']}"
    for m in d.get("microvms") or []:
        task = (m or {}).get("task") or {}
        e = str(task.get("error") or "")
        if e.startswith("driver client:") and _NO_ANSWER.search(e):
            return f"host daemon: no answer to a task request ({e[len('driver client:'):].strip()})"
        if "net::ERR_NETWORK_CHANGED" in e:
            return f"guest network: changed under a navigation in microVM {m.get('microvm_id', '?')}"
    if health_error:
        return f"host daemon: no answer after the trial ({health_error})"
    up = (health_after or {}).get("uptime_s")
    if isinstance(up, (int, float)) and trial_s is not None and up < trial_s:
        return f"host daemon: restarted during the trial (up {up:.0f} s of a {trial_s:.0f} s trial)"
    problems = (support or {}).get("problems") or []
    if problems:
        return "support host: " + "; ".join(str(p) for p in problems)
    return None


class SupportHealth:
    """The support host's own account of a time window: GET <url>/window?from=<t0>&to=<t1>."""

    def __init__(self, url: str | None, timeout_s: float = 5.0):
        self.url = url.rstrip("/") if url else None
        self.timeout_s = timeout_s

    def window(self, t0: float, t1: float) -> dict:
        """{"problems": [...], ...}. Raises OSError or ValueError when the support host doesn't say."""
        q = urllib.parse.urlencode({"from": f"{t0:.3f}", "to": f"{t1:.3f}"})
        with urllib.request.urlopen(f"{self.url}/window?{q}", timeout=self.timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not isinstance(body, dict) or not isinstance(body.get("problems", []), list):
            raise ValueError(f"unexpected answer: {str(body)[:120]}")
        return body
