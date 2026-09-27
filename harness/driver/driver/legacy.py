"""Reading run directories written before the glossary (D56). The one alias map.

Run directories from before the glossary use other words for the same things: a trial's density
was its ``level_n``, a microVM was a ``session``, the hypervisor was the ``vmm``, and trials were
numbered across the whole run (``t007-firecracker-n8-r2``) instead of within their density
(``d8-t2``, "trial 2 at density 8"). Existing evidence is never rewritten, so every reader
translates on read instead: driver/outputs.py ``RunDir`` routes the trial, run, CSV, smoke and
case readers through this module, which is what ``driver report``, the bundle, the attribution
and the evidence readers see. The explorer's dataset builder imports ``ALIASES`` and the
translators from here too.

Old words appear in this module because it names them; nothing else in the driver should.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from . import schemas

# ---- the alias map: old name -> new name (None: dropped) -------------------------------------------

ALIASES: dict = {
    "files": {"sessions.csv": "microvms.csv"},
    # hostd's --log-dir: <log-dir>/sessions/<id>/console.log -> <log-dir>/microvms/<id>/console.log
    "directories": {"sessions": "microvms"},
    "csv_columns": {
        "tasks.csv": {"level_n": "density", "repeat": "trial_number", "session_id": "microvm_id",
                      "kind": "trial_kind"},
        "microvms.csv": {"session_id": "microvm_id"},
        "guest_metrics.csv": {"session_id": "microvm_id"},
        "host_metrics.csv": {"session_id": "subject"},
    },
    "values": {
        "trial_kind": {"confirm": "boundary"},
        "failure_category": {"session_not_ready": "microvm_not_ready"},
        "metric": {"cpu_vmm_usec": "cpu_hypervisor_usec"},
    },
    "trial_json": {
        "level_n": "density", "repeat": "trial_number", "kind": "trial_kind",
        # before commit a9c4d07 level_passed meant "the protocol held", never a pass: it becomes
        # protocol_ok when that field is absent, and ``passed`` comes only from evaluation.passed
        "level_passed": None, "actual_concurrency": None,
        "sessions": "microvms", "sessions[].session_id": "microvms[].microvm_id",
        "counts.sessions_requested": "counts.microvms_requested",
        "counts.sessions_created": "counts.microvms_created",
        "counts.sessions_ready": "counts.microvms_ready",
        "counts.sessions_failed_startup": "counts.microvms_failed_startup",
        "counts.sessions_by_outcome": "counts.microvms_by_outcome",
        "percentiles.*.n": "percentiles.*.count",
    },
    "run_json": {
        "inputs": "spec", "inputs.host_info": "observed.host_info", "inputs.guest_info": "observed.guest_info",
        "inputs.ladder": "spec.densities", "inputs.repeats": "spec.trials_per_density",
        "inputs.confirm_repeats": "spec.boundary_trials",
        "plan.ladder": "plan.densities", "plan.repeats": "plan.trials_per_density",
        "plan.confirm_repeats": "plan.boundary_trials",
        "plan.levels_run": "plan.densities_run", "plan.levels_run[].level": "plan.densities_run[].density",
        "plan.levels_run[].ladder_trials": "plan.densities_run[].ladder_trial_count",
        "plan.levels_run[].confirm_trials": "plan.densities_run[].boundary_trial_count",
        "plan.levels_not_run": "plan.densities_not_run",
        "plan.confirmations": "plan.boundary_checks", "plan.confirmations[].level": "plan.boundary_checks[].density",
    },
    # argparse destinations recorded in run.json spec.options
    "options": {"n": "densities", "levels": "densities", "repeats": "trials_per_density",
                "confirm_repeats": "boundary_trials", "max_runs": "max_rounds"},
    "smoke_state_json": {"runs": "rounds"},
    "case_json": {"session_id": "microvm_id", "session": "microvm", "spec": "create_request"},
    "events": {"session.state": "microvm.state"},
    "log_attributes": {"session_id": "microvm_id", "fleetkit.session_id": "fleetkit.microvm_id",
                       "fleetkit.level_n": "fleetkit.density", "fleetkit.repeat": "fleetkit.trial_number"},
    "trial_ids": {
        "t<seq>-<backend>-n<N>-r<k>": "d<N>-t<k>, numbered from 1 within the density in execution order",
        "t<seq>-smoke-n<N>": "d<N>-t<k>, trial_kind smoke",
        "t<seq>-warmup-n1-r<k>": "warmup (then warmup-2, warmup-3)",
        "t<seq>-illustration-n1": "illustration",
        "t<seq>-fault-<name>": "fault-<name>",
        "t<seq>-case-<name>": "case-<name>",
        "<seq>": "sequence",
    },
}

_TASK_COLS = ALIASES["csv_columns"]["tasks.csv"]
_KIND = ALIASES["values"]["trial_kind"]
_CATEGORY = ALIASES["values"]["failure_category"]
_METRIC = ALIASES["values"]["metric"]
_COUNTS = {k.split(".", 1)[1]: v.split(".", 1)[1] for k, v in ALIASES["trial_json"].items()
           if k.startswith("counts.")}
_OPTIONS = ALIASES["options"]
EVENT_ALIASES = ALIASES["events"]
LOG_ATTRIBUTE_ALIASES = ALIASES["log_attributes"]
LEGACY_MICROVMS_CSV = "sessions.csv"
LEGACY_MICROVM_LOG_DIR = "sessions"

# t005-firecracker-n8-r1, t001-warmup-n1-r1, t011-illustration-n1, t003-smoke-n2, t004-fault-hang_step
LEGACY_TRIAL_ID = re.compile(r"^t(\d{3,})-(.+)$")
_NUMBERED = re.compile(r"^(?:[a-z]+)-n(\d+)-r(\d+)$")
_SMOKE = re.compile(r"^smoke-n(\d+)$")
_LABELLED = re.compile(r"^(warmup|illustration)(?:-n\d+)?(?:-r\d+)?$")


# ---- detection ------------------------------------------------------------------------------------

def _header(path: Path) -> list[str]:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return next(csv.reader(fh), None) or []
    except OSError:
        return []


def is_legacy_dir(root: Path) -> bool:
    """True for a run directory written before the glossary: an old CSV name or column, or a run-wide
    trial id (``t001-...``) under trials/."""
    root = Path(root)
    if (root / LEGACY_MICROVMS_CSV).exists():
        return True
    if set(_header(root / "tasks.csv")) & set(_TASK_COLS):
        return True
    if "session_id" in _header(root / "host_metrics.csv") or "session_id" in _header(root / "guest_metrics.csv"):
        return True
    trials = root / "trials"
    if trials.is_dir():
        return any(p.is_dir() and LEGACY_TRIAL_ID.match(p.name) for p in trials.iterdir())
    return False


# ---- trial ids ------------------------------------------------------------------------------------

def _legacy_kind(old_id: str, doc: dict | None, is_case: bool) -> str:
    if is_case:
        return "case"
    d = doc or {}
    k = d.get("kind")
    k = _KIND.get(k, k)
    if k in schemas.TRIAL_KINDS:
        return k
    rest = LEGACY_TRIAL_ID.match(old_id).group(2)
    if d.get("fault") or rest.startswith("fault-"):
        return "fault"
    if _SMOKE.match(rest):
        return "smoke"
    m = _LABELLED.match(rest)
    if m:
        return m.group(1)
    return "ladder"


def trial_id_map(entries) -> dict[str, dict]:
    """``entries``: ``[(old_trial_id, trial.json or case.json doc or None, is_case)]`` for one run
    directory. -> {old id: {trial_id, sequence, density, trial_number, trial_kind}}.

    Counting trials (ladder, boundary, smoke) are numbered from 1 within their density in execution
    order, which is the old ``r<k>`` for a single invocation (cap-baseline-1: t005, t007, t008 are
    d8-t1, d8-t2, d8-t3). Warm-up, illustration, fault and case trials keep a label; a label used
    twice gets ``-2``, ``-3``."""
    parsed = []
    for old_id, doc, is_case in entries:
        m = LEGACY_TRIAL_ID.match(str(old_id))
        if not m:
            continue
        parsed.append((int(m.group(1)), old_id, m.group(2), doc if isinstance(doc, dict) else {}, is_case))
    parsed.sort()
    numbers: dict[int, int] = {}
    labels: dict[str, int] = {}
    out: dict[str, dict] = {}
    for seq, old_id, rest, doc, is_case in parsed:
        kind = _legacy_kind(old_id, doc, is_case)
        density = _density_of(rest, doc)
        number = None
        if kind in schemas.COUNTED_TRIAL_KINDS and density is not None:
            numbers[density] = numbers.get(density, 0) + 1
            number = numbers[density]
            new_id = f"d{density}-t{number}"
        else:
            if kind == "fault":
                name = schemas.fault_name(doc.get("fault"))
                if not name and rest.startswith("fault-"):
                    name = rest[len("fault-"):]
                label = f"fault-{name}" if name else "fault"
            elif kind in ("warmup", "illustration"):
                label = kind
            else:
                label = rest
            labels[label] = labels.get(label, 0) + 1
            new_id = label if labels[label] == 1 else f"{label}-{labels[label]}"
        out[old_id] = {"trial_id": new_id, "sequence": seq, "density": density, "trial_number": number,
                       "trial_kind": "case" if is_case else kind}
    return out


def _density_of(rest: str, doc: dict) -> int | None:
    v = doc.get("level_n")
    try:
        if v is not None and v != "":
            return int(float(v))
    except (TypeError, ValueError):
        pass
    for rx in (_NUMBERED, _SMOKE):
        m = rx.match(rest)
        if m:
            return int(m.group(1))
    m = re.search(r"-n(\d+)(?:-|$)", rest)
    return int(m.group(1)) if m else None


def swap_prefix(value, old: str, new: str):
    """``t005-firecracker-n8-r1-slot003`` -> ``d8-t1-slot003`` (task ids start with their trial id)."""
    if isinstance(value, str) and old and value.startswith(old + "-"):
        return new + value[len(old):]
    return value


# ---- trial.json -----------------------------------------------------------------------------------

def translate_trial(doc: dict, ident: dict) -> dict:
    t = dict(doc)
    old_id = str(t.get("trial_id") or "")
    new_id = ident["trial_id"]
    t["legacy_trial_id"] = old_id
    t["trial_id"] = new_id
    t["sequence"] = ident["sequence"]
    t.pop("level_n", None)
    t.pop("repeat", None)
    t.pop("kind", None)
    t["density"] = ident["density"]
    t["trial_number"] = ident["trial_number"]
    t["trial_kind"] = ident["trial_kind"]
    old_flag = t.pop("level_passed", None)
    if "protocol_ok" not in t:
        t["protocol_ok"] = old_flag
    t.pop("actual_concurrency", None)
    ev = t.get("evaluation")
    t["passed"] = bool(ev.get("passed")) if isinstance(ev, dict) else None
    counts = t.get("counts")
    if isinstance(counts, dict):
        c = {_COUNTS.get(k, k): v for k, v in counts.items()}
        if isinstance(c.get("tasks_by_failure_category"), dict):
            c["tasks_by_failure_category"] = {_CATEGORY.get(k, k): v for k, v in c["tasks_by_failure_category"].items()}
        t["counts"] = c
    pct = t.get("percentiles")
    if isinstance(pct, dict):
        t["percentiles"] = _count_key(pct)
    old_vms = t.pop("sessions", None)
    if isinstance(old_vms, list):
        vms = []
        for s in old_vms:
            if not isinstance(s, dict):
                continue
            v = {("microvm_id" if k == "session_id" else k): val for k, val in s.items()}
            task = v.get("task")
            if isinstance(task, dict):
                task = dict(task)
                task["task_id"] = swap_prefix(task.get("task_id"), old_id, new_id)
                task["failure_category"] = _CATEGORY.get(task.get("failure_category"), task.get("failure_category"))
                v["task"] = task
            vms.append(v)
        t["microvms"] = vms
    return t


def _count_key(obj):
    """percentiles: ``{"n": 3, "p50": ...}`` -> ``{"count": 3, ...}`` at every depth."""
    if isinstance(obj, dict):
        return {("count" if k == "n" else k): _count_key(v) for k, v in obj.items()}
    return obj


# ---- CSV rows -------------------------------------------------------------------------------------

def translate_row(table: str, row: dict, ids: dict[str, dict]) -> dict:
    """One CSV row of ``table`` (tasks, steps, microvms, host_metrics, guest_metrics) in the new names."""
    if table == "host_metrics":
        r = {("subject" if k == "session_id" else k): v for k, v in row.items()}
        r["metric"] = _METRIC.get(r.get("metric"), r.get("metric"))
        return r
    r = {}
    for k, v in row.items():
        if table == "tasks":
            k = _TASK_COLS.get(k, k)
        elif k == "session_id":
            k = "microvm_id"
        r[k] = v
    old = r.get("trial_id") or ""
    ident = ids.get(old)
    if ident:
        r["trial_id"] = ident["trial_id"]
        if "task_id" in r:
            r["task_id"] = swap_prefix(r["task_id"], old, ident["trial_id"])
        if table == "tasks":
            r["density"] = str(ident["density"]) if ident["density"] is not None else r.get("density", "")
            r["trial_number"] = "" if ident["trial_number"] is None else str(ident["trial_number"])
            r["trial_kind"] = ident["trial_kind"]
    if table == "tasks":
        r["failure_category"] = _CATEGORY.get(r.get("failure_category"), r.get("failure_category"))
        if r.get("trial_kind"):
            r["trial_kind"] = _KIND.get(r["trial_kind"], r["trial_kind"])
    return r


# ---- run.json, smoke-state.json, case.json --------------------------------------------------------

def translate_options(options: dict) -> dict:
    return {_OPTIONS.get(k, k): v for k, v in (options or {}).items()}


def translate_run_json(doc: dict, ids: dict[str, dict]) -> dict:
    r = dict(doc)
    inputs = r.pop("inputs", None)
    if isinstance(inputs, dict) and "spec" not in r:
        spec = {}
        for k, v in inputs.items():
            if k in ("host_info", "guest_info"):
                continue
            key = ALIASES["run_json"].get(f"inputs.{k}", f"spec.{k}").split(".", 1)[1]
            spec[key] = translate_options(v) if k == "options" and isinstance(v, dict) else v
        r["spec"] = spec
        r["observed"] = {"host_info": inputs.get("host_info"), "guest_info": inputs.get("guest_info")}
    if isinstance(r.get("plan"), dict):
        r["plan"] = translate_plan(r["plan"], ids)
    return r


def translate_plan(plan: dict, ids: dict[str, dict]) -> dict:
    def tid(x):
        return ids[x]["trial_id"] if x in ids else x

    p = {}
    for k, v in plan.items():
        if k == "ladder":
            p["densities"] = v
        elif k == "repeats":
            p["trials_per_density"] = v
        elif k == "confirm_repeats":
            p["boundary_trials"] = v
        elif k == "levels_run":
            p["densities_run"] = [{
                "density": e.get("level"), "trials": [tid(x) for x in e.get("trials") or []],
                "ladder_trial_count": e.get("ladder_trials"), "boundary_trial_count": e.get("confirm_trials"),
                "ladder_passed": e.get("ladder_passed"), "passed": e.get("passed"),
            } for e in v or [] if isinstance(e, dict)]
        elif k == "levels_not_run":
            p["densities_not_run"] = v
        elif k == "confirmations":
            p["boundary_checks"] = [{"density": e.get("level"), "trials": [tid(x) for x in e.get("trials") or []],
                                     "passed": e.get("passed")} for e in v or [] if isinstance(e, dict)]
        else:
            p[k] = v
    return p


def translate_smoke_state(doc: dict) -> dict:
    s = dict(doc)
    if "runs" in s and "rounds" not in s:
        s["rounds"] = s.pop("runs")
    return s


def translate_case(doc: dict, ident: dict | None) -> dict:
    c = {ALIASES["case_json"].get(k, k): v for k, v in doc.items()}
    if ident:
        c["legacy_trial_id"] = c.get("trial_id")
        c["trial_id"] = ident["trial_id"]
        c["sequence"] = ident["sequence"]
    return c


# ---- log records ----------------------------------------------------------------------------------

def event_name(name) -> str:
    """``session.state`` -> ``microvm.state``; new names unchanged."""
    return EVENT_ALIASES.get(name, name) if isinstance(name, str) else name


def log_attributes(attrs: dict) -> dict:
    """Attributes of an old log record in the new names (a new name already present wins)."""
    out = dict(attrs or {})
    for old, new in LOG_ATTRIBUTE_ALIASES.items():
        if old in out and new not in out:
            out[new] = out[old]
    return out


def dump_aliases() -> str:
    """The alias map as JSON, for readers outside Python."""
    return json.dumps(ALIASES, indent=2, sort_keys=True)
