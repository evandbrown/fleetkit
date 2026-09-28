"""The launcher's decisions, offline: what it refuses, the order runs start in under the quota, what
a Terraform plan may change, the support host's health rules, and how an earlier attempt at a run
is kept out of a new one's results (against a fake `aws`).

    harness/.venv/bin/python -m pytest -q experiments/launcher/tests
"""
from __future__ import annotations

import base64
import datetime
import fnmatch
import json
import os
import subprocess
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
    args = {"spent": 10.0, "free_vcpus": 32, "head_pushed": True, "earlier": {}}
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


def test_examples_metal_quota_head_and_earlier_attempts_are_refused():
    plan = plan_of("hv-host-1", quota=1024)
    plan["runs"][-1] = {**plan["runs"][-1], "worker_instance_type": "c8i.metal-48xl"}
    earlier = {"firecracker-nested-r1": "results/hv-host-1/firecracker-nested-r1/ holds 3 files"}
    why = launch.refusals(CAMPAIGNS / "examples" / "hv-host-1.json", plan,
                          **ok(free_vcpus=32, head_pushed=False, earlier=earlier))
    text = "\n".join(why)
    assert "written, not approved" in text
    assert "c8i.metal-48xl isn't allowed by the account's instance-type guardrail" in text
    assert "needs 208 vCPUs with its support host; 32 are free" in text
    assert "HEAD isn't on any remote branch" in text
    assert ("firecracker-nested-r1: an earlier attempt is in the way (results/hv-host-1/firecracker-nested-r1/ "
            "holds 3 files)") in text and "Pass --replace" in text
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
    """The campaign stack: ``held`` is the runs whose hosts its state holds. Its destroy refuses, as
    the real one's check does, to take another run's hosts with the whole state."""

    def __init__(self, held: set | None = None):
        self.added, self.removed, self.destroyed = [], [], []
        self.held = held if held is not None else set()
        self.lock = __import__("threading").Lock()

    def add(self, run, worker, support):
        with self.lock:
            self.added.append((run, worker, support))
            self.held.add(run)
        return {"worker_id": f"i-w-{run}", "support_id": f"i-s-{run}", "support_ip": "10.0.0.9"}

    def remove(self, run):
        with self.lock:
            self.removed.append(run)
            self.held.discard(run)

    def state_runs(self):
        return set(self.held)

    def destroy(self, runs, everything=False):
        self.destroyed.append((sorted(runs), everything))
        if everything and self.held - set(runs):
            raise launch.StepFailed("terraform (destroy): unexpected delete of another run's host; not applied")
        self.held -= set(runs)
        return [(a, "delete") for r in sorted(runs) for a in launch.run_addresses(r)]


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

    def fake_pull(self, rid, rdir, say, since):
        if not rid.endswith("m8i-2xlarge-r1"):
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / "run.json").write_text('{"plan": {"complete": true}}')

    monkeypatch.setattr(launch.Campaign, "pull", fake_pull)
    monkeypatch.setattr(launch, "aws", FakeAws())  # an empty bucket: no earlier attempt
    stack = FakeStack()
    c = launch.Campaign(camp, plan, launch.Log(None), stack, "abc123")
    c.bucket, c.poll_s, c.start_gap_s = FakeAws.BUCKET, 0.05, 0.0
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


# ---- an earlier attempt at a run: refused, or archived with --replace; the pull takes only this one's --

UTC = datetime.timezone.utc


def done(code: int = 0, err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, "", err)


class FakeAws:
    """The `aws` commands the launcher sends to STS, EC2 and S3, against one in-memory bucket of
    {key: (body, LastModified)} and a list of instances. S3 stamps what it writes (a copy too) by
    its own clock, ``s3_clock`` off the launcher's. `s3 cp --recursive` skips folder markers, and
    `s3 sync` applies --exclude/--include, the way the CLI does: fnmatch on bucket/key, each pattern
    joined to the source's bucket/prefix, the last match winning. ``copy`` is "ok", "fails" (exit 1
    after half the objects) or "drops" (exit 0, one object not copied); ``delete_errors`` is how
    many keys of each delete-objects batch fail; ``on_delete`` runs just before a delete (something
    else writing to the bucket)."""
    BUCKET = "fleetkit-results-test"

    def __init__(self, copy: str = "ok", delete_errors: int = 0, s3_clock=datetime.timedelta(0)):
        self.objects: dict[str, tuple[bytes, datetime.datetime]] = {}
        self.calls: list[tuple] = []
        self.copy, self.delete_errors, self.s3_clock = copy, delete_errors, s3_clock
        self.instances: list[dict] = []
        self.on_delete = None

    def now(self) -> datetime.datetime:
        return datetime.datetime.now(UTC) + self.s3_clock

    def put(self, key: str, body: bytes = b"x", at: datetime.datetime | None = None) -> None:
        self.objects[key] = (body, at or self.now())

    def instance(self, iid: str, run: str, state: str = "running", campaign: str = "nested-sizes-1") -> None:
        self.instances.append({"id": iid, "run": run, "state": state, "campaign": campaign})

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))

    def _split(self, url: str) -> str:
        bucket, _, key = url[len("s3://"):].partition("/")
        assert bucket == self.BUCKET, url
        return key

    def __call__(self, *args, profile=None, check=True, parse=True):
        self.calls.append(args)
        arg = lambda flag: args[args.index(flag) + 1]  # noqa: E731
        if args[:2] == ("sts", "get-caller-identity"):
            return "test"
        if args[:2] == ("s3api", "list-objects-v2"):
            assert arg("--bucket") == self.BUCKET
            return [{"Key": k, "LastModified": self.objects[k][1].isoformat(timespec="seconds"),
                     "Size": len(self.objects[k][0])} for k in self.keys(arg("--prefix"))] or None
        if args[:2] == ("s3", "cp"):
            assert "--recursive" in args
            src, dst = (self._split(a) for a in args if a.startswith("s3://"))
            keys = [k for k in self.keys(src) if not (k.endswith("/") and not self.objects[k][0])]
            keys = keys[:len(keys) // 2] if self.copy == "fails" else keys[1:] if self.copy == "drops" else keys
            for k in keys:
                self.put(dst + k[len(src):], self.objects[k][0])
            return done(1, "copy failed: SlowDown") if self.copy == "fails" else done()
        if args[:2] == ("s3api", "delete-objects"):
            assert arg("--bucket") == self.BUCKET
            if self.on_delete:
                self.on_delete()
            objs = json.loads(arg("--delete"))["Objects"]
            failed, gone = objs[:self.delete_errors], objs[self.delete_errors:]
            for o in gone:
                self.objects.pop(o["Key"], None)
            return {"Errors": [{"Key": o["Key"], "Code": "AccessDenied"} for o in failed]} if failed else None
        if args[:2] == ("ec2", "describe-instances"):
            assert arg("--query") == launch.INSTANCES_QUERY
            want = {}
            for f in args[args.index("--filters") + 1:args.index("--query")]:
                name, _, values = f.partition(",Values=")
                want[name[len("Name="):]] = values.split(",")
            out = []
            for i in self.instances:
                have = {"tag:Campaign": i["campaign"], "tag:Run": i["run"], "instance-state-name": i["state"]}
                if all(have[k] in v for k, v in want.items()):
                    out.append([i["id"], i["state"], i["run"]])
            return out
        if args[:2] == ("ec2", "terminate-instances"):
            ids = args[args.index("--instance-ids") + 1:]
            for i in self.instances:
                if i["id"] in ids:
                    i["state"] = "terminated"
            return done()
        if args[:2] == ("s3", "sync"):
            src, dest, filters = args[3], Path(args[4]), args[5:]
            root, prefix = src[len("s3://"):], self._split(src)
            for k in self.keys(prefix):
                keep = True
                for flag, pattern in zip(filters[::2], filters[1::2]):
                    if fnmatch.fnmatch(f"{self.BUCKET}/{k}", os.path.join(root, pattern)):
                        keep = flag == "--include"
                if keep:
                    (dest / k[len(prefix):]).parent.mkdir(parents=True, exist_ok=True)
                    (dest / k[len(prefix):]).write_bytes(self.objects[k][0])
            return done()
        raise AssertionError(f"the launcher sent an aws command the fake doesn't know: {args}")

    def sent(self, *head: str) -> list[int]:
        """Where in the call order commands starting with ``head`` were sent."""
        return [i for i, c in enumerate(self.calls) if c[:len(head)] == head]


def files_under(d: Path) -> list[str]:
    return sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()) if d.exists() else []


def test_earlier_attempt_names_what_is_in_the_way():
    assert launch.earlier_attempt("c", "r1", 0, []) is None
    assert launch.earlier_attempt("c", "r1", 3, []) == "results/c/r1/ holds 3 files"
    assert launch.earlier_attempt("c", "r1", 0, [{"Key": "runs/c/r1/a"}]) == \
        "runs/c/r1/ in the results bucket holds 1 objects"
    assert " and " in launch.earlier_attempt("c", "r1", 2, [{"Key": "runs/c/r1/a"}])


def test_replace_lifts_only_the_earlier_attempt_refusal():
    plan = plan_of("nested-sizes-1")
    earlier = {"m8i-2xlarge-r1": "runs/nested-sizes-1/m8i-2xlarge-r1/ in the results bucket holds 4 objects"}
    why = launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan, **ok(earlier=earlier))
    assert len(why) == 1 and why[0].startswith("m8i-2xlarge-r1: an earlier attempt is in the way") \
        and "Pass --replace" in why[0]
    assert launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan, **ok(earlier=earlier, replace=True)) == []
    why = launch.refusals(CAMPAIGNS / "nested-sizes-1.json", plan, **ok(earlier=earlier, replace=True, spent=None))
    assert why and "spend so far is unknown" in why[0]
    assert "can't be named superseded" in launch.refusals(CAMPAIGNS / "x.json", {**plan, "campaign": "superseded"},
                                                          **ok())[0]


def test_glob_escape_matches_its_key_alone():
    for key in ("trials/d4-t2/trial.json", "screens/a[1]*?.jpg", "odd]name"):
        assert fnmatch.fnmatch(key, launch.glob_escape(key))
    assert not fnmatch.fnmatch("screens/a1x.jpg", launch.glob_escape("screens/a[1]*.jpg"))
    assert not fnmatch.fnmatch("screens/a1.jpg", launch.glob_escape("screens/a?.jpg"))


def test_split_by_time_keeps_what_is_stamped_at_or_after_since():
    since = datetime.datetime(2026, 9, 28, 1, 0, 0, tzinfo=UTC)
    objs = [{"Key": "a", "LastModified": "2026-09-28T00:59:59+00:00"},
            {"Key": "b", "LastModified": "2026-09-28T01:00:00+00:00"},
            {"Key": "c", "LastModified": "2026-09-28T02:00:00.000Z"}]
    new, old = launch.split_by_time(objs, since)
    assert [o["Key"] for o in new] == ["b", "c"] and [o["Key"] for o in old] == ["a"]


# -- the launch as a whole: the check comes before any host --

@pytest.fixture
def world(tmp_path, monkeypatch):
    """main() against a fake account: logged in, the member profile for the account the campaign
    stack uses, 64 vCPUs free, HEAD pushed, nothing uncommitted, and a campaign stack that records
    what it's asked to do; its state holds the runs in ``world.held``, and ``world.on_init`` runs
    when it's initialised."""
    fake = FakeAws()
    monkeypatch.setattr(launch, "aws", fake)
    monkeypatch.setattr(launch, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(launch.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(launch, "sh", lambda args, **kw: done())  # the two `aws login` checks
    monkeypatch.setattr(launch, "stack_account", lambda: "test")  # the fake STS says "test" too
    monkeypatch.setattr(launch, "vcpu_quota", lambda: 64)
    monkeypatch.setattr(launch, "vcpus_running", lambda tag_filters=None: 0)
    monkeypatch.setattr(launch, "head_pushed", lambda: True)
    monkeypatch.setattr(launch, "uncommitted", lambda: [])
    monkeypatch.setattr(launch, "head_commit", lambda: "abc123def456")
    stacks = []
    fake.held, fake.on_init = set(), None

    class RecordingStack(FakeStack):
        def __init__(self, *args):
            super().__init__(held=fake.held)
            self.did = []
            stacks.append(self)

        def init(self):
            self.did.append("init")
            if fake.on_init:
                fake.on_init()

        def plan(self, runs, lock=True):
            self.did.append("plan")
            return None, [], "Plan: 2 to add, 0 to change, 0 to destroy."

        def setup(self):
            self.did.append("setup")

        def bucket(self):
            return FakeAws.BUCKET

    monkeypatch.setattr(launch, "Stack", RecordingStack)
    monkeypatch.setattr(launch.Campaign, "settle_s", 0.0)
    fake.stacks, fake.Stack = stacks, RecordingStack
    return fake


RUN = "m8i-2xlarge-r1"
ARGS = [str(CAMPAIGNS / "nested-sizes-1.json"), "--runs", RUN, "--spent", "10"]


@pytest.mark.parametrize("where", ["local", "bucket"])
def test_a_launch_over_an_earlier_attempt_is_refused_before_any_host(world, capsys, where):
    old = datetime.datetime(2026, 9, 27, 18, 0, tzinfo=UTC)
    if where == "local":
        (launch.RESULTS / "nested-sizes-1" / RUN / "trials" / "d4-t2").mkdir(parents=True)
        (launch.RESULTS / "nested-sizes-1" / RUN / "trials" / "d4-t2" / "trial.json").write_text("{}")
    else:
        world.put(f"runs/nested-sizes-1/{RUN}/run/trials/d4-t2/trial.json", b"{}", old)
    world.put(f"runs/nested-sizes-1/{RUN}0/run/run.json", b"{}", old)  # another run's prefix: not in the way
    assert launch.main(ARGS) == 1
    err = capsys.readouterr().err
    held = f"results/nested-sizes-1/{RUN}/ holds 1 files" if where == "local" else \
        f"runs/nested-sizes-1/{RUN}/ in the results bucket holds 1 objects"
    assert f"{RUN}: an earlier attempt is in the way ({held})" in err and "Pass --replace" in err
    assert all(s.did == [] and s.added == [] for s in world.stacks)  # no terraform at all, so no host
    assert world.sent("s3", "cp") == world.sent("s3api", "delete-objects") == []
    assert FakeAws.BUCKET not in err


def test_the_dry_run_reports_what_replace_would_archive(world, capsys):
    old = datetime.datetime(2026, 9, 27, 18, 0, tzinfo=UTC)
    rdir = launch.RESULTS / "nested-sizes-1" / RUN
    rdir.mkdir(parents=True)
    (rdir / "run.json").write_text("{}")
    for k in ("run/run.json", "run/trials/d4-t2/trial.json", "support/support-metrics.csv"):
        world.put(f"runs/nested-sizes-1/{RUN}/{k}", b"0123456789", old)
    before = dict(world.objects)
    assert launch.main(ARGS + ["--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "With --replace, a launch would archive" in out
    assert (f"{RUN}: results/nested-sizes-1/{RUN}/ (1 files) -> results/superseded/nested-sizes-1/{RUN}-<UTC>/"
            in out)
    assert (f"{RUN}: runs/nested-sizes-1/{RUN}/ (3 objects, 0.0 MB) -> superseded/nested-sizes-1/{RUN}-<UTC>/ "
            "in the results bucket") in out
    assert "A launch would be refused" in out and "Pass --replace" in out
    assert launch.main(ARGS + ["--dry-run", "--replace"]) == 0
    out = capsys.readouterr().out
    assert "--replace archives each earlier attempt" in out and "A launch would go ahead." in out
    assert world.objects == before and files_under(rdir) == ["run.json"]  # the dry run changed nothing
    assert world.sent("s3", "cp") == world.sent("s3api", "delete-objects") == []
    assert FakeAws.BUCKET not in out


# -- one run: the way is cleared (or not) just before its hosts are created --

def one_run(monkeypatch, tmp_path, fake: FakeAws, *, replace: bool):
    """A Campaign of one run whose hosts are faked; its bundle writes this attempt's run to the bucket."""
    camp = launch.EXPAND.read_json(CAMPAIGNS / "nested-sizes-1.json")
    plan = plan_of("nested-sizes-1")
    plan = {**plan, "runs": [r for r in plan["runs"] if r["run"] == RUN], "waves": [[RUN]]}
    monkeypatch.setattr(launch, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(launch, "aws", fake)
    monkeypatch.setattr(launch, "ssm_online", lambda iid, stop, timeout_s=600: True)

    def fake_ssm(iid, command, timeout_s, *, bucket, prefix, out_file, stop):
        rid, label = prefix.split("/ssm/")
        fake.put(f"{prefix}/{iid}/awsrunShellScript/0.awsrunShellScript/stdout", b"ok")
        if label == "bundle":
            fake.put(f"{rid}/run/run.json", b'{"plan": {"complete": true}}')
            fake.put(f"{rid}/run/trials/d1-t1/trial.json", b"{}")
        if label == "support-sync":
            fake.put(f"{rid}/support/support-metrics.csv", b"ts\n")
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("ok\n")
        return "Success"

    monkeypatch.setattr(launch, "ssm_run", fake_ssm)
    stack = FakeStack()
    add = stack.add
    # records what the run's prefix holds at the moment its hosts are created
    stack.add = lambda *a: fake.calls.append(("stack.add", *fake.keys(f"runs/nested-sizes-1/{RUN}/"))) or add(*a)
    c = launch.Campaign(camp, plan, launch.Log(None), stack, "abc123", replace=replace)
    c.bucket = FakeAws.BUCKET
    return c, stack, launch.RESULTS / "nested-sizes-1" / RUN


def earlier(fake: FakeAws, rdir: Path) -> None:
    """What the first attempt left: its pulled results and its prefix in the bucket."""
    old = datetime.datetime.now(UTC) - datetime.timedelta(hours=2)
    for k in ("run/run.json", "run/trials/d4-t2/trial.json", "run/trials/d8-t3/trial.json",
              "support/support-metrics.csv", "ssm/run/i-old/stdout"):
        fake.put(f"runs/nested-sizes-1/{RUN}/{k}", b"old", old)
    (rdir / "trials" / "d4-t2").mkdir(parents=True)
    (rdir / "trials" / "d4-t2" / "trial.json").write_text("old")
    (rdir / "run.json").write_text('{"plan": {"complete": true}}')


@pytest.mark.parametrize("where", ["local", "bucket"])
def test_a_run_over_an_earlier_attempt_is_refused_without_replace(monkeypatch, tmp_path, where):
    fake = FakeAws()
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=False)
    if where == "local":
        rdir.mkdir(parents=True)
        (rdir / "run.json").write_text('{"plan": {"complete": true}}')
    else:
        fake.put(f"runs/nested-sizes-1/{RUN}/run/run.json", b"{}")
    before = (dict(fake.objects), files_under(rdir))
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN].startswith("refused: an earlier attempt is in the way")
    assert c.status[RUN].endswith("(no results)")  # the earlier attempt's run.json isn't this one's
    assert stack.added == stack.removed == []
    assert (dict(fake.objects), files_under(rdir)) == before
    assert fake.sent("s3", "sync") == fake.sent("s3", "cp") == fake.sent("s3api", "delete-objects") == []


def test_replace_archives_the_earlier_attempt_then_launches(monkeypatch, tmp_path, capsys):
    fake = FakeAws()
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=True)
    earlier(fake, rdir)
    old_keys = fake.keys(f"runs/nested-sizes-1/{RUN}/")
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN] == "complete"
    # the order: every copy, then the local move and the deletes, then the hosts
    (cp,), (delete,), (add,) = fake.sent("s3", "cp"), fake.sent("s3api", "delete-objects"), fake.sent("stack.add")
    assert cp < delete < add
    archived = [p for p in (tmp_path / "results" / "superseded" / "nested-sizes-1").iterdir()]
    assert len(archived) == 1 and archived[0].name.startswith(f"{RUN}-")
    stamp = archived[0].name[len(RUN) + 1:]
    assert len(stamp) == 16 and stamp.endswith("Z")
    assert files_under(archived[0]) == ["run.json", "trials/d4-t2/trial.json"]
    copies = fake.keys(f"superseded/nested-sizes-1/{archived[0].name}/")
    assert [k.split(archived[0].name + "/")[1] for k in copies] == \
        [k.split(RUN + "/")[1] for k in old_keys]
    assert fake.calls[add] == ("stack.add",)  # runs/<rid>/ was empty when the hosts were created
    assert not {k for k in old_keys if "/d4-t2/" in k or "/d8-t3/" in k or "/i-old/" in k} & set(fake.keys())
    # the new attempt's results hold only what it wrote
    assert "trials/d4-t2/trial.json" not in files_under(rdir)
    assert {"run.json", "trials/d1-t1/trial.json", "support/support-metrics.csv"} <= set(files_under(rdir))
    assert (rdir / "run.json").read_text() == '{"plan": {"complete": true}}'
    out = capsys.readouterr().out
    assert f"archive: copied the 5 objects under runs/nested-sizes-1/{RUN}/ to superseded/" in out
    assert f"archive: moved results/nested-sizes-1/{RUN}/ to results/superseded/nested-sizes-1/{RUN}-" in out
    assert "archive: deleted the 5 originals" in out
    assert FakeAws.BUCKET not in out


@pytest.mark.parametrize("copy", ["fails", "drops"])
def test_a_failed_copy_stops_the_run_and_deletes_nothing(monkeypatch, tmp_path, copy):
    fake = FakeAws(copy=copy)
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=True)
    earlier(fake, rdir)
    old_keys, old_files = fake.keys(f"runs/nested-sizes-1/{RUN}/"), files_under(rdir)
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN].startswith("failed: ") and "nothing was deleted" in c.status[RUN]
    assert fake.sent("s3api", "delete-objects") == [] and stack.added == []
    assert fake.keys(f"runs/nested-sizes-1/{RUN}/") == old_keys  # every original is still there
    assert files_under(rdir) == old_files  # and the local results weren't moved
    assert not (tmp_path / "results" / "superseded").exists()


def test_the_pull_leaves_out_objects_older_than_the_launch(monkeypatch, tmp_path, capsys):
    fake = FakeAws()
    monkeypatch.setattr(launch, "aws", fake)
    c = launch.Campaign({"name": "c", "shutdown_after_minutes": 60}, {}, launch.Log(None), FakeStack(), "abc")
    c.bucket = FakeAws.BUCKET
    launched = datetime.datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
    at = lambda **kw: launched + datetime.timedelta(**kw)  # noqa: E731
    for key, when in (("run/trials/d4-t2/trial.json", at(hours=-2)),     # the earlier attempt
                      ("run/screens/a1x.jpg", at(hours=-2)),             # matched by a[1]*.jpg unescaped
                      ("ssm/run/i-old/stdout", at(hours=-2)),
                      ("support/old.csv", at(minutes=-5)),
                      ("run/run.json", at(seconds=-30)),                 # inside the clock-skew margin
                      ("run/trials/d1-t1/trial.json", at(minutes=40)),
                      ("run/screens/a[1]*.jpg", at(minutes=40)),
                      ("support/support-metrics.csv", at(minutes=41)),
                      ("ssm/run/i-new/stdout", at(minutes=39))):
        fake.put(f"runs/c/r1/{key}", b"x", when)
    fake.put("runs/c/r10/run/run.json", b"x", at(minutes=40))  # another run's prefix
    rdir = tmp_path / "c" / "r1"
    c.pull("c/r1", rdir, lambda m: c.log(m, "r1"), since=launched - datetime.timedelta(seconds=launch.CLOCK_SKEW_S))
    assert files_under(rdir) == ["ops/host/ssm/run/i-new/stdout", "run.json", "screens/a[1]*.jpg",
                                 "support/support-metrics.csv", "trials/d1-t1/trial.json"]
    out = capsys.readouterr().out
    assert "pull: left out 4 objects under runs/c/r1/ stamped before this attempt" in out
    assert "pulled 5 objects into results/c/r1/" in out


def test_the_pull_takes_nothing_when_the_bucket_cannot_be_listed(monkeypatch, tmp_path, capsys):
    def no_listing(*args, **kw):
        if args[:2] == ("s3api", "list-objects-v2"):
            raise launch.StepFailed(f"aws s3api list-objects-v2: exit 254: AccessDenied on {FakeAws.BUCKET}")
        raise AssertionError(f"nothing else should be sent: {args}")

    monkeypatch.setattr(launch, "aws", no_listing)
    log = launch.Log(None)
    log.hide(FakeAws.BUCKET)
    c = launch.Campaign({"name": "c", "shutdown_after_minutes": 60}, {}, log, FakeStack(), "abc")
    c.bucket = FakeAws.BUCKET
    c.pull("c/r1", tmp_path / "r1", lambda m: c.log(m, "r1"), since=datetime.datetime.now(UTC))
    out = capsys.readouterr().out
    assert "nothing was pulled" in out and "<results bucket>" in out and FakeAws.BUCKET not in out
    assert not (tmp_path / "r1").exists()


# -- the archive's edges: a partial delete, a failed move, a folder marker, something still writing --

PREFIX = f"runs/nested-sizes-1/{RUN}/"


def test_a_partial_delete_stops_the_run_with_every_original_archived(monkeypatch, tmp_path):
    fake = FakeAws(delete_errors=1)
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=True)
    earlier(fake, rdir)
    old = {k[len(PREFIX):]: fake.objects[k][0] for k in fake.keys(PREFIX)}
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN].startswith("failed: 4 of 5 originals were deleted") and "has its copy" in c.status[RUN]
    assert stack.added == []
    (archived,) = (tmp_path / "results" / "superseded" / "nested-sizes-1").iterdir()
    dst = f"superseded/nested-sizes-1/{archived.name}/"
    assert {k[len(dst):]: fake.objects[k][0] for k in fake.keys(dst)} == old  # every original has its copy
    assert len(fake.keys(PREFIX)) == 1  # and the one that failed is still where it was


@pytest.mark.parametrize("how", ["exists", "oserror"])
def test_a_failed_local_move_stops_the_run_and_deletes_nothing(monkeypatch, tmp_path, how):
    fake = FakeAws()
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=True)
    earlier(fake, rdir)
    monkeypatch.setattr(launch, "utc_stamp", lambda now=None: "20260927T184512Z")
    dest = tmp_path / "results" / "superseded" / "nested-sizes-1" / f"{RUN}-20260927T184512Z"
    if how == "exists":
        dest.mkdir(parents=True)
    else:
        def full(src, dst):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(launch.shutil, "move", full)
    old_keys, old_files = fake.keys(PREFIX), files_under(rdir)
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN].startswith("failed: ") and "nothing was deleted" in c.status[RUN]
    assert ("already exists" if how == "exists" else "No space left") in c.status[RUN]
    assert fake.sent("s3api", "delete-objects") == [] and stack.added == []
    assert fake.keys(PREFIX) == old_keys and files_under(rdir) == old_files


def test_a_folder_marker_does_not_block_replace(monkeypatch, tmp_path, capsys):
    """`aws s3 cp --recursive` never copies a zero-byte key ending in / (the fake skips it too), so
    the copy check leaves it out; it's deleted with the rest."""
    fake = FakeAws()
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=True)
    earlier(fake, rdir)
    fake.put(f"{PREFIX}run/", b"", datetime.datetime.now(UTC) - datetime.timedelta(hours=2))
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN] == "complete"
    assert f"{PREFIX}run/" not in fake.objects
    out = capsys.readouterr().out
    assert "copied the 5 objects" in out and "(1 empty folder markers need no copy)" in out
    assert "deleted the 6 originals" in out


def test_something_still_writing_after_the_archive_refuses_the_run(monkeypatch, tmp_path):
    fake = FakeAws()
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=True)
    earlier(fake, rdir)
    late = f"{PREFIX}run/trials/d8-t3/late.json"
    fake.on_delete = lambda: fake.put(late, b"the earlier attempt, written late")
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN].startswith(f"refused: {PREFIX} holds 1 objects again after the archive "
                                    "(first: run/trials/d8-t3/late.json")
    assert stack.added == [] and fake.keys(PREFIX) == [late]  # kept, and nothing pulled
    assert "trials/d8-t3/late.json" not in files_under(rdir)


def test_a_run_whose_host_is_still_there_is_refused(monkeypatch, tmp_path):
    fake = FakeAws()
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=True)
    earlier(fake, rdir)
    fake.instance("i-old", RUN)                   # the earlier attempt's worker host, still up
    fake.instance("i-sib", "m8i-2xlarge-r2")      # another run's: not in the way
    fake.instance("i-gone", RUN, "terminated")    # gone
    before = (dict(fake.objects), files_under(rdir))
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN].startswith(f"refused: an instance tagged with it is still there ({RUN}: i-old running)")
    assert stack.added == [] and (dict(fake.objects), files_under(rdir)) == before
    assert fake.sent("s3", "cp") == fake.sent("s3api", "delete-objects") == []


def test_with_replace_the_pull_cutoff_is_the_buckets_own_clock(monkeypatch, tmp_path):
    """The launcher's clock 10 minutes fast: its launch time less the margin would leave out all of
    this attempt; the bucket's stamp on the archive copies doesn't."""
    fake = FakeAws(s3_clock=-datetime.timedelta(minutes=10))
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=True)
    earlier(fake, rdir)
    c.carry_out(c.plan["runs"][0])
    assert c.status[RUN] == "complete"
    got = set(files_under(rdir))
    assert {"run.json", "trials/d1-t1/trial.json", "support/support-metrics.csv"} <= got
    assert "trials/d4-t2/trial.json" not in got


# -- the pull: only into a directory holding nothing but this attempt's ops, and it never raises --

def test_the_pull_takes_nothing_into_a_directory_it_did_not_write(monkeypatch, tmp_path, capsys):
    fake = FakeAws()
    monkeypatch.setattr(launch, "aws", fake)
    c = launch.Campaign({"name": "c", "shutdown_after_minutes": 60}, {}, launch.Log(None), FakeStack(), "abc")
    c.bucket = FakeAws.BUCKET
    rdir = tmp_path / "c" / "r1"
    (rdir / "ops").mkdir(parents=True)
    (rdir / "ops" / "run.txt").write_text("ok\n")  # this attempt's
    (rdir / "trials" / "d4-t2").mkdir(parents=True)
    (rdir / "trials" / "d4-t2" / "trial.json").write_text("old")  # an earlier one's
    fake.put("runs/c/r1/run/run.json", b"new")
    since = datetime.datetime.now(UTC) - datetime.timedelta(hours=1)
    c.pull("c/r1", rdir, lambda m: c.log(m, "r1"), since=since)
    assert files_under(rdir) == ["ops/run.txt", "trials/d4-t2/trial.json"] and fake.sent("s3", "sync") == []
    assert "already holds 1 files this attempt didn't write (e.g. trials/d4-t2/trial.json)" in capsys.readouterr().out
    (rdir / "trials" / "d4-t2" / "trial.json").unlink()
    c.pull("c/r1", rdir, lambda m: c.log(m, "r1"), since=since)
    assert files_under(rdir) == ["ops/run.txt", "run.json"]


def test_a_pull_that_fails_still_ends_the_run(monkeypatch, tmp_path, capsys):
    fake = FakeAws()
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=False)

    def unreadable(objects, since):
        raise ValueError("Invalid isoformat string: 'yesterday'")

    monkeypatch.setattr(launch, "split_by_time", unreadable)
    c.carry_out(c.plan["runs"][0])
    assert stack.removed == [RUN] and c.status[RUN] == "no results"
    out = capsys.readouterr().out
    assert "pull: failed (ValueError: Invalid isoformat string" in out and f"[{RUN}] ended: no results" in out


# -- several runs, and several launchers of one campaign --

def test_a_launch_of_several_runs_refuses_only_the_run_in_the_way(monkeypatch, tmp_path):
    fake = FakeAws()
    c, stack, rdir = one_run(monkeypatch, tmp_path, fake, replace=False)
    two = [RUN, "m8i-2xlarge-r2"]
    plan = plan_of("nested-sizes-1")
    c.plan = {**plan, "runs": [r for r in plan["runs"] if r["run"] in two], "waves": [two]}
    fake.put(f"{PREFIX}run/run.json", b"{}")
    c.poll_s, c.start_gap_s = 0.01, 0.0
    c.launch(free_vcpus=64)
    assert c.status[RUN].startswith("refused: an earlier attempt is in the way")
    assert c.status["m8i-2xlarge-r2"] == "complete"
    assert [r for r, _, _ in stack.added] == ["m8i-2xlarge-r2"] and c.started == {"m8i-2xlarge-r2"}
    assert "run.json" in files_under(tmp_path / "results" / "nested-sizes-1" / "m8i-2xlarge-r2")
    assert fake.keys(PREFIX) == [f"{PREFIX}run/run.json"]  # the refused run's prefix is untouched


def test_the_sweep_leaves_another_launchers_hosts_alone(monkeypatch):
    fake = FakeAws()
    monkeypatch.setattr(launch, "aws", fake)
    monkeypatch.setattr(launch.Campaign, "settle_s", 0.0)
    stack = FakeStack(held={"ours-r1", "theirs-r1"})
    c = launch.Campaign({"name": "nested-sizes-1", "shutdown_after_minutes": 60}, {}, launch.Log(None), stack, "abc")
    c.started = {"ours-r1"}
    fake.instance("i-ours", "ours-r1")
    fake.instance("i-theirs", "theirs-r1")
    assert c.sweep() == []
    assert stack.destroyed == [(["ours-r1"], False)]  # only its hosts: theirs and the security group stay
    assert stack.held == {"theirs-r1"}
    (t,) = fake.sent("ec2", "terminate-instances")
    assert fake.calls[t][3:] == ("i-ours",)
    assert [i["state"] for i in fake.instances] == ["terminated", "running"]
    for i in fake.sent("ec2", "describe-instances"):
        assert "Name=tag:Run,Values=ours-r1" in fake.calls[i]
    # a state holding only this launcher's runs goes whole, the security group with it
    alone = FakeStack(held={"ours-r1"})
    c = launch.Campaign({"name": "nested-sizes-1", "shutdown_after_minutes": 60}, {}, launch.Log(None), alone, "abc")
    c.started = {"ours-r1"}
    assert c.sweep() == [] and alone.destroyed == [(["ours-r1"], True)]
    # a launcher that started nothing destroys nothing of another's
    idle = FakeStack(held={"theirs-r1"})
    c = launch.Campaign({"name": "nested-sizes-1", "shutdown_after_minutes": 60}, {}, launch.Log(None), idle, "abc")
    assert c.sweep() == [] and idle.destroyed == [([], False)] and idle.held == {"theirs-r1"}


def test_replace_needs_runs_to_name_what_it_archives(world, capsys):
    with pytest.raises(SystemExit) as e:
        launch.main([str(CAMPAIGNS / "nested-sizes-1.json"), "--spent", "10", "--dry-run", "--replace"])
    assert e.value.code == 2 and "name them with --runs" in capsys.readouterr().err
    assert world.calls == [] and world.stacks == []


@pytest.mark.parametrize("dry", [True, False])
def test_a_launch_is_refused_while_instances_of_the_campaign_are_still_there(world, capsys, dry):
    world.instance("i-other", "m8i-4xlarge-r1")  # another launcher's run of the campaign
    world.instance("i-elsewhere", RUN, campaign="nested-hv-1")  # another campaign's: not in the way
    assert launch.main(ARGS + (["--dry-run"] if dry else [])) == 1
    cap = capsys.readouterr()
    assert "instances of nested-sizes-1 are still there (m8i-4xlarge-r1: i-other running)" in cap.out + cap.err
    assert "i-elsewhere" not in cap.out + cap.err
    assert all(s.added == [] and "setup" not in s.did for s in world.stacks)
    assert world.sent("ec2", "terminate-instances") == []


def test_hosts_another_launcher_just_created_refuse_the_launch_before_setup(world, capsys, monkeypatch):
    launched = []
    monkeypatch.setattr(launch.Campaign, "launch", lambda self, free: launched.append(free))

    def another_launcher_starts():  # between this launcher's checks and its first change to the state
        world.held.add("m8i-4xlarge-r1")
        world.instance("i-other", "m8i-4xlarge-r1", "pending")

    world.on_init = another_launcher_starts
    assert launch.main(ARGS) == 1
    (stack,) = world.stacks
    assert stack.did == ["init"] and launched == []
    assert stack.destroyed == [([], False)] and world.held == {"m8i-4xlarge-r1"}  # nothing of theirs destroyed
    assert world.sent("ec2", "terminate-instances") == []
    out = capsys.readouterr().out
    assert ("REFUSED: the campaign's Terraform state holds hosts that are still there "
            "(m8i-4xlarge-r1: i-other pending)") in out


def test_hosts_left_in_the_state_by_a_launcher_that_stopped_are_cleared_first(world, capsys, monkeypatch):
    launched = []
    monkeypatch.setattr(launch.Campaign, "launch", lambda self, free: launched.append(free))
    world.held.add("m8i-4xlarge-r1")
    world.instance("i-gone", "m8i-4xlarge-r1", "terminated")
    assert launch.main(ARGS + ["--dry-run"]) == 0
    assert "none of them still there; a launch clears them from it first" in capsys.readouterr().out
    assert launch.main(ARGS) == 1  # (the faked launch ran no run)
    stack = world.stacks[-1]
    assert stack.did == ["init", "setup"] and launched == [64]
    assert stack.destroyed[0] == (["m8i-4xlarge-r1"], True) and world.held == set()
    assert "clearing them from it" in capsys.readouterr().out


@pytest.mark.parametrize("dry", [True, False])
@pytest.mark.parametrize("account", ["other", None])
def test_a_member_profile_for_another_account_is_refused(world, capsys, monkeypatch, dry, account):
    monkeypatch.setattr(launch, "stack_account", lambda: account)
    world.put(f"{PREFIX}run/run.json", b"{}")  # in the member profile's bucket, which isn't the hosts'
    assert launch.main(ARGS + (["--dry-run", "--replace"] if dry else [])) == 1
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert ("is for another account than the one the campaign stack creates hosts in" if account else
            "the account the campaign stack creates hosts in couldn't be read") in text
    assert world.sent("s3api", "list-objects-v2") == [] and "would archive" not in text
    assert "A launch would go ahead" not in text
    assert all(s.added == [] and "setup" not in s.did for s in world.stacks)


def test_nothing_is_launched_when_the_stack_names_another_bucket(world, capsys, monkeypatch):
    launched = []
    monkeypatch.setattr(launch.Campaign, "launch", lambda self, free: launched.append(free))
    monkeypatch.setattr(world.Stack, "bucket", lambda self: "fleetkit-results-other")
    assert launch.main(ARGS) == 1
    (stack,) = world.stacks
    assert launched == [] and stack.added == [] and stack.did == ["init", "setup"]
    assert stack.destroyed == [([], True)]  # the sweep takes the security group setup made
    out = capsys.readouterr().out
    assert "the campaign stack's results bucket isn't the one the launcher checked; not launching" in out
    assert "fleetkit-results-other" not in out


def test_the_stack_reads_its_hosts_and_destroys_only_what_it_is_asked_to(monkeypatch, tmp_path):
    """Stack.state_runs and Stack.destroy against a fake `terraform`."""
    monkeypatch.setattr(launch, "STACK", tmp_path)  # the plan's var file goes here
    sent, answer = [], {"state": (0, 'data.aws_caller_identity.member\naws_security_group.support\n'
                                     'aws_instance.worker["ours-r1"]\naws_instance.support["ours-r1"]\n'
                                     'aws_instance.worker["theirs-r1"]\n', ""), "changes": []}

    def tf(self, *args, check=True, timeout=1800):
        sent.append(args)
        if args[:2] == ("state", "list"):
            return subprocess.CompletedProcess(args, *answer["state"])
        if args[0] == "show":
            return subprocess.CompletedProcess(args, 0, json.dumps({"resource_changes": answer["changes"]}), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(launch.Stack, "tf", tf)
    s = launch.Stack("c", 60, "abc", launch.Log(None))
    s.data_dir.mkdir(parents=True)  # as init() does
    assert s.state_runs() == {"ours-r1", "theirs-r1"}
    answer["state"] = (1, "", "No state file was found!\n")
    assert s.state_runs() == set()  # a campaign never launched
    answer["state"] = (1, "", "Error: AccessDenied")
    with pytest.raises(launch.StepFailed):
        s.state_runs()

    def deletes(*addrs):
        return [{"address": a, "mode": "managed", "change": {"actions": ["delete"]}} for a in addrs]

    ours = launch.run_addresses("ours-r1")
    sent.clear()
    answer["changes"] = deletes(*ours)
    assert len(s.destroy({"ours-r1"})) == 2
    (plan,) = [a for a in sent if a[0] == "plan"]
    assert "-destroy" in plan and {a for a in plan if a.startswith("-target=")} == {f"-target={a}" for a in ours}
    assert [a[0] for a in sent].count("apply") == 1
    sent.clear()  # the whole state, but it would take another run's host: not applied
    answer["changes"] = deletes(*ours, 'aws_instance.worker["theirs-r1"]', "aws_security_group.support")
    with pytest.raises(launch.StepFailed, match="theirs-r1"):
        s.destroy({"ours-r1"}, everything=True)
    assert "apply" not in [a[0] for a in sent] and not [x for a in sent for x in a if x.startswith("-target")]
    answer["changes"] = deletes(*ours, "aws_security_group.support")
    assert len(s.destroy({"ours-r1"}, everything=True)) == 3
    sent.clear()
    assert s.destroy(set()) == [] and sent == []  # nothing asked for, nothing planned
