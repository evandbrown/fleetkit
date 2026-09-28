"""Specs come from the one input schema: a named spec is the base merged with its changes, labelled by what sets
it apart; host facts and the price follow from the instance type; a run must have carried out its spec."""
import json
from pathlib import Path

import pytest

import specs as SP

expand = SP.expand

REPO = Path(__file__).resolve().parents[3]
CAMPAIGNS = REPO / "experiments/campaigns"


def load(name):
    return json.loads((CAMPAIGNS / f"{name}.json").read_text())


def test_fields_come_from_the_schema_in_order():
    paths = [f["path"] for f in SP.FIELDS]
    assert paths[:2] == ["worker_host.instance_type", "hypervisor.name"]
    assert "densities" in paths and paths[-2:] == ["support_host.instance_type", "workload.chromium_extra_flags"]
    flags = SP.LABEL["workload.chromium_extra_flags"]
    assert flags["required"] is False and flags["default"] == [] and flags["label"] == "Extra Chromium flags"
    assert SP.LABEL["microvm.memory_mib"]["unit"] == "MiB"
    assert {f["tier"] for f in SP.FIELDS} == {"basic", "advanced", "rare"}


def test_a_spec_is_the_base_merged_with_its_changes():
    d = load("nested-sizes-1")
    resolved = dict(SP.resolve(d))
    small = resolved["m8i-2xlarge"]
    assert small["worker_host"]["instance_type"] == "m8i.2xlarge" and small["densities"] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert small["criteria"] == d["base"]["criteria"]
    assert [c["path"] for c in SP.changes(d["base"], small)] == ["worker_host.instance_type", "densities"]
    assert SP.changes(d["base"], resolved["m8i-4xlarge"]) == []
    assert d["base"]["worker_host"]["instance_type"] == "m8i.4xlarge"          # the base is never modified


def test_labels_name_what_sets_each_spec_apart():
    assert SP.labels(SP.resolve(load("nested-sizes-1"))) == {"m8i-4xlarge": "m8i.4xlarge", "m8i-2xlarge": "m8i.2xlarge"}
    hv = SP.labels(SP.resolve(load("nested-hv-1")))
    assert hv["cloud-hypervisor"] == "Cloud Hypervisor, pci, random-number device"
    assert hv["firecracker-mmio"] == "Firecracker, mmio, no random-number device"
    one = SP.resolve({**load("nested-sizes-1"), "specs": {"only": {}}})
    assert SP.labels(one) == {"only": "m8i.4xlarge, Firecracker, 2 vCPU / 2 GiB"}


def test_extra_chromium_flags_label_a_spec_and_left_out_are_none():
    d = load("nested-sizes-1")
    d = {**d, "specs": {"default": {}, "one-renderer": {"workload": {"chromium_extra_flags": ["--renderer-process-limit=1"]}}}}
    resolved = SP.resolve(d)
    assert SP.labels(resolved) == {"default": "no extra Chromium flags", "one-renderer": "--renderer-process-limit=1"}
    assert SP.changes(d["base"], dict(resolved)["one-renderer"]) == [
        {"path": "workload.chromium_extra_flags", "base": [], "value": ["--renderer-process-limit=1"]}]
    with pytest.raises(SP.SpecError, match="same spec"):
        SP.resolve({**d, "specs": {"aa": {}, "bb": {"workload": {"chromium_extra_flags": []}}}})


LEAN = ["--disable-features=PreloadTopChromeWebUI,WebUIOmniboxPopup,WebUIOmniboxAimPopup,WebUIOmniboxFullPopup"]


def test_a_list_change_is_the_whole_list_against_the_bases_list_or_its_default():
    base = load("nested-sizes-1")["base"]
    assert "workload" not in base
    lean = expand.merge(base, {"workload": {"chromium_extra_flags": LEAN}, "densities": [1, 4, 8]})
    # Left out by the base, the flags are at their default: the change's base is [], never null.
    assert SP.changes(base, lean) == [
        {"path": "densities", "base": [1, 4, 8, 9, 10, 11, 12, 14, 16], "value": [1, 4, 8]},
        {"path": "workload.chromium_extra_flags", "base": [], "value": LEAN}]
    # Written by the base, a list is compared item by item, in order: other items, the same items reordered, and
    # the default are each a change.
    two = expand.merge(base, {"workload": {"chromium_extra_flags": ["--process-per-site", "--no-zygote"]}})
    for value in (LEAN, ["--no-zygote", "--process-per-site"], []):
        spec = expand.merge(two, {"workload": {"chromium_extra_flags": value}})
        assert SP.changes(two, spec) == [
            {"path": "workload.chromium_extra_flags", "base": ["--process-per-site", "--no-zygote"], "value": value}]
    # A list is published as a JSON list, not a string or its words.
    doc = json.loads(json.dumps(SP.spec_doc("lean", "lean", None, base, lean)))
    assert doc["changes"][-1] == {"path": "workload.chromium_extra_flags", "base": [], "value": LEAN}
    assert doc["spec"]["workload"] == {"chromium_extra_flags": LEAN}


def test_a_list_equal_to_the_bases_or_its_default_is_no_change():
    base = load("nested-sizes-1")["base"]
    assert SP.changes(base, expand.merge(base, {"workload": {"chromium_extra_flags": []}})) == []
    assert SP.changes(base, expand.merge(base, {"densities": list(base["densities"])})) == []
    written = expand.merge(base, {"workload": {"chromium_extra_flags": []}})
    assert SP.changes(written, base) == [] and SP.changes(written, written) == []
    # Scalars are as before: only the fields that differ, each with the base's value.
    assert SP.changes(base, expand.merge(base, {"microvm": {"vcpus": 1}})) == [
        {"path": "microvm.vcpus", "base": 2, "value": 1}]


def test_the_guest_console_and_memory_pages_label_a_spec_and_left_out_are_verbose_and_4k():
    d = load("nested-sizes-1")
    d = {**d, "specs": {"default": {}, "quiet": {"microvm": {"console": "quiet"}},
                        "quiet-i8042-thp": {"microvm": {"console": "quiet-i8042", "memory_pages": "thp"}}}}
    resolved = SP.resolve(d)
    assert SP.labels(resolved) == {"default": "verbose console, 4 KiB pages", "quiet": "quiet console, 4 KiB pages",
                                   "quiet-i8042-thp": "quiet console, no keyboard probe, transparent huge pages"}
    assert SP.changes(d["base"], dict(resolved)["quiet-i8042-thp"]) == [
        {"path": "microvm.console", "base": "verbose", "value": "quiet-i8042"},
        {"path": "microvm.memory_pages", "base": "4k", "value": "thp"}]
    for key in ("microvm.console", "microvm.memory_pages"):
        assert SP.LABEL[key]["required"] is False and SP.LABEL[key]["tier"] == "advanced"
    with pytest.raises(SP.SpecError, match="same spec"):
        SP.resolve({**d, "specs": {"aa": {}, "bb": {"microvm": {"console": "verbose", "memory_pages": "4k"}}}})


def test_host_facts_follow_from_the_instance_type():
    assert SP.host_of("m8i.2xlarge") == {"instance_type": "m8i.2xlarge", "host_kind": "nested", "vcpus": 8,
                                         "memory_gib": 32, "price_usd_per_hour": 0.42336, "price_estimated": True}
    assert SP.host_of("m8i.metal-48xl")["host_kind"] == "metal"
    with pytest.raises(SP.SpecError):
        SP.host_of("x9.huge")


def test_refusals():
    d = load("nested-sizes-1")
    with pytest.raises(SP.SpecError, match="same spec"):
        SP.resolve({**d, "specs": {"aa": {}, "bb": {"densities": d["base"]["densities"]}}})
    with pytest.raises(SP.SpecError, match="isn't valid"):
        SP.resolve({**d, "specs": {"aa": {"microvm": {"vcpus": 99}}}})


def test_a_run_must_have_carried_out_its_spec():
    spec = dict(SP.resolve(load("nested-sizes-1")))["m8i-4xlarge"]
    recorded = {"worker_host": {"instance_type": "m8i.4xlarge"}, "microvm": {"vcpus": 2, "memory_mib": 2048}}
    SP.check_run_matches_spec("m8i-4xlarge-r1", recorded, spec)
    with pytest.raises(SP.SpecError, match="microvm.memory_mib"):
        SP.check_run_matches_spec("m8i-4xlarge-r1", {"microvm": {"memory_mib": 4096}}, spec)


def test_the_spec_a_run_recorded_before_specs_existed():
    run = {"backend": "firecracker", "criteria": {"step_p50_target_ms": 1000.0},
           "spec": {"densities": [1, 2], "boundary_trials": 2, "trials_per_density": 1, "settle_s": 10.0,
                    "options": {"vcpus": 2, "mem_mib": 2048, "ready_timeout_s": 180.0}}}
    rec = SP.recorded_spec(run, {"instance_type": "m8i.4xlarge"}, {"support_instance_type": "m8i.xlarge"})
    assert rec == {"worker_host": {"instance_type": "m8i.4xlarge"},
                   "hypervisor": {"name": "firecracker", "virtio_transport": "mmio", "virtio_rng": False},
                   "microvm": {"vcpus": 2, "memory_mib": 2048}, "densities": [1, 2],
                   "criteria": {"step_p50_target_ms": 1000, "ready_timeout_s": 180},
                   "procedure": {"trials_per_density": 1, "boundary_trials": 2, "settle_s": 10},
                   "support_host": {"instance_type": "m8i.xlarge"}}
    resolved = {"worker_host": {"instance_type": "c8i.4xlarge"}}
    assert SP.recorded_spec({"spec": resolved}, {}, {}) is resolved
