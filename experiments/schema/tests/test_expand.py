"""Runs tests/cases.json against expand.py, and checks the schemas against instance-types.json.

    python3 -m pytest experiments/schema/tests
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1]
EXPERIMENTS = SCHEMA.parent
sys.path.insert(0, str(SCHEMA))
import expand  # noqa: E402

CASES = json.loads((SCHEMA / "tests" / "cases.json").read_text())["cases"]


def merge_patch(target, patch):
    """RFC 7386 JSON merge patch: objects merge, null removes a key, anything else replaces."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


def campaign_of(case):
    if "campaign" in case:
        return case["campaign"]
    doc = expand.read_json(EXPERIMENTS / (case.get("file") or case["from"]))
    return merge_patch(doc, case["patch"]) if "patch" in case else doc


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case):
    r = expand.evaluate(campaign_of(case), case.get("quota"))
    assert r["valid"] == case["valid"], r["errors"]
    if case["valid"]:
        assert r["errors"] == []
    for want in case.get("errors", []):
        assert any(want in e for e in r["errors"]), f"no error containing {want!r}; got {r['errors']}"
    if "warnings" in case:
        for want in case["warnings"]:
            assert any(want in w for w in r["warnings"]), f"no warning containing {want!r}; got {r['warnings']}"
        assert len(r["warnings"]) == len(case["warnings"]), r["warnings"]
    p = r["plan"]
    if "runs" in case:
        assert [x["run"] for x in p["runs"]] == case["runs"]
    if "differs" in case:
        assert [d["field"] for d in p["differs"]] == case["differs"]
    for key in ("expected_usd", "worst_case_usd"):
        if key in case:
            assert p[key] == pytest.approx(case[key], abs=0.005)
    for key in ("waves", "too_big_for_quota"):
        if key in case:
            assert p[key] == case[key]


def spec_fields():
    """(path, field schema) for every field of a spec."""
    s = expand.SCHEMAS["spec.schema.json"]
    for key in s["properties"]:
        node = s["$defs"][key]
        if node.get("type") == "object":
            for k, f in node["properties"].items():
                yield f"{key}.{k}", f
        else:
            yield key, node


def test_every_field_has_a_tier_a_label_and_a_valid_default():
    for path, f in spec_fields():
        xb = f.get("x-builder", {})
        assert xb.get("tier") in ("basic", "advanced", "rare"), path
        assert xb.get("label"), path
        assert "default" in f and expand.validate(f["default"], f, path) == [], path


def test_the_defaults_make_a_valid_spec():
    spec = {}
    for path, f in spec_fields():
        section, _, key = path.partition(".")
        if key:
            spec.setdefault(section, {})[key] = f["default"]
        else:
            spec[section] = f["default"]
    assert expand.validate(spec, expand.SCHEMAS["spec.schema.json"]) == []
    assert expand.check_spec(spec) == ([], [])


def test_instance_types_match_the_schema():
    defs = expand.SCHEMAS["spec.schema.json"]["$defs"]
    workers = defs["worker_host"]["properties"]["instance_type"]["enum"]
    supports = defs["support_host"]["properties"]["instance_type"]["enum"]
    assert sorted(workers) == sorted(expand.TYPES)
    assert set(supports) <= set(expand.TYPES)
    assert not any(expand.TYPES[t]["metal"] for t in supports)
    for name, t in expand.TYPES.items():
        assert t["metal"] == (".metal" in name), name
        assert set(t) == {"vcpus", "memory_gib", "metal", "usd_per_hour", "estimated"}, name


def test_every_campaign_file_is_valid():
    files = sorted((EXPERIMENTS / "campaigns").rglob("*.json"))
    assert files
    for f in files:
        r = expand.evaluate(expand.read_json(f))
        assert r["valid"], (f, r["errors"])
        assert r["plan"]["campaign"] == f.stem, f


def test_a_repeated_key_is_an_error_not_last_wins():
    with pytest.raises(expand.InputError, match="appears twice"):
        expand.parse_json('{"specs": {"a": {}, "a": {"densities": [1]}}}')


def run_cli(*args):
    return subprocess.run([sys.executable, str(SCHEMA / "expand.py"), *args], capture_output=True, text=True,
                          cwd=EXPERIMENTS)


def test_cli_run_prints_the_resolved_spec():
    p = run_cli("campaigns/nested-hv-1.json", "--run", "cloud-hypervisor-r2")
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    assert doc["campaign"] == "nested-hv-1" and doc["run"] == "cloud-hypervisor-r2" and doc["replica"] == 2
    assert doc["spec"]["hypervisor"] == {"name": "cloud-hypervisor", "virtio_transport": "pci", "virtio_rng": True}
    assert expand.validate(doc["spec"], expand.SCHEMAS["spec.schema.json"]) == []


def test_cli_exit_status():
    assert run_cli("campaigns/nested-sizes-1.json").returncode == 0
    assert run_cli("campaigns/nested-sizes-1.json", "--json").returncode == 0
    assert run_cli("campaigns/nested-sizes-1.json", "--run", "nope-r1").returncode == 1
    assert run_cli().returncode == 2
    assert run_cli("campaigns/nested-sizes-1.json", "--bogus").returncode == 2


def decimal_campaign(tmp_path) -> Path:
    """nested-sizes-1 with its whole numbers written as 2.0, 45.0, 45000.0 and 1e1, as a hand edit might."""
    text = (EXPERIMENTS / "campaigns" / "nested-sizes-1.json").read_text()
    for a, b in (('"replicas": 2,', '"replicas": 2.0,'), ('"shutdown_after_minutes": 45,', '"shutdown_after_minutes": 45.0,'),
                 ('"task_timeout_ms": 45000', '"task_timeout_ms": 45000.0'), ('"settle_s": 10', '"settle_s": 1e1')):
        assert a in text, a
        text = text.replace(a, b)
    path = tmp_path / "nested-sizes-1.json"
    path.write_text(text)
    return path


def test_whole_number_decimals_in_a_file_are_whole_numbers(tmp_path):
    path = decimal_campaign(tmp_path)
    p = run_cli(str(path))
    assert p.returncode == 0, p.stderr
    assert "2 specs x 2 replicas = 4 runs" in p.stdout and "45-minute shutdown" in p.stdout
    assert "45.0" not in p.stdout
    plan = json.loads(run_cli(str(path), "--json").stdout)["plan"]
    assert [r["run"] for r in plan["runs"]] == ["m8i-4xlarge-r1", "m8i-4xlarge-r2", "m8i-2xlarge-r1", "m8i-2xlarge-r2"]
    assert type(plan["replicas"]) is int and type(plan["shutdown_after_minutes"]) is int
    p = run_cli(str(path), "--run", "m8i-2xlarge-r2")
    assert p.returncode == 0, p.stderr
    assert ".0," not in p.stdout and ".0\n" not in p.stdout  # the driver reads this; 45000.0 would be a float there
    doc = json.loads(p.stdout)
    assert doc["replica"] == 2 and type(doc["shutdown_after_minutes"]) is int
    assert type(doc["spec"]["criteria"]["task_timeout_ms"]) is int and doc["spec"]["procedure"]["settle_s"] == 10
    assert type(doc["spec"]["procedure"]["settle_s"]) is int


def test_whole_number_floats_built_in_memory_are_whole_numbers():
    camp = expand.read_json(EXPERIMENTS / "campaigns" / "nested-sizes-1.json")
    camp["replicas"], camp["shutdown_after_minutes"] = 2.0, 45.0
    plan = expand.evaluate(camp)["plan"]
    assert [r["replica"] for r in plan["runs"]] == [1, 2, 1, 2]
    assert type(plan["replicas"]) is int and type(plan["shutdown_after_minutes"]) is int
    assert expand.parse_json("[2.5, 2.0, 1e3, 7]") == [2.5, 2, 1000, 7]
    assert [type(x) for x in expand.parse_json("[2.5, 2.0, 1e3, 7]")] == [float, int, int, int]


def test_a_pattern_ends_at_the_end_of_the_text_not_before_a_final_newline():
    s = expand.SCHEMAS["campaign.schema.json"]["$defs"]["name"]
    assert expand.validate("nested-sizes-2", s) == []
    for bad in ("nested-sizes-2\n", "nested-sizes-2\n\n", "\nnested-sizes-2"):
        assert expand.validate(bad, s), repr(bad)


def test_cli_refuses_a_campaign_name_with_a_trailing_newline(tmp_path):
    camp = expand.read_json(EXPERIMENTS / "campaigns" / "nested-sizes-1.json")
    camp["name"] = "nested-sizes-1\n"
    path = tmp_path / "c.json"
    path.write_text(json.dumps(camp))
    p = run_cli(str(path), "--json")
    assert p.returncode == 1
    assert json.loads(p.stdout)["valid"] is False and "name: a name has 2 to 40" in p.stderr


def test_cli_quota_changes_the_waves():
    p = run_cli("campaigns/examples/hv-host-1.json", "--json", "--quota", "1024")
    plan = json.loads(p.stdout)["plan"]
    assert len(plan["waves"]) == 1 and plan["too_big_for_quota"] == []


def test_the_wait_after_ready_is_optional_and_absent_is_0():
    # Added after the first campaigns ran: their definitions and the specs their runs recorded stay valid and
    # unchanged (--run adds nothing), and a spec that leaves it out is estimated exactly as one that sets 0.
    spec = json.loads(run_cli("campaigns/nested-sizes-1.json", "--run", "m8i-4xlarge-r1").stdout)["spec"]
    assert "release_after_ready_s" not in spec["procedure"]
    assert expand.validate(spec, expand.SCHEMAS["spec.schema.json"]) == []
    zero = copy.deepcopy(spec)
    zero["procedure"]["release_after_ready_s"] = 0
    assert expand.run_minutes(zero) == expand.run_minutes(spec)
    required = expand.SCHEMAS["spec.schema.json"]["properties"]["procedure"]["required"]
    assert "release_after_ready_s" not in required


def test_every_trial_waits_after_ready_in_the_run_estimate():
    # nested-sizes-1's base: 9 densities, 2 labelled trials, 2 boundary trials at each of the top two densities,
    # so 15 trials; each waits the full time once its microVMs are ready.
    spec = json.loads(run_cli("campaigns/nested-sizes-1.json", "--run", "m8i-4xlarge-r1").stdout)["spec"]
    t = expand.LIMITS["run_time_estimate"]
    trial_minutes = {}
    for wait in (0, 20, 60):
        s = copy.deepcopy(spec)
        s["procedure"]["release_after_ready_s"] = wait
        trial_minutes[wait] = expand.run_minutes(s) - t["setup_minutes"]["nested"] - t["finish_minutes"]
    assert trial_minutes == {0: 5, 20: 10, 60: 20}  # 283.8 s of trials, then 300 s and 900 s more, rounded up


def test_extra_chromium_flags_are_optional_and_absent_is_none():
    # Added after the first campaigns ran: --run adds nothing to a spec that leaves them out, so the specs those
    # runs recorded stay what they were, and a spec that leaves them out runs what one with an empty list runs.
    spec = json.loads(run_cli("campaigns/nested-sizes-1.json", "--run", "m8i-4xlarge-r1").stdout)["spec"]
    assert "workload" not in spec
    assert "workload" not in expand.SCHEMAS["spec.schema.json"]["required"]
    assert expand.OPTIONAL == {"microvm.console": "verbose", "microvm.memory_pages": "4k",
                               "procedure.release_after_ready_s": 0, "workload.chromium_extra_flags": []}
    empty = copy.deepcopy(spec)
    empty["workload"] = {"chromium_extra_flags": []}
    assert expand.filled(empty) == expand.filled(spec) and expand.differing([("a", spec), ("b", empty)]) == []
    assert expand.check_spec(empty) == expand.check_spec(spec)


def test_the_guest_console_and_memory_pages_are_optional_and_absent_is_as_before():
    # Added after the first campaigns ran: --run adds nothing to a spec that leaves them out, so the specs those
    # runs recorded stay what they were, and a spec that leaves them out runs what one that sets verbose and 4k runs.
    spec = json.loads(run_cli("campaigns/nested-sizes-1.json", "--run", "m8i-4xlarge-r1").stdout)["spec"]
    assert spec["microvm"] == {"vcpus": 2, "memory_mib": 2048}
    microvm = expand.SCHEMAS["spec.schema.json"]["$defs"]["microvm"]
    required = expand.SCHEMAS["spec.schema.json"]["properties"]["microvm"]["required"]
    assert "console" not in required and "memory_pages" not in required
    assert microvm["properties"]["console"]["enum"] == ["verbose", "quiet", "quiet-i8042"]
    assert microvm["properties"]["memory_pages"]["enum"] == ["4k", "thp"]
    explicit = copy.deepcopy(spec)
    explicit["microvm"].update({"console": "verbose", "memory_pages": "4k"})
    assert expand.filled(explicit) == expand.filled(spec) and expand.differing([("a", spec), ("b", explicit)]) == []
    assert expand.check_spec(explicit) == expand.check_spec(spec)
    assert expand.run_minutes(explicit) == expand.run_minutes(spec)
    quiet = copy.deepcopy(spec)
    quiet["microvm"].update({"console": "quiet-i8042", "memory_pages": "thp"})
    assert expand.validate(quiet, expand.SCHEMAS["spec.schema.json"]) == [] and expand.check_spec(quiet) == ([], [])
    assert [d["field"] for d in expand.differing([("a", spec), ("b", quiet)])] == ["microvm.console",
                                                                                  "microvm.memory_pages"]


def test_a_named_spec_may_change_the_extra_chromium_flags_and_nothing_else_new():
    changes = expand.SCHEMAS["campaign.schema.json"]["$defs"]["spec_changes"]["properties"]
    variable = sorted(k for k, v in changes.items() if "$ref" in v)
    assert variable == ["densities", "hypervisor", "microvm", "worker_host", "workload"]
    workload = expand.SCHEMAS["spec.schema.json"]["$defs"]["workload"]
    assert list(workload["properties"]) == ["chromium_extra_flags"]


def test_chromium_flags_file_is_consistent():
    f = expand.FLAGS
    item = expand.SCHEMAS["spec.schema.json"]["$defs"]["workload"]["properties"]["chromium_extra_flags"]["items"]
    base_names = {expand.flag_name(b) for b in f["base"]}
    assert len(base_names) == len(f["base"])
    for name, rule in f["allowed"].items():
        assert expand.validate(name, item) == [], name
        assert name not in base_names and name not in f["refused"], name
        assert rule["why"] and rule["value"] in ("none", "integer", "features", "v8"), name
        for sub in rule.get("v8", {}).values():
            assert sub["why"] and sub["value"] in ("none", "integer")
    for name, why in f["refused"].items():
        assert expand.validate(name, item) == [] and why and name not in base_names, name
    assert expand.chromium_argv([]) == f["base"] + ["about:blank"]
    assert expand.chromium_argv(["--no-zygote"]) == f["base"] + ["--no-zygote", "about:blank"]


def test_every_allowed_chromium_flag_passes_in_its_simplest_form():
    for name, rule in expand.FLAGS["allowed"].items():
        kind = rule["value"]
        value = {"none": "", "integer": f"={rule.get('minimum')}", "features": f"={next(iter(rule.get('features', [''])))}",
                 "v8": "=--jitless"}[kind]
        assert expand.flag_problems(name + value) == [], name + value
    assert expand.flags_problems(["--js-flags=--max-old-space-size=512 --max-semi-space-size=16 --no-opt"]) == []
    assert expand.flags_problems(["--js-flags=--jitless  --no-opt"])  # two spaces: an empty V8 flag between them
