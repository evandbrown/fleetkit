"""Specs, from the one input schema (experiments/schema): resolving a campaign definition into its specs, what
sets each spec apart, the host facts that follow from the instance type, and the spec a run recorded.

expand.py is the schema's own checker; the builder imports its merge and validation rather than keeping a copy.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "experiments/schema"
sys.path.insert(0, str(SCHEMA_DIR))

import expand  # noqa: E402

TYPES = expand.TYPES
HYPERVISOR_NAME = {"firecracker": "Firecracker", "cloud-hypervisor": "Cloud Hypervisor"}


class SpecError(Exception):
    pass


def fields() -> list[dict]:
    """Every leaf of spec.schema.json in schema order: path, label, unit, tier, whether it is required, and its
    default when the schema gives one."""
    schema = expand.SCHEMAS["spec.schema.json"]
    out = []

    def walk(node: dict, prefix: str, required: bool):
        node = expand.resolve(node["$ref"], "spec.schema.json")[0] if "$ref" in node else node
        props = node.get("properties")
        if node.get("type") == "object" and props:
            req = set(node.get("required", []))
            for k, v in props.items():
                walk(v, f"{prefix}{k}.", required and k in req)
            return
        xb = node.get("x-builder") or {}
        f = {"path": prefix.rstrip("."), "label": xb.get("label", prefix.rstrip(".")), "tier": xb.get("tier", "rare"),
             "required": required}
        if xb.get("unit"):
            f["unit"] = xb["unit"]
        if "default" in node:
            f["default"] = node["default"]
        out.append(f)

    top = set(schema.get("required", []))
    for k, v in schema["properties"].items():
        walk(v, f"{k}.", k in top)
    return out


FIELDS = fields()
LABEL = {f["path"]: f for f in FIELDS}


def show(path: str, value) -> str:
    """A spec value in words, as the site shows it."""
    if path == "hypervisor.name":
        return HYPERVISOR_NAME.get(value, str(value))
    if path == "hypervisor.virtio_rng":
        return "random-number device" if value else "no random-number device"
    if path == "microvm.memory_mib":
        return f"{value // 1024} GiB" if value % 1024 == 0 else f"{value:,} MiB"
    if path == "microvm.vcpus":
        return f"{value} vCPU" + ("" if value == 1 else "s")
    if path == "workload.chromium_extra_flags":
        return " ".join(value) if value else "no extra Chromium flags"
    if path == "microvm.console":
        return {"quiet-i8042": "quiet console, no keyboard probe"}.get(value, f"{value} console")
    if path == "microvm.memory_pages":
        return "transparent huge pages" if value == "thp" else "4 KiB pages"
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return expand.fmt(value)


def summary(spec: dict) -> str:
    """One line for a spec on its own: "m8i.4xlarge, Firecracker, 2 vCPU / 2 GiB"."""
    mem = spec["microvm"]["memory_mib"]
    mem_s = f"{mem // 1024} GiB" if mem % 1024 == 0 else f"{mem:,} MiB"
    return (f"{spec['worker_host']['instance_type']}, {HYPERVISOR_NAME[spec['hypervisor']['name']]}, "
            f"{spec['microvm']['vcpus']} vCPU / {mem_s}")


def labels(specs: list[tuple[str, dict]]) -> dict[str, str]:
    """What sets each spec apart: its values on the inputs that differ between the specs, the densities left
    out (they follow the host). A campaign of one spec gets its summary."""
    if len(specs) == 1:
        return {specs[0][0]: summary(specs[0][1])}
    differ = [d["field"] for d in expand.differing(specs) if d["field"] != "densities"]
    if not differ:
        differ = ["densities"]
    flat = {name: expand.filled(spec) for name, spec in specs}
    return {name: ", ".join(show(p, flat[name][p]) for p in differ) for name, _ in specs}


def host_of(instance_type: str) -> dict:
    """Facts that follow from the instance type (instance-types.json): never spec inputs (D56)."""
    t = TYPES.get(instance_type)
    if t is None:
        raise SpecError(f"{instance_type} isn't in experiments/schema/instance-types.json")
    return {"instance_type": instance_type, "host_kind": "metal" if t["metal"] else "nested", "vcpus": t["vcpus"],
            "memory_gib": t["memory_gib"], "price_usd_per_hour": t["usd_per_hour"], "price_estimated": bool(t["estimated"])}


def changes(base: dict, spec: dict) -> list[dict]:
    """Every leaf path where ``spec`` differs from ``base``, in schema order. An optional field either leaves out
    is at its default."""
    fb, fs = expand.filled(base), expand.filled(spec)
    order = [f["path"] for f in FIELDS] + [p for p in fs if p not in LABEL]
    return [{"path": p, "base": fb.get(p), "value": fs[p]} for p in order
            if p in fs and not expand.same(fb.get(p), fs[p])]


def resolve(definition: dict, whole: bool = True) -> list[tuple[str, dict]]:
    """(name, spec) for each named spec, each checked against the schema as expand.py checks it. Cost limits
    and the quota are the launcher's business, not the publisher's: a campaign that ran is published even if
    the limits change later. ``whole=False`` skips the definition's own schema (a reconstructed definition)."""
    errs = expand.validate(definition, expand.SCHEMAS["campaign.schema.json"], "", "campaign.schema.json") if whole else []
    if errs:
        raise SpecError("the campaign definition isn't valid: " + "; ".join(errs))
    out = []
    for name, changes_ in definition["specs"].items():
        spec = expand.merge(definition["base"], changes_)
        errs = expand.validate(spec, expand.SCHEMAS["spec.schema.json"])
        if not errs:
            errs = expand.check_spec(spec)[0]
        if errs:
            raise SpecError(f"spec {name} isn't valid: " + "; ".join(errs))
        out.append((name, spec))
    for i, (a, sa) in enumerate(out):
        for b, sb in out[i + 1:]:
            if expand.same(expand.filled(sa), expand.filled(sb)):
                raise SpecError(f"specs {a} and {b} are the same spec")
    return out


def spec_doc(name: str, label: str, why: str | None, base: dict, spec: dict) -> dict:
    doc = {"name": name, "label": label}
    if why:
        doc["why"] = why
    doc.update({"changes": changes(base, spec), "spec": spec, "host": host_of(spec["worker_host"]["instance_type"])})
    return doc


def _num(v):
    f = float(v)
    return int(f) if f.is_integer() else f


def recorded_spec(run: dict, manifest: dict, env: dict) -> dict:
    """The spec a run recorded, as far as it recorded one.

    A run launched from a campaign records the resolved spec in run.json ``spec`` (what expand.py --run printed).
    Until the driver reads specs, and for every run before campaigns, ``spec`` holds the driver's options, and
    this rebuilds the spec's fields from them. A field the run didn't record is left out.
    """
    sp = run.get("spec") or {}
    if "worker_host" in sp:
        return sp
    opts = sp.get("options") or {}
    out: dict = {}
    instance = env.get("instance_type") or manifest.get("instance_type")
    if instance:
        out["worker_host"] = {"instance_type": instance}
    backend = run.get("backend")
    if backend == "firecracker":
        # Before specs, the driver ran Firecracker only one way: mmio devices, no random-number device.
        out["hypervisor"] = {"name": "firecracker", "virtio_transport": "mmio", "virtio_rng": False}
    elif backend:
        out["hypervisor"] = {"name": str(backend)}
    if "vcpus" in opts and "mem_mib" in opts:
        out["microvm"] = {"vcpus": int(opts["vcpus"]), "memory_mib": int(opts["mem_mib"])}
    if sp.get("densities"):
        out["densities"] = [int(x) for x in sp["densities"]]
    crit = run.get("criteria") or sp.get("criteria") or {}
    c = {k: _num(crit[k]) for k in ("step_p50_target_ms", "step_p95_target_ms", "task_p95_target_ms") if k in crit}
    for k in ("ready_timeout_s", "step_timeout_ms", "task_timeout_ms"):
        if k in opts:
            c[k] = _num(opts[k])
    if c:
        out["criteria"] = c
    proc = {}
    if "trials_per_density" in sp:
        proc["trials_per_density"] = int(sp["trials_per_density"])
    if "boundary_trials" in sp:
        proc["boundary_trials"] = int(sp["boundary_trials"])
    if "settle_s" in sp:
        proc["settle_s"] = _num(sp["settle_s"])
    if proc:
        out["procedure"] = proc
    if env.get("support_instance_type"):
        out["support_host"] = {"instance_type": env["support_instance_type"]}
    return out


def check_run_matches_spec(run_id: str, recorded: dict, spec: dict) -> None:
    """A run must have carried out its spec: every field it recorded equals the spec's."""
    have = expand.flatten(spec)
    for path, value in expand.flatten(recorded).items():
        if path in have and not expand.same(value, have[path]):
            raise SpecError(f"run {run_id}: recorded {path} = {expand.fmt(value)}, its spec says {expand.fmt(have[path])}")
