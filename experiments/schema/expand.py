#!/usr/bin/env python3
"""Expand a campaign definition into its runs, check it, and estimate what it costs.

    python3 expand.py CAMPAIGN.json             the runs, what differs between specs, cost, waves under the quota
    python3 expand.py CAMPAIGN.json --json      the same as JSON, with every run's resolved spec
    python3 expand.py CAMPAIGN.json --run RUN   one run's resolved spec as JSON (RUN is <spec>-r<k>): what the driver reads
    python3 expand.py CAMPAIGN.json --quota N   waves under a vCPU quota other than limits.json's

Exit status: 0 valid, 1 not valid, 2 usage error. Errors and warnings go to stderr, each starting
with the field it is about. Standard library only; the schemas, instance-types.json, limits.json and
chromium-flags.json are read from this script's directory. tests/cases.json holds the cases this script and the site's
builder must both pass.
"""
from __future__ import annotations

import copy
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


class InputError(Exception):
    pass


# ------------------------------------------------------------------ reading JSON
def _no_repeated_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise InputError(f"the key {k!r} appears twice in one object")
        out[k] = v
    return out


def _no_constants(name):
    raise InputError(f"{name} is not a JSON number")


def _number(text: str):
    """A JSON number with a fraction or exponent. A whole one (2.0, 1e3) becomes an int, as it does in the
    site's builder, where JSON has one number type: "replicas": 2.0 is 2 replicas, never range(2.0)."""
    x = float(text)
    return int(x) if x.is_integer() else x


def whole_numbers(v):
    """v with every whole-number float made an int, as parse_json reads it, for a campaign built in memory."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        return [whole_numbers(x) for x in v]
    if isinstance(v, dict):
        return {k: whole_numbers(x) for k, x in v.items()}
    return v


def parse_json(text: str):
    return json.loads(text, object_pairs_hook=_no_repeated_keys, parse_constant=_no_constants,
                      parse_float=_number)


def read_json(path):
    return parse_json(Path(path).read_text(encoding="utf-8"))


SCHEMAS = {n: read_json(HERE / n) for n in ("spec.schema.json", "campaign.schema.json")}
TYPES = read_json(HERE / "instance-types.json")["types"]
LIMITS = read_json(HERE / "limits.json")
FLAGS = read_json(HERE / "chromium-flags.json")


# ------------------------------------------------------------------ small helpers
def fmt(v) -> str:
    """A value in a message, written the same way the site writes it: [1, 2], true, 1000 (not 1000.0)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {fmt(x)}" for k, x in v.items()) + "}"
    return str(v)


def same(a, b) -> bool:
    """JSON equality: 2 and 2.0 are the same number, true is not 1, key order doesn't matter."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def join(path: str, key) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else key


def flatten(obj: dict, prefix: str = "") -> dict:
    """{"a": {"b": 1}, "c": [1, 2]} -> {"a.b": 1, "c": [1, 2]}; lists are values."""
    out = {}
    for k, v in obj.items():
        if isinstance(v, dict):
            out.update(flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def optional_defaults() -> dict:
    """{path: default} for the spec fields a spec may leave out, which then stand at their default:
    procedure.release_after_ready_s and workload.chromium_extra_flags."""
    s = SCHEMAS["spec.schema.json"]
    out = {}
    for k, node in s["properties"].items():
        d = s["$defs"][k]
        if d.get("type") != "object":
            if k not in s["required"] and "default" in d:
                out[k] = d["default"]
            continue
        req = set(node.get("required", [])) if k in s["required"] else set()
        for f, fs in d["properties"].items():
            if f not in req and "default" in fs:
                out[f"{k}.{f}"] = fs["default"]
    return out


OPTIONAL = optional_defaults()


def filled(spec: dict) -> dict:
    """A spec's leaves, flattened, with every optional field it leaves out at its default (after the rest):
    what it runs."""
    out = flatten(spec)
    for k, v in OPTIONAL.items():
        out.setdefault(k, copy.deepcopy(v))
    return out


def merge(base: dict, changes: dict) -> dict:
    """A named spec: the base with its changes merged in. Objects merge key by key; lists and values replace."""
    out = copy.deepcopy(base)
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def usd(x: float) -> str:
    return f"${x:,.2f}"


# ------------------------------------------------------------------ schema validation
# The subset of JSON Schema 2020-12 the two schemas use, plus x-message: when a subschema that
# carries one fails, its errors are replaced by that one message. Messages start with the field.
KIND = {"object": "an object", "array": "a list", "string": "text", "boolean": "true or false",
        "number": "a number", "integer": "a whole number"}
TYPE_OK = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer(),
}


def resolve(ref: str, doc: str):
    name, _, frag = ref.partition("#")
    doc = name or doc
    node = SCHEMAS[doc]
    for part in [p for p in frag.split("/") if p]:
        node = node[part]
    return node, doc


def ecma_pattern(pattern: str) -> str:
    """A schema pattern (ECMA-262) for Python's re: there, $ also matches before a final newline, so
    "abc\\n" would pass ^[a-z]+$. \\Z is the end of the text only, as $ is in the site's builder."""
    return re.sub(r"(?<!\\)\$", r"\\Z", pattern)


def validate(inst, schema, path: str = "", doc: str = "spec.schema.json") -> list:
    errs: list = []
    _v(inst, schema, path, doc, errs)
    return errs


def _v(inst, s, path, doc, errs):
    if "x-message" in s:
        sub: list = []
        _check(inst, s, path, doc, sub)
        if sub:
            errs.append(f"{path or '(top)'}: {s['x-message']}")
        return
    _check(inst, s, path, doc, errs)


def _check(inst, s, path, doc, errs):
    where = path or "(top)"
    if "$ref" in s:
        sub, d = resolve(s["$ref"], doc)
        _v(inst, sub, path, d, errs)
    for sub in s.get("allOf", []):
        _v(inst, sub, path, doc, errs)
    if "not" in s and not validate(inst, s["not"], path, doc):
        errs.append(f"{where}: not allowed")
    if "if" in s and "then" in s and not validate(inst, s["if"], path, doc):
        _v(inst, s["then"], path, doc, errs)
    t = s.get("type")
    if t and not TYPE_OK[t](inst):
        errs.append(f"{where}: must be {KIND[t]}")
        return
    if "const" in s and not same(inst, s["const"]):
        errs.append(f"{where}: must be {fmt(s['const'])}")
    if "enum" in s and not any(same(inst, e) for e in s["enum"]):
        errs.append(f"{where}: must be one of {', '.join(fmt(e) for e in s['enum'])}")
    if TYPE_OK["number"](inst):
        if "minimum" in s and inst < s["minimum"]:
            errs.append(f"{where}: must be at least {fmt(s['minimum'])}")
        if "maximum" in s and inst > s["maximum"]:
            errs.append(f"{where}: must be at most {fmt(s['maximum'])}")
        if "exclusiveMinimum" in s and inst <= s["exclusiveMinimum"]:
            errs.append(f"{where}: must be more than {fmt(s['exclusiveMinimum'])}")
        if "multipleOf" in s and inst % s["multipleOf"] != 0:
            errs.append(f"{where}: must be a multiple of {fmt(s['multipleOf'])}")
    if isinstance(inst, str):
        if "minLength" in s and len(inst) < s["minLength"]:
            errs.append(f"{where}: must be at least {s['minLength']} characters")
        if "maxLength" in s and len(inst) > s["maxLength"]:
            errs.append(f"{where}: must be at most {s['maxLength']} characters")
        if "pattern" in s and not re.search(ecma_pattern(s["pattern"]), inst):
            errs.append(f"{where}: must match {s['pattern']}")
    if isinstance(inst, list):
        if "minItems" in s and len(inst) < s["minItems"]:
            errs.append(f"{where}: needs at least {s['minItems']} {'value' if s['minItems'] == 1 else 'values'}")
        if "maxItems" in s and len(inst) > s["maxItems"]:
            errs.append(f"{where}: allows at most {s['maxItems']} values")
        if "items" in s:
            for i, item in enumerate(inst):
                _v(item, s["items"], join(path, i), doc, errs)
    if isinstance(inst, dict):
        for k in s.get("required", []):
            if k not in inst:
                errs.append(f"{join(path, k)}: required")
        if "minProperties" in s and len(inst) < s["minProperties"]:
            errs.append(f"{where}: needs at least {s['minProperties']}")
        if "maxProperties" in s and len(inst) > s["maxProperties"]:
            errs.append(f"{where}: allows at most {s['maxProperties']}")
        props = s.get("properties", {})
        extra = s.get("additionalProperties", True)
        for k, v in inst.items():
            if "propertyNames" in s:
                _v(k, s["propertyNames"], join(path, k), doc, errs)
            if k in props:
                _v(v, props[k], join(path, k), doc, errs)
            elif extra is False:
                errs.append(f"{join(path, k)}: not a field")
            elif isinstance(extra, dict):
                _v(v, extra, join(path, k), doc, errs)


# ------------------------------------------------------------------ extra Chromium flags
def flag_name(flag: str) -> str:
    """--name of --name=value."""
    return flag.split("=", 1)[0]


def chromium_argv(extra) -> list:
    """What Chromium runs with after its binary: the base flags, the spec's extra flags, the start page."""
    return list(FLAGS["base"]) + list(extra) + [FLAGS["start_page"]]


def _value_problems(name: str, has_value: bool, value: str, rule: dict) -> list:
    """Why ``value`` isn't what the flag ``name`` takes under ``rule`` ({value: none | integer}); empty when it is.
    A number is written one way only (no leading zeros), so =2 and =02 can't pass as two different specs."""
    if rule["value"] == "none":
        return [f"{name} takes no value"] if has_value else []
    lo, hi = rule["minimum"], rule["maximum"]
    if not has_value or not re.fullmatch(r"0|[1-9][0-9]*", value) or not lo <= int(value) <= hi:
        return [f"{name} takes a whole number from {lo} to {hi}, as {name}=N"]
    return []


def flag_problems(flag: str) -> list:
    """Why one extra Chromium flag isn't allowed (chromium-flags.json); empty when it is."""
    name, eq, value = flag.partition("=")
    if name in {flag_name(b) for b in FLAGS["base"]}:
        return [f"{name} is one of the base flags every run has"]
    if name in FLAGS["refused"]:
        return [f"{name} is refused: {FLAGS['refused'][name]}"]
    rule = FLAGS["allowed"].get(name)
    if rule is None:
        return [f"{name} is not on the allowed list in chromium-flags.json"]
    if rule["value"] == "features":
        if not eq or not value:
            return [f"{name} takes features, comma-separated, as {name}=BackForwardCache"]
        out, seen = [], set()
        for feat in value.split(","):
            if feat not in rule["features"]:
                out.append(f"{name}: {feat or 'an empty name'} is not an allowed feature "
                           f"(allowed: {', '.join(rule['features'])})")
            elif feat in seen:
                out.append(f"{name}: {feat} appears twice")
            seen.add(feat)
        return out
    if rule["value"] == "v8":
        if not eq or not value:
            return [f"{name} takes V8 flags, space-separated, as {name}=--jitless"]
        out, seen = [], set()
        for tok in value.split(" "):
            vname, veq, vval = tok.partition("=")
            vrule = rule["v8"].get(vname)
            if vrule is None:
                out.append(f"{name}: {tok or 'an empty flag'} is not an allowed V8 flag "
                           f"(allowed: {', '.join(rule['v8'])})")
                continue
            out += [f"{name}: {p}" for p in _value_problems(vname, bool(veq), vval, vrule)]
            if vname in seen:
                out.append(f"{name}: {vname} appears twice")
            seen.add(vname)
        return out
    return _value_problems(name, bool(eq), value, rule)


def flags_problems(flags: list) -> list:
    """Errors for workload.chromium_extra_flags, each starting with its field."""
    errs, seen = [], set()
    for i, flag in enumerate(flags):
        where = f"workload.chromium_extra_flags[{i}]"
        errs += [f"{where}: {p}" for p in flag_problems(flag)]
        name = flag_name(flag)
        if name in seen:
            errs.append(f"{where}: {name} appears twice; give each flag once")
        seen.add(name)
    total, most = sum(len(f) for f in flags), FLAGS["max_total_chars"]
    if total > most:
        errs.append(f"workload.chromium_extra_flags: the flags add up to {total} characters, more than {most}")
    return errs


# ------------------------------------------------------------------ rules the schema can't state
def check_spec(spec: dict) -> tuple[list, list]:
    """Errors and warnings for one complete, schema-valid spec; paths are relative to the spec."""
    errs, warns = [], []
    d = spec["densities"]
    if any(b <= a for a, b in zip(d, d[1:])):
        errs.append(f"densities: must be in increasing order, each density once, got {fmt(d)}")
    c = spec["criteria"]
    if c["step_p50_target_ms"] > c["step_p95_target_ms"]:
        errs.append("criteria.step_p50_target_ms: must be at most criteria.step_p95_target_ms")
    if c["step_p95_target_ms"] >= c["step_timeout_ms"]:
        errs.append("criteria.step_p95_target_ms: must be below criteria.step_timeout_ms")
    if c["step_timeout_ms"] > c["task_timeout_ms"]:
        errs.append("criteria.step_timeout_ms: must be at most criteria.task_timeout_ms")
    if c["task_p95_target_ms"] >= c["task_timeout_ms"]:
        errs.append("criteria.task_p95_target_ms: must be below criteria.task_timeout_ms")
    errs += flags_problems(spec.get("workload", {}).get("chromium_extra_flags", []))
    host = TYPES[spec["worker_host"]["instance_type"]]
    top = max(d)
    gib = top * spec["microvm"]["memory_mib"] / 1024
    if gib >= host["memory_gib"]:
        warns.append(f"densities: at density {top} the microVMs' memory adds up to {fmt(gib)} GiB against the worker "
                     f"host's {host['memory_gib']} GiB, so memory may run out before CPU")
    return errs, warns


def host_kind(instance_type: str) -> str:
    return "metal" if TYPES[instance_type]["metal"] else "nested"


def run_minutes(spec: dict) -> int:
    """Expected length of a run that tests every density: setup, the trials, then upload and teardown.
    Every trial, labelled ones too, waits settle_s before it and release_after_ready_s (absent: 0) inside it."""
    t = LIMITS["run_time_estimate"]
    p, d = spec["procedure"], spec["densities"]
    wait_s = p.get("release_after_ready_s", 0)

    def trial_s(n):
        return p["settle_s"] + wait_s + t["trial_base_s"] + t["trial_s_per_microvm"] * n

    trials_s = (t["labelled_trials"] * trial_s(1) + p["trials_per_density"] * sum(trial_s(n) for n in d)
                + p["boundary_trials"] * sum(trial_s(n) for n in d[-2:]))
    kind = host_kind(spec["worker_host"]["instance_type"])
    return math.ceil(t["setup_minutes"][kind] + t["finish_minutes"] + trials_s / 60)


def pack_waves(runs: list, quota: int) -> list:
    """First-fit decreasing: groups of runs whose vCPUs fit under the quota together."""
    waves: list = []
    for r in sorted(runs, key=lambda r: -r["vcpus"]):
        if r["vcpus"] > quota:
            continue
        for w in waves:
            if w["vcpus"] + r["vcpus"] <= quota:
                w["runs"].append(r["run"])
                w["vcpus"] += r["vcpus"]
                break
        else:
            waves.append({"runs": [r["run"]], "vcpus": r["vcpus"]})
    return waves


def differing(specs: list) -> list:
    """[{field, values: {spec: value}}] for every field whose value isn't the same in every spec. An optional
    field a spec leaves out is at its default."""
    flat = [(name, filled(spec)) for name, spec in specs]
    keys = list(dict.fromkeys(k for _, f in flat for k in f))
    out = []
    for k in keys:
        vals = [f.get(k) for _, f in flat]
        if any(not same(vals[0], v) for v in vals[1:]):
            out.append({"field": k, "values": {name: f.get(k) for name, f in flat}})
    return out


# ------------------------------------------------------------------ a campaign
def evaluate(camp, quota: int | None = None) -> dict:
    """{valid, errors, warnings, specs: [(name, spec)], plan}. plan is None unless the campaign is valid."""
    quota = LIMITS["vcpu_quota"] if quota is None else quota
    camp = whole_numbers(camp)
    out = {"valid": False, "errors": [], "warnings": [], "specs": [], "plan": None}
    errs, warns = out["errors"], out["warnings"]
    errs += validate(camp, SCHEMAS["campaign.schema.json"], "", "campaign.schema.json")
    if errs:
        return out
    base = camp["base"]
    base_errs, _ = check_spec(base)
    errs += [f"base.{e}" for e in base_errs]
    specs = []
    for name, changes in camp["specs"].items():
        spec = merge(base, changes)
        se = validate(spec, SCHEMAS["spec.schema.json"])
        errs += [f"specs.{name}.{e}" for e in se]
        if not se:
            re_, rw = check_spec(spec)
            errs += [f"specs.{name}.{e}" for e in re_ if e not in base_errs]
            warns += [f"specs.{name}.{w}" for w in rw]
        specs.append((name, spec))
    for k in camp.get("why", {}):
        if k not in camp["specs"]:
            errs.append(f"why.{k}: no spec named {k}")
    for i, (a, sa) in enumerate(specs):
        for b, sb in specs[i + 1:]:
            if same(filled(sa), filled(sb)):
                errs.append(f"specs.{b}: expands to the same spec as {a}; to run a spec again, raise replicas")
    if errs:
        return out
    out["specs"] = specs

    name, reps, timer = camp["name"], camp["replicas"], camp["shutdown_after_minutes"]
    runs = []
    for s, spec in specs:
        worker, support = spec["worker_host"]["instance_type"], spec["support_host"]["instance_type"]
        minutes = run_minutes(spec)
        for k in range(1, reps + 1):
            runs.append({
                "run": f"{s}-r{k}", "path": f"results/{name}/{s}-r{k}", "spec_name": s, "replica": k,
                "worker_instance_type": worker, "support_instance_type": support, "host_kind": host_kind(worker),
                "vcpus": TYPES[worker]["vcpus"] + TYPES[support]["vcpus"],
                "usd_per_hour": TYPES[worker]["usd_per_hour"] + TYPES[support]["usd_per_hour"],
                "minutes_expected": minutes,
            })
    expected = round(sum(r["usd_per_hour"] * r["minutes_expected"] / 60 for r in runs), 2)
    worst = round(sum(r["usd_per_hour"] * timer / 60 for r in runs), 2)
    limit = LIMITS["campaign_worst_case_usd"]
    if worst > limit:
        errs.append(f"shutdown_after_minutes: the worst case is {usd(worst)}, above the {usd(limit)} limit for one "
                    "campaign; shorten the timer, or run fewer specs or replicas")
    for s, spec in specs:
        m = run_minutes(spec)
        if m > timer:
            errs.append(f"shutdown_after_minutes: a run of {s} is expected to take about {m} minutes, longer than the "
                        f"{timer}-minute timer")
    if reps == 1 and len(specs) > 1:
        warns.append("replicas: with 1 replica per spec there is no spread between hosts to judge a difference against")
    too_big = {}
    for r in runs:
        if r["vcpus"] > quota:
            too_big.setdefault(r["spec_name"], r["vcpus"])
    for s, n in too_big.items():
        warns.append(f"specs.{s}.worker_host.instance_type: a run needs {n} vCPUs with its support host, more than "
                     f"the quota of {quota}; it can't launch until the quota grows")
    if errs:
        return out
    waves = pack_waves(runs, quota)
    minutes = {r["run"]: r["minutes_expected"] for r in runs}
    out["valid"] = True
    out["plan"] = {
        "campaign": name,
        "question": camp["question"],
        "replicas": reps,
        "shutdown_after_minutes": timer,
        "runs": [{**r, "spec": spec} for r in runs for s, spec in specs if s == r["spec_name"]],
        "differs": differing(specs),
        "expected_usd": expected,
        "worst_case_usd": worst,
        "prices_estimated": any(TYPES[r[k]]["estimated"] for r in runs
                                for k in ("worker_instance_type", "support_instance_type")),
        "vcpu_quota": quota,
        "vcpus_all_at_once": sum(r["vcpus"] for r in runs),
        "waves": [w["runs"] for w in waves],
        "wave_vcpus": [w["vcpus"] for w in waves],
        "too_big_for_quota": [r["run"] for r in runs if r["vcpus"] > quota],
        "minutes_in_waves": sum(max(minutes[x] for x in w["runs"]) for w in waves),
    }
    return out


# ------------------------------------------------------------------ the text report
def table(header: list, rows: list) -> str:
    cells = [header] + rows
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]

    def line(r):
        return "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()

    return "\n".join(["  " + line(header), "  " + line(["-" * w for w in widths])] + ["  " + line(r) for r in rows])


def report(camp: dict, r: dict) -> str:
    p, specs = r["plan"], r["specs"]
    runs = p["runs"]
    out = [f"{p['campaign']}: {len(specs)} {'spec' if len(specs) == 1 else 'specs'} x {p['replicas']} "
           f"{'replica' if p['replicas'] == 1 else 'replicas'} = {len(runs)} runs",
           f"Question: {p['question']}", "", "Specs"]
    why = camp.get("why", {})
    for s, spec in specs:
        it = spec["worker_host"]["instance_type"]
        t = TYPES[it]
        ids = ", ".join(x["run"] for x in runs if x["spec_name"] == s)
        out.append(f"  {s}: {it} ({host_kind(it)}, {t['vcpus']} vCPUs, {t['memory_gib']} GiB), "
                   f"{spec['hypervisor']['name']}; runs {ids}")
        if s in why:
            out.append(f"    why: {why[s]}")
    out += ["", "What differs between specs"]
    rows = [[d["field"]] + [fmt(d["values"][s]) for s, _ in specs] for d in p["differs"]]
    vcpus = [TYPES[spec["worker_host"]["instance_type"]]["vcpus"] for _, spec in specs]
    if len(set(vcpus)) > 1:
        rows.append(["densities per host vCPU"] + [
            ", ".join(f"{round(n / v, 4):g}" for n in spec["densities"]) for (_, spec), v in zip(specs, vcpus)])
    out.append(table(["field"] + [s for s, _ in specs], rows) if rows else "  nothing (one spec)")
    mins = sorted({x["minutes_expected"] for x in runs})
    span = f"{mins[0]}" if len(mins) == 1 else f"{mins[0]} to {mins[-1]}"
    out += ["", f"Cost: expected {usd(p['expected_usd'])} (runs of about {span} minutes); worst case "
                f"{usd(p['worst_case_usd'])} if every host runs until its {p['shutdown_after_minutes']}-minute "
                f"shutdown. Limit {usd(LIMITS['campaign_worst_case_usd'])} per campaign; project cap "
                f"{usd(LIMITS['project_cap_usd'])}.{' Some prices are estimates.' if p['prices_estimated'] else ''}"]
    waves, q = p["waves"], p["vcpu_quota"]
    most = max((len(w) for w in waves), default=0)
    line = f"Quota: all {len(runs)} runs at once need {p['vcpus_all_at_once']} vCPUs; the quota is {q}"
    if not waves:
        line += ", and no run fits under it."
    elif len(waves) == 1 and not p["too_big_for_quota"]:
        line += ", so they all run at once."
    else:
        line += (f", so at most {most} run at once, in {len(waves)} {'wave' if len(waves) == 1 else 'waves'} "
                 f"(about {p['minutes_in_waves']} minutes).")
    out.append(line)
    if len(waves) > 1 or p["too_big_for_quota"]:
        for i, (w, v) in enumerate(zip(waves, p["wave_vcpus"]), 1):
            out.append(f"  wave {i} ({v} vCPUs): {', '.join(w)}")
        if p["too_big_for_quota"]:
            out.append(f"  too big for the quota: {', '.join(p['too_big_for_quota'])}")
    out += ["", f"Results: results/{p['campaign']}/campaign.json, and one directory per run: "
                f"results/{p['campaign']}/<run>/."]
    return "\n".join(out)


# ------------------------------------------------------------------ command line
def main(argv: list) -> int:
    pos, opts, flags = [], {}, set()
    it = iter(argv)
    for a in it:
        if a in ("--run", "--quota"):
            opts[a] = next(it, None)
            if opts[a] is None:
                print(__doc__.strip(), file=sys.stderr)
                return 2
        elif a == "--json":
            flags.add(a)
        elif a.startswith("-"):
            print(__doc__.strip(), file=sys.stderr)
            return 2
        else:
            pos.append(a)
    if len(pos) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        quota = int(opts["--quota"]) if "--quota" in opts else None
    except ValueError:
        print("--quota takes a whole number of vCPUs", file=sys.stderr)
        return 2
    try:
        camp = read_json(pos[0])
    except (OSError, ValueError, InputError) as exc:
        print(f"error: {pos[0]}: {exc}", file=sys.stderr)
        return 1
    r = evaluate(camp, quota)
    if r["valid"] and "--run" in opts:
        run = next((x for x in r["plan"]["runs"] if x["run"] == opts["--run"]), None)
        if run is None:
            print(f"error: no run {opts['--run']} in campaign {camp['name']}", file=sys.stderr)
            return 1
        print(json.dumps({"campaign": camp["name"], "run": run["run"], "spec_name": run["spec_name"],
                          "replica": run["replica"], "shutdown_after_minutes": camp["shutdown_after_minutes"],
                          "spec": run["spec"]}, indent=2))
    elif "--json" in flags:
        print(json.dumps({"valid": r["valid"], "errors": r["errors"], "warnings": r["warnings"], "plan": r["plan"]},
                         indent=2))
    elif r["valid"]:
        print(report(camp, r))
    sys.stdout.flush()
    for e in r["errors"]:
        print(f"error: {e}", file=sys.stderr)
    for w in r["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    return 0 if r["valid"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
