"""The launcher's decisions, offline: what it refuses, the order runs start in under the quota, what
a Terraform plan may change, and the support host's health rules.

    harness/.venv/bin/python -m pytest -q experiments/launcher/tests
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import launch  # noqa: E402
import support_health  # noqa: E402

CAMPAIGNS = launch.REPO / "experiments" / "campaigns"


def plan_of(name: str, quota: int = 32) -> dict:
    path = CAMPAIGNS / f"{name}.json" if (CAMPAIGNS / f"{name}.json").exists() else CAMPAIGNS / "examples" / f"{name}.json"
    r = launch.EXPAND.evaluate(launch.EXPAND.read_json(path), quota)
    assert r["valid"], r["errors"]
    return r["plan"]


def ok(**kw):
    args = {"spent": 10.0, "free_vcpus": 32, "head_pushed": True, "existing_runs": []}
    args.update(kw)
    return args


# ---- refusals ---------------------------------------------------------------------------------

def test_a_valid_campaign_within_every_limit_may_launch():
    assert launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan_of("nested-sizes-1"), **ok()) == []


def test_the_project_cap_counts_what_is_spent():
    plan = plan_of("nested-sizes-1")
    worst = plan["worst_case_usd"]
    assert launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan, **ok(spent=200 - worst)) == []
    why = launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan, **ok(spent=200 - worst + 0.01))
    assert len(why) == 1 and "crosses the $200.00 project cap" in why[0]


def test_unknown_spend_is_refused():
    why = launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan_of("nested-sizes-1"), **ok(spent=None))
    assert why == ["spend so far is unknown (Cost Explorer didn't answer); pass --spent USD to launch anyway"]


def test_the_per_campaign_limit_is_checked_again():
    plan = {**plan_of("nested-sizes-1"), "worst_case_usd": 80.0}
    assert any("above the $75.00 limit" in w for w in launch.refusals(CAMPAIGNS / "x.json", plan, **ok()))


def test_examples_metal_quota_head_and_existing_runs_are_refused():
    plan = plan_of("hv-host-1", quota=1024)
    why = launch.refusals(CAMPAIGNS / "examples" / "hv-host-1.json", plan,
                          **ok(free_vcpus=32, head_pushed=False, existing_runs=["firecracker-nested-r1"]))
    text = "\n".join(why)
    assert "written, not approved" in text
    assert "m8i.metal-48xl isn't allowed by the account's instance-type guardrail" in text
    assert "needs 208 vCPUs with its support host; 32 are free" in text
    assert "HEAD isn't on any remote branch" in text
    assert "results/hv-host-1/firecracker-nested-r1/ already exists" in text
    why = launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan_of("nested-sizes-1"),
                          **ok(uncommitted=["harness/driver/driver/cli.py"]))
    assert why == ["uncommitted changes the hosts wouldn't run (they check out HEAD): harness/driver/driver/cli.py"]


def test_uncommitted_covers_everything_the_hosts_run(monkeypatch):
    """setup.sh runs the Makefile's targets and the support host mounts observability/'s collector
    configuration, both from HEAD, so a change there that isn't committed is refused too."""
    seen = []

    class Done:
        stdout = " M Makefile\n M observability/otelcol-config.yaml\n?? harness/host/hostd/backends/new.py\n"

    monkeypatch.setattr(launch, "sh", lambda args, **kw: seen.append(args) or Done())
    assert launch.uncommitted() == ["Makefile", "observability/otelcol-config.yaml",
                                    "harness/host/hostd/backends/new.py"]
    paths = seen[0][seen[0].index("--") + 1:]
    for p in ("harness", "images", "fixture", "experiments/launcher", "experiments/schema", "Makefile",
              "observability"):
        assert p in paths
        assert (launch.REPO / p).exists()


def test_a_run_too_big_for_what_is_free_is_refused():
    plan = plan_of("nested-sizes-1")
    why = launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan, **ok(free_vcpus=16))
    assert [w.split(":")[0] for w in why] == ["m8i-4xlarge-r1", "m8i-4xlarge-r2"]


# ---- waves ------------------------------------------------------------------------------------

def test_runs_start_in_wave_order_as_their_vcpus_fit():
    plan = plan_of("nested-sizes-1")
    q = launch.queue_order(plan)
    assert [r["run"] for r in q] == [n for w in plan["waves"] for n in w]
    assert [r["vcpus"] for r in q] == [20, 12, 20, 12]
    first = launch.next_to_start(q, 32)
    assert [r["run"] for r in first] == [q[0]["run"], q[1]["run"]]
    rest = [r for r in q if r not in first]
    assert launch.next_to_start(rest, 0) == []
    # the 2xlarge run ends first: its 12 vCPUs let the other 2xlarge run start, not the 4xlarge one
    assert [r["vcpus"] for r in launch.next_to_start(rest, 12)] == [12]
    assert [r["vcpus"] for r in launch.next_to_start(rest, 32)] == [20, 12]


def test_under_a_larger_quota_everything_starts_at_once():
    q = launch.queue_order(plan_of("nested-hv-1", quota=1024))
    assert len(launch.next_to_start(q, 1024)) == len(q) == 6


# ---- what a Terraform plan may change ----------------------------------------------------------

SHOW = {"resource_changes": [
    {"address": 'aws_instance.worker["a-r1"]', "mode": "managed", "change": {"actions": ["create"]}},
    {"address": 'aws_instance.support["a-r1"]', "mode": "managed", "change": {"actions": ["create"]}},
    {"address": "data.aws_caller_identity.member", "mode": "data", "change": {"actions": ["read"]}},
    {"address": 'aws_instance.worker["b-r1"]', "mode": "managed", "change": {"actions": ["no-op"]}},
]}


def test_plan_changes_ignore_reads_and_no_ops():
    assert launch.plan_changes(SHOW) == [('aws_instance.support["a-r1"]', "create"),
                                         ('aws_instance.worker["a-r1"]', "create")]
    replace = {"resource_changes": [{"address": "x", "mode": "managed",
                                     "change": {"actions": ["delete", "create"]}}]}
    assert launch.plan_changes(replace) == [("x", "replace")]


def test_the_gate_applies_exactly_the_expected_change():
    ch = launch.plan_changes(SHOW)
    assert launch.gate(ch, create=set(launch.run_addresses("a-r1"))) is None
    assert "unexpected change: create" in launch.gate(ch, create={'aws_instance.worker["a-r1"]'})
    assert "doesn't create" in launch.gate([], create=set(launch.run_addresses("a-r1")))
    gone = [('aws_instance.worker["a-r1"]', "delete")]  # its support host had already shut itself down
    assert launch.gate(gone, delete=set(launch.run_addresses("a-r1"))) is None
    assert launch.gate(gone, delete=set(launch.run_addresses("b-r1"))).startswith("unexpected change: delete")
    assert launch.gate([("aws_instance.worker[\"b-r1\"]", "replace")], create=set()) is not None


def test_the_gate_allows_the_security_group_only_at_setup():
    sg = [("aws_security_group.support", "create"), ('aws_vpc_security_group_ingress_rule.support_from_workers["health"]',
                                                    "create")]
    assert launch.gate(sg, setup=True) is None
    assert launch.gate(sg) is not None
    assert launch.gate([("aws_security_group.support", "delete")], setup=True) is not None


def test_backend_args_swap_in_the_campaign_state_key():
    hcl = 'bucket       = "state-bucket"\nkey          = "experiments/terraform.tfstate"\nregion = "us-east-1"\n' \
          "encrypt      = true\n# comment\nprofile = \"default\"\n"
    assert launch.backend_args(hcl, "experiments/campaigns/c-1.tfstate") == [
        "-backend-config=bucket=state-bucket", "-backend-config=region=us-east-1", "-backend-config=encrypt=true",
        "-backend-config=profile=default", "-backend-config=key=experiments/campaigns/c-1.tfstate"]


def test_run_status_uses_the_results_words():
    assert launch.run_status(None) == "no results"
    assert launch.run_status({"plan": {"complete": True}}) == "complete"
    assert launch.run_status({"plan": {"complete": False, "stop_reason": "not_clean"}}) == "stopped early"


# ---- the support host's health rules ------------------------------------------------------------

def rows_for(usec_per_s: list[float], t0: float = 100.0) -> list:
    out, total = [], 0.0
    for i, u in enumerate([0.0] + usec_per_s):
        total += u
        out.append((t0 + i, "fleetkit-fixture", "cpu_usage_usec", total))
        out.append((t0 + i, "support", "cpu_util", 20.0))
    return out


def test_a_quiet_fixture_has_no_problems():
    rows = rows_for([50_000] * 10)
    probes = [(100.0 + i, True, 3.0) for i in range(10)]
    r = support_health.evaluate(rows, probes, 101.0, 108.0)
    assert r["problems"] == [] and r["fixture_cpu_max_pct"] == 5.0 and r["probe"]["count"] == 9


def test_a_busy_fixture_is_a_problem_only_inside_the_window():
    rows = rows_for([50_000] * 5 + [950_000] + [50_000] * 4)
    probes = [(100.0 + i, True, 3.0) for i in range(10)]
    assert support_health.evaluate(rows, probes, 104.5, 106.5)["problems"] == ["the fixture used 95% of its CPU"]
    assert support_health.evaluate(rows, probes, 107.0, 109.0)["problems"] == []


def test_a_fixture_that_does_not_answer_its_own_host_is_a_problem():
    rows = rows_for([50_000] * 5)
    probes = [(101.0, True, 3.0), (102.0, False, 2000.0), (103.0, True, 1500.0)]
    r = support_health.evaluate(rows, probes, 100.0, 104.0)
    assert r["problems"] == ["the fixture didn't answer its own host 1 of 3 times",
                             "the fixture took 1500 ms to answer its own host"]


def test_no_samples_is_not_a_problem():
    assert support_health.evaluate([], [], 0.0, 10.0)["problems"] == []


def test_the_monitor_reads_only_whole_new_lines(tmp_path):
    m = tmp_path / "support-metrics.csv"
    m.write_text("ts,subject,metric,value\n100,fleetkit-fixture,cpu_usage_usec,0\n101,fleetkit-fixture,cpu_usage_usec,9")
    mon = support_health.Monitor(str(m), "http://127.0.0.1:9/")
    assert mon.window(0, 200)["fixture_cpu_max_pct"] is None  # the second line isn't finished yet
    with open(m, "a") as fh:
        fh.write("50000\n102,fleetkit-fixture,cpu_usage_usec,1900000\n")
    assert mon.window(0, 200)["fixture_cpu_max_pct"] == pytest.approx(95.0)


# ---- one run failing doesn't stop the others; every run's hosts are destroyed ------------------

class FakeStack:
    def __init__(self):
        self.added, self.removed, self.lock = [], [], __import__("threading").Lock()

    def add(self, run, worker, support):
        with self.lock:
            self.added.append((run, worker, support))
        return {"worker_id": f"i-w-{run}", "support_id": f"i-s-{run}", "support_ip": "10.0.0.9"}

    def remove(self, run):
        with self.lock:
            self.removed.append(run)

    def destroy_all(self):
        return []


def test_a_failed_run_does_not_stop_the_others(tmp_path, monkeypatch):
    camp = launch.EXPAND.read_json(CAMPAIGNS / "nested-sizes-1.json")
    plan = plan_of("nested-sizes-1")
    monkeypatch.setattr(launch, "RESULTS", tmp_path)
    monkeypatch.setattr(launch, "ssm_online", lambda iid, stop, timeout_s=600: True)
    commands = []

    def fake_ssm(iid, command, timeout_s, *, bucket, prefix, out_file, stop):
        commands.append((iid, prefix.split("/ssm/")[1], command))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("ok\n")
        return "Failed" if prefix.endswith("m8i-2xlarge-r1/ssm/hostcheck") else "Success"

    monkeypatch.setattr(launch, "ssm_run", fake_ssm)

    def fake_pull(self, rid, rdir, say):
        if not rid.endswith("m8i-2xlarge-r1"):
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / "run.json").write_text('{"plan": {"complete": true}}')

    monkeypatch.setattr(launch.Campaign, "pull", fake_pull)
    stack = FakeStack()
    c = launch.Campaign(camp, plan, launch.Log(None), stack, "abc123")
    c.bucket, c.poll_s, c.start_gap_s = "bucket", 0.05, 0.0
    c.launch(free_vcpus=32)  # two runs at a time
    assert sorted(r for r, _, _ in stack.added) == sorted(stack.removed) == sorted(r["run"] for r in plan["runs"])
    assert c.status["m8i-2xlarge-r1"].startswith("failed: hostcheck ended Failed")
    assert [c.status[r] for r in ("m8i-2xlarge-r2", "m8i-4xlarge-r1", "m8i-4xlarge-r2")] == ["complete"] * 3
    labels = [lab for iid, lab, _ in commands if iid == "i-w-m8i-4xlarge-r1"]
    assert labels == ["cloud-init", "host-setup", "hostcheck", "run", "bundle"]
    run_cmd = next(cmd for iid, lab, cmd in commands if iid == "i-w-m8i-4xlarge-r1" and lab == "run")
    assert "SPEC_FILE=/var/lib/fleetkit/runs/nested-sizes-1/m8i-4xlarge-r1/spec.json" in run_cmd
    assert "SUPPORT_HEALTH_URL=http://10.0.0.9:8082" in run_cmd and "stage.sh run" in run_cmd
    hc = next(cmd for iid, lab, cmd in commands if iid == "i-w-m8i-4xlarge-r1" and lab == "hostcheck")
    b64 = hc.split("echo ")[1].split(" |")[0]
    sent = json.loads(base64.b64decode(b64))
    assert sent["run"] == "m8i-4xlarge-r1" and sent["spec"]["worker_host"]["instance_type"] == "m8i.4xlarge"
    assert launch.EXPAND.same(sent["spec"], next(r["spec"] for r in plan["runs"] if r["run"] == "m8i-4xlarge-r1"))
