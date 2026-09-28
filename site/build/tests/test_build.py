"""Real builds: the catalog (cap-baseline-1, a campaign of one), and a campaign in the new results layout made from
copies of that run, with one trial marked as failing outside the experiment.

results/ is local and gitignored, so these skip where it hasn't been downloaded.
"""
import json
import shutil
from pathlib import Path

import pytest

import build_data
import source as S
from words import retired_word_in, without_legacy_blocks

BUILD = Path(__file__).resolve().parents[1]
SITE = BUILD.parent
REPO = SITE.parent
RUN = REPO / "results/cap-baseline-1/host/capacity"
BIG = shutil.ignore_patterns("*.jsonl", "console-logs", "guest-logs", "hostd.log", "otlp", "*.tgz")

needs_results = pytest.mark.skipif(not RUN.exists(), reason="results/cap-baseline-1 isn't downloaded here")


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("data") / "data"
    assert build_data.main(["--out", str(out), "--no-contract"]) == 0
    yield out
    shutil.rmtree(out, ignore_errors=True)


def read(root, *p):
    return json.loads(root.joinpath(*p).read_text())


@needs_results
def test_index_lists_the_catalog_and_features_the_newest_complete_campaign(data):
    index = read(data, "index.json")
    assert index["schema"] == "fleetkit-site-data/2"
    ids = [c["id"] for c in index["campaigns"]]
    assert "cap-baseline-1" in ids
    complete = [c for c in index["campaigns"] if c.get("status") == "complete"]
    newest = max(complete, key=lambda c: c.get("started") or "")["id"]
    assert index["featured"] == newest          # no "featured" in the catalog: the newest complete (D58)
    assert not any(c.get("synthetic") for c in index["campaigns"])


@needs_results
def test_the_published_result(data):
    run = read(data, "campaigns/cap-baseline-1/runs/baseline-r1.json")
    r = run["result"]
    assert (r["tested_successfully"], r["first_failed"], r["gap"], r["not_tested"]) == (8, 12, [9, 11], [16])
    assert r["per_host_vcpu"] == 0.5 and r["midpoint_per_host_vcpu"] == 0.625
    assert run["stopped_early"] is False
    assert [t["id"] for t in run["trials"]] == ["warmup", "d1-t1", "d2-t1", "d4-t1", "d8-t1", "d12-t1", "d8-t2",
                                                "d8-t3", "d12-t2", "d12-t3", "illustration"]
    assert next(t for t in run["trials"] if t["id"] == "d12-t1")["order"] == 6
    d12 = next(b for b in run["by_density"] if b["density"] == 12)
    home = next(c for c in d12["checks"] if c["subject"] == "home" and c["stat"] == "p50")
    assert [round(v) for v in home["range"]] == [1063, 1563] and home["met"] == 0
    sep = r["limit"]["separated_by"]
    assert sep["rule"] == "host_cpu_pressure_pct"
    assert [round(v, 1) for v in sep["passing"]] == [5.5, 10.6]
    assert [round(v, 1) for v in sep["failing"]] == [34.5, 46.5]
    assert r["limit"]["host_consumer"] == "microvm_vcpus" and r["limit"]["guest_group"] == "renderer"
    # never rounded across a threshold: d12-t2's utilisation is 89.98%, under the 90% rule
    assert next(t for t in run["trials"] if t["id"] == "d12-t2")["host"]["cpu_util_pct"] == 89.9837
    cost = r["cost_per_1000_tasks"]
    assert set(cost) == {"execution", "observed"}
    assert 0.06 < cost["execution"][0] <= cost["execution"][1] < 0.09
    assert 0.18 <= cost["observed"][0] <= cost["observed"][1] <= 0.21


@needs_results
def test_a_trial_document(data):
    t = read(data, "campaigns/cap-baseline-1/runs/baseline-r1/d12-t1.json")
    assert round(t["marks"]["all_ready_ms"] / 1000, 2) == 4.88 and round(t["marks"]["last_return_ms"] / 1000, 2) == 8.72
    assert len(t["microvms"]) == 12 and [m["index"] for m in t["microvms"]] == list(range(1, 13))
    assert all(m["steps"] and m.get("boot") and m.get("img") for m in t["microvms"])
    assert "fixture_rtt" not in t["series"]          # the support host's health is never a result (D63)
    ill = read(data, "campaigns/cap-baseline-1/runs/baseline-r1/illustration.json")
    assert [f["step"] for f in ill["filmstrip"]["frames"]] == ["home", "search", "open_product", "add_to_cart",
                                                                "verify_cart"]


@needs_results
def test_every_screenshot_is_published_once_in_both_sizes(data):
    shas = set()
    for run_doc in (data / "campaigns").glob("*/runs/*.json"):
        shas |= {s for s in json.loads(run_doc.read_text())["tasks"]["img"] if s}
        for f in run_doc.with_suffix("").glob("*.json"):
            shas |= {fr["img"] for fr in json.loads(f.read_text()).get("filmstrip", {}).get("frames", [])}
    files = {p.name for p in (data / "img").iterdir()}
    assert files == {f"{s}.{k}.webp" for s in shas for k in ("t", "f")}


@needs_results
def test_the_campaign_of_one(data):
    c = read(data, "campaigns/cap-baseline-1/campaign.json")
    assert c["before_campaigns"] is True and c["reconstructed"] is True
    d = c["definition"]
    assert d["specs"] == {"baseline": {}} and "shutdown_after_minutes" not in d
    assert d["base"]["hypervisor"] == {"name": "firecracker", "virtio_transport": "mmio", "virtio_rng": False}
    assert d["base"]["support_host"] == {"instance_type": "m8i.xlarge"}
    s = c["specs"][0]
    assert (s["name"], s["label"], s["spec"]["densities"]) == \
        ("baseline", "m8i.4xlarge, Firecracker, 2 vCPU / 2 GiB", [1, 2, 4, 8, 12, 16])
    assert s["host"] == {"instance_type": "m8i.4xlarge", "host_kind": "nested", "vcpus": 16, "memory_gib": 64,
                         "price_usd_per_hour": 0.84672, "price_estimated": False}
    assert "price" not in json.dumps(s["spec"])           # derived from the instance type, never a spec input
    assert c["preregistration"]["path"] == "docs/capacity-experiment.md"
    assert [r["key"] for r in c["rules"]][:2] == ["host_cpu_pressure_pct", "host_cpu_util_pct"]
    assert c["outcomes"] == [{"spec": "baseline", "midpoint_per_host_vcpu": 0.625, "replicas": [{
        "run": "baseline-r1", "host_vcpus": 16, "tested_successfully": 8, "first_failed": 12, "stopped_early": False,
        "per_host_vcpu": 0.5, "midpoint_per_host_vcpu": 0.625,
        "cost_per_1000_tasks": c["runs"][0]["result"]["cost_per_1000_tasks"]}]}]


# ---- the new results layout: results/<campaign>/campaign.json and <spec>-r<k>/ --------------------------------

def set_aside(run_dir: Path, trial_id: str) -> None:
    """What the harness does with a trial that failed outside the experiment (D63): its directory and its rows in
    the trial tables move to ops/set-aside/, out of the run."""
    dest = run_dir / "ops/set-aside" / f"{trial_id}-1"
    dest.parent.mkdir(parents=True)
    shutil.move(str(run_dir / "trials" / trial_id), str(dest))
    for table in run_dir.glob("*.csv"):
        lines = table.read_text().splitlines(keepends=True)
        header = lines[0].rstrip("\n").split(",")
        if "trial_id" not in header:
            continue
        i = header.index("trial_id")
        table.write_text("".join([lines[0]] + [ln for ln in lines[1:] if ln.split(",")[i] != trial_id]))


def _campaign_repo(tmp: Path) -> Path:
    """A repo with one campaign of two replicas, both copies of cap-baseline-1's run. In replica 2, trial d12-t3
    failed outside the experiment, so the harness set it aside."""
    repo = tmp / "repo"
    (repo / "results").mkdir(parents=True)
    cdir = repo / "results/copies-test"
    cdir.mkdir()
    base = read(REPO / "experiments/campaigns", "nested-sizes-1.json")["base"]
    base = {**base, "hypervisor": {"name": "firecracker", "virtio_transport": "mmio", "virtio_rng": False},
            "densities": [1, 2, 4, 8, 12, 16]}
    definition = {"name": "copies-test", "question": "Does the same run, read twice, give the same result?",
                  "replicas": 2, "shutdown_after_minutes": 45, "base": base, "specs": {"baseline": {}},
                  "why": {"baseline": "The spec cap-baseline-1 ran, to test the new layout."}}
    (cdir / "campaign.json").write_text(json.dumps(definition))
    for k in (1, 2):
        shutil.copytree(RUN, cdir / f"baseline-r{k}", ignore=BIG)
    set_aside(cdir / "baseline-r2", "t010-firecracker-n12-r3")
    (repo / "catalog.json").write_text(json.dumps({"format": "fleetkit-catalog/2", "campaigns": [
        {"id": "copies-test", "title": "Two copies", "notes": ["A test campaign."]}]}))
    return repo


@needs_results
def test_a_campaign_in_the_new_layout(tmp_path):
    repo = _campaign_repo(tmp_path)
    out = tmp_path / "data"
    assert build_data.main(["--catalog", str(repo / "catalog.json"), "--repo", str(repo), "--out", str(out),
                            "--no-contract"]) == 0
    index = read(out, "index.json")
    assert index["featured"] == "copies-test" and index["campaigns"][0]["status"] == "complete"
    c = read(out, "campaigns/copies-test/campaign.json")
    assert c["definition"]["name"] == "copies-test" and c["specs"][0]["why"].startswith("The spec cap-baseline-1")
    assert [r["id"] for r in c["runs"]] == ["baseline-r1", "baseline-r2"]
    o = c["outcomes"][0]
    assert [x["midpoint_per_host_vcpu"] for x in o["replicas"]] == [0.625, 0.625]
    assert o["midpoint_per_host_vcpu"] == 0.625
    assert c["notes"] == ["A test campaign."]
    # The set-aside trial never appears: density 12 has two trials in replica 2, and ids stay 1..n.
    r2 = read(out, "campaigns/copies-test/runs/baseline-r2.json")
    d12 = next(b for b in r2["by_density"] if b["density"] == 12)
    assert (d12["trials"], d12["trial_ids"], d12["result"]) == (2, ["d12-t1", "d12-t2"], "failed")
    assert not (out / "campaigns/copies-test/runs/baseline-r2/d12-t3.json").exists()
    assert r2["stopped_early"] is False and len(r2["tasks"]["trial"]) == len(read(out, "campaigns/copies-test/runs/baseline-r1.json")["tasks"]["trial"]) - 12


@needs_results
def test_the_builder_reads_old_directories_through_the_harness_alias_map():
    """No second table of old names: the builder reads through driver.outputs.RunDir."""
    src, _ = S.read_run(RUN, build_data.SP.TYPES)
    assert [t.id for t in src.trials][:2] == ["warmup", "d1-t1"]
    assert S.harness_outputs.RunDir(RUN).legacy is True
    assert not (BUILD / "legacy.py").exists()


def test_the_builder_names_retired_words_only_in_marked_blocks():
    hits = []
    for p in sorted(BUILD.rglob("*")):
        if p.suffix not in (".py", ".ts", ".json", ".md") or ".staging" in p.parts or "__pycache__" in p.parts:
            continue
        for i, line in enumerate(without_legacy_blocks(p.read_text()).splitlines(), 1):
            w = retired_word_in(line)
            if w:
                hits.append(f"{p.relative_to(BUILD)}:{i}: {w}: {line.strip()}")
    catalog = (SITE / "catalog.json").read_text()
    hits += [f"catalog.json: {retired_word_in(line)}" for line in catalog.splitlines() if retired_word_in(line)]
    assert hits == []


def test_the_catalog_refuses_an_id_the_site_routes_use(tmp_path):
    import catalog as C
    cat = tmp_path / "catalog.json"
    cat.write_text(json.dumps({"format": C.FORMAT, "campaigns": [{"id": "compare", "title": "Compare"}]}))
    with pytest.raises(C.CatalogError, match="reserved"):
        C.load(cat, REPO)
