#!/usr/bin/env python3
"""Launch a campaign: one command from a campaign definition to results/<campaign>/.

    experiments/launch.sh CAMPAIGN.json [--dry-run] [--spent USD] [--runs RUN,RUN] [--quota N]
                                        [--replace (with --runs)]

1. Check. The campaign is expanded and checked by experiments/schema/expand.py. The launcher
   refuses it if it isn't valid; if it's under experiments/campaigns/examples/ (written, not
   approved); if its worst case is above the limit for one campaign, or would cross what's left of
   the project cap (spend read from Cost Explorer, or --spent when that can't be read); if a run
   needs more vCPUs than the quota leaves free; if a worker host isn't allowed by the account's
   instance-type guardrail; if HEAD isn't pushed or the files the hosts run have uncommitted
   changes (the hosts check out HEAD from GitHub); if the member-account profile isn't for the
   account the campaign stack creates hosts in (the bucket it checks wouldn't be the one the hosts
   write to); if any instance tagged with the campaign is still there, or the campaign's Terraform
   state holds hosts still there (another launcher of it is running: they would share one state,
   and each one's changes would destroy the other's hosts); or if an earlier attempt at one of its
   runs left anything in results/<campaign>/<run>/ or in the results bucket under
   runs/<campaign>/<run>/ (a new attempt would be pulled on top of it).
2. Plan. Runs start in the order of expand.py's waves, each as soon as its worker and support
   hosts' vCPUs fit under the live quota, less what's already running in the account.
3. Run. For each run: check again for an earlier attempt and for an instance tagged with the run
   (with --replace, archive the attempt: see below), create its own worker host and support host
   (Terraform, infra/experiments/campaign, one state per campaign, keyed by run name), set both up
   over SSM, carry out the run's spec (images/host/stage.sh run), bundle it, pull what this attempt
   wrote into results/<campaign>/<run>/ (only objects stamped since the run's launch, and only
   into a directory holding nothing else), and destroy its hosts as soon as it ends. One run
   failing doesn't stop the others. Every host also shuts itself down shutdown_after_minutes after
   boot.
4. Sweep. Destroy what's left of the runs this launcher started (and the campaign's support
   security group once no other run's hosts are in its state), terminate any instance still
   tagged with one of those runs, and check none is left. Another launcher's hosts are never
   touched.

--replace launches the runs named by --runs again over their earlier attempts: just before a run's
hosts are created, the bucket's runs/<campaign>/<run>/ is copied server-side to
superseded/<campaign>/<run>-<UTC>/, every copy checked, results/<campaign>/<run>/ moved to
results/superseded/<campaign>/<run>-<UTC>/, and only then are the bucket's originals deleted (the
bucket is versioned, so they stay as old versions too) and the prefix checked empty. A copy or a
move that fails stops the run with nothing deleted. The run's pull then takes only objects the
bucket stamped after its newest archive copy (S3's own clock, so the launcher's doesn't matter).

--dry-run does 1 and 2 and a `terraform plan` of every run's hosts at once (no lock, no apply),
reports what --replace would archive, and changes nothing. Operational events go to
results/<campaign>/launch.log and each run's
ops/ directory, never into the results. Standard library only; needs terraform and the AWS CLI,
logged in (`aws login`) with the management-account default profile and the member-account
profile (FLEETKIT_AWS_PROFILE, default fleetkit).
"""
from __future__ import annotations

import argparse
import base64
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STACK = REPO / "infra" / "experiments" / "campaign"
PARENT_STACK = STACK.parent
RESULTS = REPO / "results"
EXAMPLES = REPO / "experiments" / "campaigns" / "examples"
REGION = "us-east-1"
MANAGEMENT_PROFILE = os.environ.get("FLEETKIT_MANAGEMENT_PROFILE", "default")
MEMBER_PROFILE = os.environ.get("FLEETKIT_AWS_PROFILE", "fleetkit")
VCPU_QUOTA_CODE = "L-1216C47A"  # Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances
PROJECT_START = "2026-09-01"    # the project cap's start (infra/org, project_cap_start)
# The account's instance-type guardrail (infra/org, allowed_instance_families x allowed_instance_sizes);
# infra/experiments/campaign/variables.tf checks the same list.
GUARDRAIL = re.compile(r"^(m8i|c8i|m7i|c7i)\.(large|xlarge|2xlarge|4xlarge)$")
SUPPORT_HEALTH_PORT = 8082
FK = "/var/lib/fleetkit"
SUPERSEDED = "superseded"  # results/superseded/<campaign>/ and the bucket's superseded/<campaign>/
# How much earlier than the launcher's clock S3 may stamp an object this attempt wrote, when nothing
# in the bucket was archived (then the bucket's own clock gives the cutoff). The first object is
# written minutes after launch (hosts boot first), so this only absorbs clock error.
CLOCK_SKEW_S = 60
HIDDEN_BUCKET = "<results bucket>"  # the bucket's name carries the account id; logs never show it
HIDDEN_ACCOUNT = "<account>"
# An instance in these states is still there: it may run, or be started again. (A host that shuts
# itself down terminates: shutting-down, then terminated.)
LIVE_STATES = "pending,running,stopping,stopped"
INSTANCES_QUERY = "Reservations[].Instances[].[InstanceId, State.Name, Tags[?Key=='Run'].Value | [0]]"
STATE_HOST = re.compile(r'^aws_instance\.(?:worker|support)\["([^"]+)"\]$')  # a host in `terraform state list`
ROLE_ACCOUNT = re.compile(r'^\s*member_role_arn\s*=\s*"arn:[a-z-]+:iam::(\d{12}):', re.M)  # any partition


# ------------------------------------------------------------------ expand.py
def expand_module():
    path = REPO / "experiments" / "schema" / "expand.py"
    spec = importlib.util.spec_from_file_location("fleetkit_expand", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXPAND = expand_module()


# ------------------------------------------------------------------ the checks (pure)
def refusals(campaign_path: Path, plan: dict, *, spent: float | None, free_vcpus: int | None,
             head_pushed: bool, earlier: dict[str, str] | None = None, replace: bool = False,
             uncommitted: list[str] = ()) -> list[str]:
    """Every reason not to launch this (valid) campaign plan; empty when it may launch. ``earlier``
    maps a run to what an earlier attempt at it left behind (earlier_attempt); --replace
    (``replace``) archives that instead of refusing."""
    out = []
    limits = EXPAND.LIMITS
    if plan["campaign"] == SUPERSEDED:
        out.append(f"a campaign can't be named {SUPERSEDED}: results/{SUPERSEDED}/ holds archived attempts")
    try:
        Path(campaign_path).resolve().relative_to(EXAMPLES.resolve())
        out.append(f"{campaign_path} is under experiments/campaigns/examples/: written, not approved to run")
    except ValueError:
        pass
    worst, limit, cap = plan["worst_case_usd"], limits["campaign_worst_case_usd"], limits["project_cap_usd"]
    if worst > limit:
        out.append(f"the worst case is ${worst:,.2f}, above the ${limit:,.2f} limit for one campaign")
    if spent is None:
        out.append("spend so far is unknown (Cost Explorer didn't answer); pass --spent USD to launch anyway")
    elif spent + worst > cap:
        out.append(f"the worst case ${worst:,.2f} on top of ${spent:,.2f} spent crosses the ${cap:,.2f} project cap")
    for r in plan["runs"]:
        for key in ("worker_instance_type", "support_instance_type"):
            if not GUARDRAIL.match(r[key]):
                out.append(f"{r['run']}: {r[key]} isn't allowed by the account's instance-type guardrail "
                           "(metal needs the guardrail changed first)")
    if free_vcpus is not None:
        for r in plan["runs"]:
            if r["vcpus"] > free_vcpus:
                out.append(f"{r['run']}: needs {r['vcpus']} vCPUs with its support host; {free_vcpus} are free "
                           "under the quota")
    if not head_pushed:
        out.append("HEAD isn't on any remote branch; push it first (the hosts check it out from GitHub)")
    if uncommitted:
        out.append(f"uncommitted changes the hosts wouldn't run (they check out HEAD): {', '.join(uncommitted[:6])}"
                   f"{' and more' if len(uncommitted) > 6 else ''}")
    for run, what in ({} if replace else earlier or {}).items():
        out.append(f"{run}: an earlier attempt is in the way ({what}); a new attempt would be pulled on top "
                   f"of it. Pass --replace to archive it under {SUPERSEDED}/ and launch the run again")
    return list(dict.fromkeys(out))


def earlier_attempt(campaign: str, run: str, local_files: int, objects: list[dict]) -> str | None:
    """What an earlier attempt at this run left behind, in words; None when it left nothing."""
    left = []
    if local_files:
        left.append(f"results/{campaign}/{run}/ holds {local_files} files")
    if objects:
        left.append(f"runs/{campaign}/{run}/ in the results bucket holds {len(objects)} objects")
    return " and ".join(left) or None


def archive_preview(campaign: str, found: dict[str, tuple[int, list[dict]]]) -> list[str]:
    """What --replace would archive, one line per place, from {run: (local files, bucket objects)}."""
    out = []
    for run, (files, objects) in found.items():
        if files:
            out.append(f"{run}: results/{campaign}/{run}/ ({files} files) -> "
                       f"results/{SUPERSEDED}/{campaign}/{run}-<UTC>/")
        if objects:
            mb = sum(int(o.get("Size") or 0) for o in objects) / 1e6
            out.append(f"{run}: runs/{campaign}/{run}/ ({len(objects)} objects, {mb:,.1f} MB) -> "
                       f"{SUPERSEDED}/{campaign}/{run}-<UTC>/ in the results bucket")
    return out


def utc_stamp(now: datetime.datetime | None = None) -> str:
    """The <UTC> in an archive's name: 20260927T184512Z."""
    return (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def last_modified(obj: dict) -> datetime.datetime:
    """An S3 listing's LastModified ("2026-09-28T01:33:53+00:00", or ...Z), timezone-aware."""
    return datetime.datetime.fromisoformat(obj["LastModified"].replace("Z", "+00:00"))


def split_by_time(objects: list[dict], since: datetime.datetime) -> tuple[list[dict], list[dict]]:
    """(stamped at or after ``since``, stamped before it)."""
    new = [o for o in objects if last_modified(o) >= since]
    return new, [o for o in objects if last_modified(o) < since]


def folder_marker(obj: dict) -> bool:
    """A zero-byte key ending in / (what the S3 console makes for a "folder"). `aws s3 cp --recursive`
    never copies one, so a copy check leaves it out; it holds nothing to archive."""
    return obj["Key"].endswith("/") and not int(obj.get("Size") or 0)


def instances_text(instances: list[tuple[str, str, str]], most: int = 6) -> str:
    """(instance id, run, state) listed for a message."""
    text = ", ".join(f"{run}: {iid} {state}" for iid, run, state in instances[:most])
    return text + (f" and {len(instances) - most} more" if len(instances) > most else "")


def glob_escape(key: str) -> str:
    """A key as an `aws s3` --include pattern that matches that key alone (the CLI matches with fnmatch)."""
    return re.sub(r"([*?\[])", r"[\1]", key)


def local_files(path: Path) -> int:
    """How many files are under ``path`` (a path that is itself a file counts as one)."""
    if not path.exists():
        return 0
    if not path.is_dir():
        return 1
    return sum(1 for p in path.rglob("*") if not p.is_dir())


def next_to_start(queue: list[dict], free_vcpus: int) -> list[dict]:
    """The queued runs to start now: in queue order, each one whose vCPUs still fit."""
    out = []
    for r in queue:
        if r["vcpus"] <= free_vcpus:
            out.append(r)
            free_vcpus -= r["vcpus"]
    return out


def queue_order(plan: dict) -> list[dict]:
    """The plan's runs in the order of its waves (expand.py packs them under the quota)."""
    by_name = {r["run"]: r for r in plan["runs"]}
    order = [n for w in plan["waves"] for n in w] + [r["run"] for r in plan["runs"]]
    return [by_name[n] for n in dict.fromkeys(order)]


def plan_changes(show: dict) -> list[tuple[str, str]]:
    """(address, action) for every resource a `terraform show -json` plan changes."""
    out = []
    for rc in show.get("resource_changes") or []:
        if rc.get("mode") == "data":
            continue
        actions = rc.get("change", {}).get("actions") or []
        if actions in (["no-op"], ["read"]):
            continue
        out.append((rc["address"], "replace" if len(actions) > 1 else actions[0]))
    return sorted(out)


def run_addresses(run: str) -> list[str]:
    return [f'aws_instance.worker["{run}"]', f'aws_instance.support["{run}"]']


def gate(changes: list[tuple[str, str]], *, create: set[str] = frozenset(), delete: set[str] = frozenset(),
         setup: bool = False) -> str | None:
    """Why this plan must not be applied, or None. It may create exactly ``create``, delete only
    from ``delete`` (a host that already shut itself down is simply gone), and on ``setup`` create
    the campaign's support security group and its rules; nothing else."""
    made = {a for a, act in changes if act == "create"}
    for addr, act in changes:
        if act == "create" and (addr in create or (setup and addr.startswith(
                ("aws_security_group.support", "aws_vpc_security_group_")))):
            continue
        if act == "delete" and addr in delete:
            continue
        return f"unexpected change: {act} {addr}"
    missing = set(create) - made
    if missing:
        return f"the plan doesn't create {', '.join(sorted(missing))}"
    return None


def backend_args(backend_hcl: str, key: str) -> list[str]:
    """-backend-config arguments from the parent stack's backend.hcl, with this campaign's state key."""
    out = []
    for line in backend_hcl.splitlines():
        m = re.match(r'^\s*([a-z_]+)\s*=\s*(.+?)\s*$', line)
        if not m or m.group(1) == "key":
            continue
        v = m.group(2).strip().strip('"')
        out.append(f"-backend-config={m.group(1)}={v}")
    return out + [f"-backend-config=key={key}"]


def run_status(run_json: dict | None) -> str:
    """How a run ended, in the words the results use: complete, stopped early, or no results."""
    plan = (run_json or {}).get("plan") if isinstance(run_json, dict) else None
    if not isinstance(plan, dict):
        return "no results"
    return "complete" if plan.get("complete") else "stopped early"


# ------------------------------------------------------------------ shell helpers
class StepFailed(Exception):
    pass


class Refused(StepFailed):
    """A run not launched because an earlier attempt is in its way."""


class Log:
    def __init__(self, path: Path | None):
        self.path = path
        self.lock = threading.Lock()
        self.hidden: dict[str, str] = {}  # text never logged -> what's logged instead

    def hide(self, text: str | None, instead: str = HIDDEN_BUCKET) -> None:
        if text:
            self.hidden[text] = instead

    def __call__(self, msg: str, run: str | None = None) -> None:
        for text, instead in self.hidden.items():
            msg = msg.replace(text, instead)
        line = f"{time.strftime('%H:%M:%S')} {'[' + run + '] ' if run else ''}{msg}"
        with self.lock:
            print(line, flush=True)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")


def sh(args: list[str], *, env: dict | None = None, cwd: Path | None = None, check: bool = True,
       timeout: float | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run(args, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True,
                       timeout=timeout)
    if check and p.returncode != 0:
        raise StepFailed(f"{' '.join(args[:4])}...: exit {p.returncode}: {(p.stderr or p.stdout).strip()[-600:]}")
    return p


def aws(*args: str, profile: str = MEMBER_PROFILE, check: bool = True, parse: bool = True):
    p = sh(["aws", "--profile", profile, "--region", REGION, *args, "--output", "json"], check=check)
    if not parse:
        return p
    return json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else None


# ------------------------------------------------------------------ what the account says
def spent_so_far() -> float | None:
    """Spend since the project started, from Cost Explorer (the whole consolidated bill, so at least
    the project's share; it lags by up to a day). None when it can't be read."""
    end = (datetime.datetime.now(datetime.timezone.utc).date() + datetime.timedelta(days=1)).isoformat()
    try:
        doc = aws("ce", "get-cost-and-usage", "--time-period", f"Start={PROJECT_START},End={end}",
                  "--granularity", "MONTHLY", "--metrics", "UnblendedCost", profile=MANAGEMENT_PROFILE)
        return round(sum(float(r["Total"]["UnblendedCost"]["Amount"]) for r in doc["ResultsByTime"]), 2)
    except (StepFailed, KeyError, TypeError, ValueError):
        return None


def vcpu_quota() -> int | None:
    try:
        doc = aws("service-quotas", "get-service-quota", "--service-code", "ec2", "--quota-code", VCPU_QUOTA_CODE)
        return int(doc["Quota"]["Value"])
    except (StepFailed, KeyError, TypeError, ValueError):
        return None


def campaign_instances(campaign: str, runs: list[str] | None = None) -> list[tuple[str, str, str]]:
    """(instance id, run, state) of every instance tagged with the campaign (and, given ``runs``,
    with one of them) that is still there: pending, running, stopping or stopped. Every host the
    campaign stack creates carries Campaign and Run tags. Raises StepFailed when they can't be
    listed."""
    filters = [f"Name=tag:Campaign,Values={campaign}", f"Name=instance-state-name,Values={LIVE_STATES}"]
    if runs is not None:
        if not runs:
            return []
        filters.append(f"Name=tag:Run,Values={','.join(runs)}")
    doc = aws("ec2", "describe-instances", "--filters", *filters, "--query", INSTANCES_QUERY)
    return [(iid, run or "?", state) for iid, state, run in doc or []]


def vcpus_running(tag_filters: list[str] | None = None) -> int:
    doc = aws("ec2", "describe-instances", "--filters", "Name=instance-state-name,Values=pending,running",
              *(tag_filters or []),
              "--query", "Reservations[].Instances[].[CpuOptions.CoreCount, CpuOptions.ThreadsPerCore]")
    return sum(int(c or 0) * int(t or 1) for c, t in doc or [])


def head_commit() -> str:
    return sh(["git", "-C", str(REPO), "rev-parse", "HEAD"]).stdout.strip()


def head_pushed() -> bool:
    return bool(sh(["git", "-C", str(REPO), "branch", "-r", "--contains", "HEAD"], check=False).stdout.strip())


# What the hosts run from their checkout of HEAD: the harness, the setup scripts, the fixture, the
# launcher's support health service and expand.py, the Makefile (setup.sh runs make venv, fixture,
# guest-image and rootfs) and the collector configuration the support host mounts.
HOST_PATHS = ("harness", "images", "fixture", "experiments/launcher", "experiments/schema", "Makefile",
              "observability")


def uncommitted() -> list[str]:
    p = sh(["git", "-C", str(REPO), "status", "--porcelain", "--", *HOST_PATHS], check=False)
    return [line[3:] for line in p.stdout.splitlines() if line.strip()]


# ------------------------------------------------------------------ the results bucket
def member_account() -> str | None:
    """The account of the member-account profile, which every `aws` call here uses. Never printed
    or logged. None when it can't be read."""
    try:
        return aws("sts", "get-caller-identity", "--query", "Account") or None
    except StepFailed:
        return None


def stack_account() -> str | None:
    """The account the campaign stack creates hosts in: the one whose role it assumes (member_role_arn
    in infra/experiments/terraform.tfvars, which it reads). Never printed or logged. None when it
    can't be read."""
    try:
        m = ROLE_ACCOUNT.search((PARENT_STACK / "terraform.tfvars").read_text())
    except OSError:
        return None
    return m.group(1) if m else None


def results_bucket(account: str) -> str:
    """The results bucket's name, as infra/experiments names it (results.tf; the campaign stack's
    data.tf reads the same). It's needed before the campaign's state has any outputs, and it
    carries the account id, so it's never printed or logged."""
    return f"fleetkit-results-{account}"


def list_objects(bucket: str, prefix: str) -> list[dict]:
    """Every object under ``prefix`` as {Key, LastModified, Size}. The prefix ends in / so that
    runs/c/r1/ never takes in runs/c/r10/. Raises StepFailed when the bucket can't be listed."""
    if not prefix.endswith("/"):
        raise ValueError(f"a prefix to list must end in /: {prefix}")
    return aws("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix,
               "--query", "Contents[].{Key: Key, LastModified: LastModified, Size: Size}") or []


def earlier_attempts(campaign: str, runs: list[str], bucket: str | None
                     ) -> tuple[dict[str, tuple[int, list[dict]]], list[str]]:
    """{run: (files under results/<campaign>/<run>/, objects under the bucket's runs/<campaign>/<run>/)}
    and the runs whose prefix couldn't be listed. Without a bucket name nothing is listed."""
    found, unlisted = {}, []
    for run in runs:
        try:
            objects = list_objects(bucket, f"runs/{campaign}/{run}/") if bucket else []
        except StepFailed:
            objects = []
            unlisted.append(run)
        found[run] = (local_files(RESULTS / campaign / run), objects)
    return found, unlisted


def copy_prefix(bucket: str, src: str, dst: str, objects: list[dict]) -> datetime.datetime | None:
    """Copy everything under ``src`` to ``dst`` in the bucket, server-side, then check that every one
    of ``objects`` (src's listing) but a folder marker has its copy, the same size. Raises StepFailed
    otherwise; it never deletes anything. Returns the bucket's stamp on the newest copy (None when
    nothing was copied): S3's own clock, and earlier than anything written after the copy."""
    p = aws("s3", "cp", "--recursive", "--only-show-errors", f"s3://{bucket}/{src}", f"s3://{bucket}/{dst}",
            check=False, parse=False)
    if p.returncode != 0:
        raise StepFailed(f"copying {src} to {dst} failed (exit {p.returncode}: "
                         f"{(p.stderr or p.stdout).strip()[-300:]}); nothing was deleted")
    listed = list_objects(bucket, dst)
    copied = {o["Key"][len(dst):]: o.get("Size") for o in listed}
    missing = [o["Key"] for o in objects
               if not folder_marker(o) and copied.get(o["Key"][len(src):], -1) != o.get("Size")]
    if missing:
        raise StepFailed(f"{len(missing)} of {len(objects)} objects under {src} have no copy under {dst} "
                         f"(first: {missing[0][len(src):]}); nothing was deleted")
    return max((last_modified(o) for o in listed), default=None)


def delete_objects(bucket: str, keys: list[str], batch: int = 500) -> None:
    """Delete exactly these keys (never a whole prefix: an object that arrived after the listing was
    never copied). Raises StepFailed on the first batch with an error."""
    for i in range(0, len(keys), batch):
        chunk = keys[i:i + batch]
        doc = aws("s3api", "delete-objects", "--bucket", bucket, "--delete",
                  json.dumps({"Objects": [{"Key": k} for k in chunk], "Quiet": True})) or {}
        errors = doc.get("Errors") or []
        if errors:
            raise StepFailed(f"{i + len(chunk) - len(errors)} of {len(keys)} originals were deleted; "
                             f"{errors[0].get('Key')}: {errors[0].get('Code')}")


# ------------------------------------------------------------------ Terraform, one state per campaign
class Stack:
    """infra/experiments/campaign for one campaign. Every change goes through one lock, is planned,
    checked against exactly what it should change (gate) and only then applied."""

    def __init__(self, campaign: str, shutdown_after_minutes: int, repo_ref: str, log: Log):
        self.campaign, self.timer, self.repo_ref, self.log = campaign, shutdown_after_minutes, repo_ref, log
        self.data_dir = STACK / ".terraform" / "campaigns" / campaign
        self.env = {"TF_DATA_DIR": str(self.data_dir), "TF_IN_AUTOMATION": "1"}
        self.lock = threading.Lock()
        self.runs: dict[str, dict] = {}

    def tf(self, *args: str, check: bool = True, timeout: float = 1800) -> subprocess.CompletedProcess:
        return sh(["terraform", *args], cwd=STACK, env=self.env, check=check, timeout=timeout)

    def init(self) -> None:
        hcl = (PARENT_STACK / "backend.hcl").read_text()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tf("init", "-input=false", "-no-color", "-reconfigure",
                *backend_args(hcl, f"experiments/campaigns/{self.campaign}.tfstate"))

    def _var_args(self, runs: dict) -> list[str]:
        vars_file = self.data_dir / "runs.tfvars.json"
        vars_file.write_text(json.dumps({"campaign": self.campaign, "runs": runs,
                                         "shutdown_after_minutes": self.timer, "repo_ref": self.repo_ref}))
        return [f"-var-file={PARENT_STACK / 'terraform.tfvars'}", f"-var-file={vars_file}"]

    def plan(self, runs: dict, *, destroy: bool = False, lock: bool = True, refresh: bool = True,
             targets: list[str] = ()):
        out = self.data_dir / "tfplan"
        args = ["plan", "-input=false", "-no-color", f"-out={out}", *self._var_args(runs)]
        args += [] if lock else ["-lock=false"]
        args += [] if refresh else ["-refresh=false"]
        args += ["-destroy"] if destroy else []
        args += [f"-target={t}" for t in targets]
        p = self.tf(*args)
        show = json.loads(self.tf("show", "-json", str(out)).stdout)
        return out, plan_changes(show), p.stdout

    def _change(self, runs: dict, why: str, **expect) -> None:
        # -refresh=false: an apply only makes this change, and never recreates a host of another
        # run that has already shut itself down.
        planfile, changes, _ = self.plan(runs, refresh=False)
        problem = gate(changes, **expect)
        if problem:
            raise StepFailed(f"terraform ({why}): {problem}; not applied")
        if changes:
            self.tf("apply", "-input=false", "-no-color", str(planfile))
        self.runs = runs

    def setup(self) -> None:
        with self.lock:
            self._change(dict(self.runs), "campaign setup", setup=True)

    def add(self, run: str, worker: str, support: str) -> dict:
        with self.lock:
            runs = {**self.runs, run: {"worker_instance_type": worker, "support_instance_type": support}}
            self._change(runs, f"create {run}", create=set(run_addresses(run)))
            return json.loads(self.tf("output", "-json", "runs").stdout)[run]

    def remove(self, run: str) -> None:
        with self.lock:
            runs = {k: v for k, v in self.runs.items() if k != run}
            self._change(runs, f"destroy {run}", delete=set(run_addresses(run)))

    def state_runs(self) -> set[str]:
        """The runs whose hosts the campaign's state holds (read-only, no lock). Raises StepFailed
        when the state can't be read; a campaign never launched has none."""
        p = self.tf("state", "list", "-no-color", check=False, timeout=300)
        if p.returncode != 0:
            if "No state file was found" in (p.stderr or "") + (p.stdout or ""):
                return set()
            raise StepFailed(f"terraform state list: exit {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}")
        return {m.group(1) for line in p.stdout.splitlines() if (m := STATE_HOST.match(line.strip()))}

    def destroy(self, runs: set[str], *, everything: bool = False) -> list[tuple[str, str]]:
        """Destroy the hosts of ``runs`` (a targeted plan); with ``everything``, the whole state, the
        campaign's support security group included, which the caller has found holds no other run's
        hosts. A plan that would delete any other run's host, or change anything but deletes, isn't
        applied."""
        mine = {a for r in runs for a in run_addresses(r)}
        if not everything and not mine:
            return []
        with self.lock:
            planfile, changes, _ = self.plan({}, destroy=True, targets=[] if everything else sorted(mine))
            bad = [c for c in changes if c[1] != "delete" or (c[0].startswith("aws_instance.") and c[0] not in mine)]
            if bad:
                raise StepFailed(f"terraform (destroy): unexpected {bad}; not applied")
            if changes:
                self.tf("apply", "-input=false", "-no-color", str(planfile))
            self.runs = {} if everything else {k: v for k, v in self.runs.items() if k not in runs}
            return changes

    def bucket(self) -> str:
        return json.loads(self.tf("output", "-json", "results_bucket").stdout)


def stale_hosts(stack: Stack, campaign: str) -> set[str]:
    """Before a launch touches the campaign's state: the runs whose hosts it still holds when none of
    them is still there (a launcher stopped before its sweep; the launch clears them first). Raises
    Refused when any of them is still there: another launcher of the campaign is running, and a
    launch sharing its state would destroy its hosts. Raises StepFailed when either can't be read."""
    held = stack.state_runs()
    still = campaign_instances(campaign, sorted(held)) if held else []
    if still:
        raise Refused(f"the campaign's Terraform state holds hosts that are still there ({instances_text(still)}): "
                      f"another launcher of {campaign} is running, and this one's changes to that state would "
                      "destroy them. Launch again once it has ended")
    return held


# ------------------------------------------------------------------ SSM
def ssm_online(iid: str, stop: threading.Event, timeout_s: int = 600) -> bool:
    end = time.time() + timeout_s
    while time.time() < end and not stop.is_set():
        doc = aws("ssm", "describe-instance-information", "--filters", f"Key=InstanceIds,Values={iid}", check=False)
        info = (doc or {}).get("InstanceInformationList") or []
        if info and info[0].get("PingStatus") == "Online":
            return True
        stop.wait(15)
    return False


def ssm_run(iid: str, command: str, timeout_s: int, *, bucket: str, prefix: str, out_file: Path,
            stop: threading.Event) -> str:
    """Run a shell command as root on a host; its output goes to out_file. -> the final status."""
    params = json.dumps({"commands": [command], "executionTimeout": [str(int(timeout_s))]})
    doc = aws("ssm", "send-command", "--document-name", "AWS-RunShellScript", "--instance-ids", iid,
              "--parameters", params, "--output-s3-bucket-name", bucket, "--output-s3-key-prefix", prefix)
    cid = doc["Command"]["CommandId"]
    status, end = "Pending", time.time() + timeout_s + 120
    while time.time() < end:
        if stop.wait(10):
            aws("ssm", "cancel-command", "--command-id", cid, check=False, parse=False)
            status = "Cancelled"
            break
        inv = aws("ssm", "get-command-invocation", "--command-id", cid, "--instance-id", iid, check=False)
        status = (inv or {}).get("Status", status)
        if status in ("Success", "Failed", "TimedOut", "Cancelled"):
            break
    inv = aws("ssm", "get-command-invocation", "--command-id", cid, "--instance-id", iid, check=False) or {}
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text((inv.get("StandardOutputContent") or "") + (inv.get("StandardErrorContent") or ""))
    return status


# ------------------------------------------------------------------ one run
class Campaign:
    poll_s, start_gap_s = 5.0, 2.0  # how often the launch loop looks; the gap between two runs' starts
    settle_s = 20.0  # how long the sweep gives the instances it terminates before it looks again

    def __init__(self, camp: dict, plan: dict, log: Log, stack: Stack, head: str, replace: bool = False):
        self.camp, self.plan, self.log, self.stack, self.head = camp, plan, log, stack, head
        self.name = camp["name"]
        self.dir = RESULTS / self.name
        self.timer = camp["shutdown_after_minutes"]
        self.replace = replace
        self.stop = threading.Event()
        self.bucket = ""
        self.status: dict[str, str] = {}
        self.started: set[str] = set()  # the runs whose hosts this launcher created (or tried to)

    def clear_the_way(self, name: str, say) -> datetime.datetime | None:
        """Just before a run's hosts are created. An instance tagged with the run that is still there
        refuses it (an earlier attempt's host, or another launcher's, could still write to its
        prefix). What an earlier attempt left in results/<rid>/ or the bucket's runs/<rid>/ refuses
        it too, or with --replace is archived under superseded/: the bucket's objects copied and
        every copy checked, the local directory moved, and only then the bucket's originals deleted
        and the prefix checked empty. Returns the bucket's stamp on the newest archive copy (S3's
        clock; whatever this attempt writes is stamped later), None when the bucket held nothing.
        Raises Refused or StepFailed; the run then doesn't start."""
        rid, rdir = f"{self.name}/{name}", self.dir / name
        prefix = f"runs/{rid}/"
        still = campaign_instances(self.name, [name])
        if still:
            raise Refused(f"an instance tagged with it is still there ({instances_text(still)}); it could still "
                          f"write to {prefix}")
        objects = list_objects(self.bucket, prefix)
        what = earlier_attempt(self.name, name, local_files(rdir), objects)
        if what is None:
            return None
        if not self.replace:
            raise Refused(f"an earlier attempt is in the way ({what}); launch it again with --replace to "
                          f"archive that under {SUPERSEDED}/")
        archived = f"{name}-{utc_stamp()}"
        newest = None
        if objects:
            dst = f"{SUPERSEDED}/{self.name}/{archived}/"
            newest = copy_prefix(self.bucket, prefix, dst, objects)
            markers = sum(1 for o in objects if folder_marker(o))
            say(f"archive: copied the {len(objects) - markers} objects under {prefix} to {dst} in the results "
                f"bucket{f' ({markers} empty folder markers need no copy)' if markers else ''}")
        if rdir.exists():
            dest = self.dir.parent / SUPERSEDED / self.name / archived
            if dest.exists():
                raise StepFailed(f"can't archive results/{rid}/: {dest} already exists; nothing was deleted")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(rdir, dest)
            except OSError as exc:
                raise StepFailed(f"moving results/{rid}/ to {dest} failed ({exc}); nothing was deleted") from exc
            say(f"archive: moved results/{rid}/ to results/{SUPERSEDED}/{self.name}/{archived}/")
        if objects:
            try:
                delete_objects(self.bucket, [o["Key"] for o in objects])
            except StepFailed as exc:
                raise StepFailed(f"{exc}; every original has its copy under {SUPERSEDED}/{self.name}/{archived}/, "
                                 f"and what wasn't deleted is still under {prefix}") from exc
            say(f"archive: deleted the {len(objects)} originals under {prefix} (their copies are under "
                f"{SUPERSEDED}/{self.name}/{archived}/, and the bucket keeps them as old versions)")
        again = list_objects(self.bucket, prefix)
        if again:
            raise Refused(f"{prefix} holds {len(again)} objects again after the archive (first: "
                          f"{again[0]['Key'][len(prefix):]} at {again[0]['LastModified']}): something is still "
                          "writing there. They're kept, and the run isn't launched")
        return newest

    def carry_out(self, run: dict) -> None:
        name = run["run"]
        rid = f"{self.name}/{name}"
        rdir = self.dir / name
        ops = rdir / "ops"
        say = lambda m: self.log(m, name)  # noqa: E731
        created = cleared = False
        started = time.time()
        since = None  # the pull takes nothing the bucket stamped earlier
        deadline = started + self.timer * 60 - 180  # the hosts shut themselves down at the timer

        def left() -> int:
            return int(deadline - time.time())

        def step(label: str, iid: str, cmd: str, timeout_s: int, must: bool = True) -> str:
            if self.stop.is_set():
                raise StepFailed("the launcher was interrupted")
            timeout_s = min(timeout_s, left())
            if timeout_s < 60:
                raise StepFailed(f"no time left before the shutdown timer for {label}")
            say(f"{label} (up to {timeout_s} s)")
            st = ssm_run(iid, cmd, timeout_s, bucket=self.bucket, prefix=f"runs/{rid}/ssm/{label}",
                         out_file=ops / f"{label}.txt", stop=self.stop)
            say(f"{label}: {st}")
            if must and st != "Success":
                raise StepFailed(f"{label} ended {st} (ops/{label}.txt)")
            return st

        try:
            if self.stop.is_set():
                raise StepFailed("the launcher was interrupted before this run started")
            archived_at = self.clear_the_way(name, say)
            cleared = True  # results/<rid>/ and runs/<rid>/ now hold only what this attempt writes
            # The bucket's own stamp on the archive copies when it held an earlier attempt (no clock
            # skew), else the launcher's clock less a margin for skew.
            since = archived_at or (datetime.datetime.now(datetime.timezone.utc)
                                    - datetime.timedelta(seconds=CLOCK_SKEW_S))
            deadline = time.time() + self.timer * 60 - 180
            say(f"creating a {run['worker_instance_type']} worker host and a {run['support_instance_type']} "
                "support host")
            created = True  # from here on the run's hosts are destroyed at the end, even if this apply fails midway
            self.started.add(name)  # and the sweep destroys what's left of them
            ids = self.stack.add(name, run["worker_instance_type"], run["support_instance_type"])
            wid, sid, sip = ids["worker_id"], ids["support_id"], ids["support_ip"]
            say(f"worker {wid}, support {sid} at {sip}")
            for iid in (wid, sid):
                if not ssm_online(iid, self.stop):
                    raise StepFailed(f"{iid} never registered with SSM")
            step("cloud-init", wid, f"timeout 900 cloud-init status --wait --long; echo EXIT=$?; "
                 f"cp /var/log/cloud-init-output.log {FK}/ 2>/dev/null; test -f {FK}/cloud-init-done", 960)
            step("support-cloud-init", sid, f"timeout 900 cloud-init status --wait --long; echo EXIT=$?; docker ps -a; "
                 f"test -f {FK}/cloud-init-done", 960)
            step("support-health", sid,
                 "systemctl reset-failed fleetkit-support-health >/dev/null 2>&1; "
                 "systemd-run --unit fleetkit-support-health --collect -p Restart=on-failure "
                 f"/usr/bin/python3.12 /opt/fleetkit/experiments/launcher/support_health.py --port {SUPPORT_HEALTH_PORT}"
                 f" && for i in $(seq 1 20); do curl -fsS -m 2 http://127.0.0.1:{SUPPORT_HEALTH_PORT}/health && exit 0;"
                 " sleep 1; done; exit 1", 120)
            envs = (f"FLEETKIT_RUN_ID={rid} FIXTURE_URL=http://{sip}:8081 OTLP_ENDPOINT=http://{sip}:4318 "
                    "METRICS_PERIOD=0.2")
            step("host-setup", wid, f"cd /opt/fleetkit && git fetch -q && git checkout -q {self.head} && "
                 f"{envs} bash images/host/setup.sh 2>&1", 1800)
            # The run's resolved spec, as expand.py --run prints it; hostcheck boots its hypervisor.
            spec = {"campaign": self.name, "run": name, "spec_name": run["spec_name"], "replica": run["replica"],
                    "shutdown_after_minutes": self.timer, "spec": run["spec"]}
            b64 = base64.b64encode(json.dumps(spec, indent=2).encode()).decode()
            spec_file = f"{FK}/runs/{rid}/spec.json"
            step("hostcheck", wid, f"mkdir -p {FK}/runs/{rid} && echo {b64} | base64 -d > {spec_file} && "
                 f"{envs} SPEC={spec_file} bash /opt/fleetkit/images/host/hostcheck.sh", 400)
            bound = max(300, left() - 20 * 60)  # leaves time for the bundle, the support sync and the pull
            step("run", wid, f"{envs} SPEC_FILE={spec_file} SUPPORT_HEALTH_URL=http://{sip}:{SUPPORT_HEALTH_PORT} "
                 f"RUN_TIMEOUT_S={bound} bash /opt/fleetkit/images/host/stage.sh run 2>&1", bound + 300, must=False)
            step("bundle", wid, f"FLEETKIT_RUN_ID={rid} bash /opt/fleetkit/images/host/stage.sh bundle 2>&1", 900,
                 must=False)
            step("support-sync", sid, f"FLEETKIT_RUN_ID={rid} bash /opt/fleetkit/images/support/sync.sh 2>&1", 600,
                 must=False)
        except Refused as exc:
            say(f"REFUSED: {exc}")
            self.status[name] = f"refused: {exc}"
        except StepFailed as exc:
            say(f"FAILED: {exc}")
            self.status[name] = f"failed: {exc}"
        except Exception as exc:  # a launcher bug must not leave hosts behind
            say(f"FAILED: {type(exc).__name__}: {exc}")
            self.status[name] = f"failed: {type(exc).__name__}: {exc}"
        finally:
            if created:  # every stage already synced its evidence to the bucket, so the hosts go first
                try:
                    self.stack.remove(name)
                    say("hosts destroyed")
                except Exception as exc:
                    say(f"DESTROY FAILED: {exc}; the final sweep will retry")
                self.pull(rid, rdir, say, since=since)
                try:
                    for line in (rdir / "ops.jsonl").read_text().splitlines() if (rdir / "ops.jsonl").exists() else []:
                        e = json.loads(line)
                        say("ops: " + " ".join(f"{k}={e[k]}" for k in ("event", "trial_id", "density", "cause",
                                                                       "next") if e.get(k) is not None))
                except Exception as exc:
                    say(f"ops.jsonl couldn't be read: {type(exc).__name__}: {exc}")
            # results/<rid>/ is this attempt's only once the way was cleared; before that it's an earlier one's
            try:
                got = run_status(EXPAND.read_json(rdir / "run.json") if cleared and (rdir / "run.json").exists()
                                 else None)
            except Exception as exc:
                say(f"run.json couldn't be read: {type(exc).__name__}: {exc}")
                got = run_status(None)
            self.status.setdefault(name, got)
            if self.status[name] != got:
                self.status[name] += f" ({got})"
            say(f"ended: {self.status[name]} after {int((time.time() - started) / 60)} min")

    def pull(self, rid: str, rdir: Path, say, since: datetime.datetime, batch: int = 500) -> None:
        """Download what this attempt wrote under the bucket's runs/<rid>/: run/ into results/<rid>/,
        support/ into its support/, the rest (SSM output, host files) into its ops/host/. Only objects
        the bucket stamped at or after ``since`` are downloaded, each named by its own --include, and
        only into a results/<rid>/ holding nothing but this attempt's ops/*.txt, so nothing an earlier
        attempt left, there or in the bucket, can mix with this one. Never raises: a failure is
        logged, and what wasn't pulled is still in the bucket."""
        try:
            self._pull(rid, rdir, say, since, batch)
        except Exception as exc:
            say(f"pull: failed ({type(exc).__name__}: {exc}); what wasn't pulled is still in the results bucket "
                f"under runs/{rid}/")

    def _pull(self, rid: str, rdir: Path, say, since: datetime.datetime, batch: int) -> None:
        prefix = f"runs/{rid}/"
        stray = sorted(str(p.relative_to(rdir)) for p in rdir.rglob("*")
                       if p.is_file() and not (p.parent == rdir / "ops" and p.suffix == ".txt")) \
            if rdir.exists() else []
        if stray:
            say(f"pull: results/{rid}/ already holds {len(stray)} files this attempt didn't write (e.g. {stray[0]}), "
                f"so nothing was pulled into it; it's all still in the results bucket under {prefix}")
            return
        try:
            objects = list_objects(self.bucket, prefix)
        except StepFailed as exc:
            say(f"pull: couldn't list {prefix} in the results bucket, so nothing was pulled; it's all still "
                f"there: {exc}")
            return
        new, old = split_by_time(objects, since)
        if old:
            say(f"pull: left out {len(old)} objects under {prefix} stamped before this attempt "
                f"({since:%H:%M:%S} UTC), e.g. {old[0]['Key'][len(prefix):]} at {old[0]['LastModified']}")
        rels = [o["Key"][len(prefix):] for o in new]
        for sub, dest in (("run/", rdir), ("support/", rdir / "support"), ("", rdir / "ops" / "host")):
            keys = [k[len(sub):] for k in rels
                    if (k.startswith(sub) if sub else not k.startswith(("run/", "support/")))]
            for i in range(0, len(keys), batch):  # keeps the command line short
                only = [arg for k in keys[i:i + batch] for arg in ("--include", glob_escape(k))]
                p = aws("s3", "sync", "--only-show-errors", f"s3://{self.bucket}/{prefix}{sub}", str(dest),
                        "--exclude", "*", *only, check=False, parse=False)
                if p.returncode != 0:
                    say(f"pull {sub or 'host files'}: exit {p.returncode} {(p.stderr or p.stdout).strip()[-300:]}")
        say(f"pulled {len(new)} objects into results/{rid}/")

    def launch(self, free_vcpus: int) -> None:
        queue = queue_order(self.plan)
        running: dict[str, threading.Thread] = {}
        vcpus = {r["run"]: r["vcpus"] for r in queue}
        try:
            while queue or running:
                for n, th in list(running.items()):
                    if not th.is_alive():
                        del running[n]
                if self.stop.is_set():
                    queue = []
                in_use = sum(vcpus[n] for n in running)
                for r in next_to_start(queue, free_vcpus - in_use):
                    queue.remove(r)
                    th = threading.Thread(target=self.carry_out, args=(r,), name=r["run"], daemon=True)
                    running[r["run"]] = th
                    th.start()
                    time.sleep(self.start_gap_s)
                time.sleep(self.poll_s)
        except KeyboardInterrupt:
            self.log("interrupted: stopping the runs and tearing their hosts down")
            self.stop.set()
            for th in running.values():
                th.join()

    def sweep(self) -> list[str]:
        """Destroy what's left of the runs this launcher started, and the campaign's support security
        group too once the state holds no other run's hosts; terminate any instance still tagged
        with one of those runs. Another launcher's hosts are never touched. Returns what of this
        launcher's runs is still there (should be nothing)."""
        ours = sorted(self.started)
        try:
            others = sorted(self.stack.state_runs() - set(ours))
        except Exception as exc:
            others = None
            self.log(f"sweep: the campaign's state couldn't be read ({exc}); destroying only this launcher's hosts")
        try:
            gone = self.stack.destroy(set(ours), everything=others == [])
            self.log(f"sweep: terraform destroyed {len(gone)} resources"
                     + (f"; the state still holds hosts of {', '.join(others)}, which aren't this launcher's, so "
                        "the support security group stays" if others else ""))
        except Exception as exc:
            self.log(f"sweep: terraform destroy failed: {exc}")
        if not ours:
            return []
        try:
            still = campaign_instances(self.name, ours)
            if still:
                self.log(f"sweep: terminating {len(still)} instances still tagged with this launcher's runs: "
                         f"{instances_text(still, most=50)}")
                aws("ec2", "terminate-instances", "--instance-ids", *[i for i, _, _ in still], check=False,
                    parse=False)
                time.sleep(self.settle_s)
                still = campaign_instances(self.name, ours)
        except StepFailed as exc:
            return [f"unknown (the instances couldn't be listed: {exc})"]
        return [f"{iid} {state}" for iid, _, state in still]


# ------------------------------------------------------------------ command line
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="experiments/launch.sh", description=__doc__.splitlines()[0])
    ap.add_argument("campaign", help="the campaign definition, experiments/campaigns/<name>.json")
    ap.add_argument("--dry-run", action="store_true", help="check, plan waves and terraform plan; change nothing")
    ap.add_argument("--spent", type=float, default=None,
                    help="project spend so far in USD, when Cost Explorer can't be read")
    ap.add_argument("--runs", default=None, help="launch only these runs of the campaign (comma-separated)")
    ap.add_argument("--quota", type=int, default=None, help="with --dry-run: plan waves under this vCPU quota")
    ap.add_argument("--replace", action="store_true",
                    help=f"with --runs: launch those runs again over their earlier attempts, archiving each one's "
                         f"results/<campaign>/<run>/ and the bucket's runs/<campaign>/<run>/ under {SUPERSEDED}/ first")
    a = ap.parse_args(argv)
    if a.quota is not None and not a.dry_run:
        ap.error("--quota is only for --dry-run; a launch reads the live quota")
    if a.replace and not a.runs:
        ap.error("--replace archives the earlier attempts of the runs it launches, so name them with --runs")
    for tool in ("terraform", "aws", "git"):
        if not shutil.which(tool):
            print(f"{tool} isn't on the PATH", file=sys.stderr)
            return 2

    path = Path(a.campaign)
    try:
        camp = EXPAND.read_json(path)
    except Exception as exc:
        print(f"error: {path}: {exc}", file=sys.stderr)
        return 1
    for who, prof in (("management", MANAGEMENT_PROFILE), ("member", MEMBER_PROFILE)):
        if sh(["aws", "--profile", prof, "sts", "get-caller-identity"], check=False).returncode != 0:
            print(f"the {who}-account profile ({prof}) isn't logged in; run `aws login`", file=sys.stderr)
            return 2
    live_quota = vcpu_quota()
    quota = a.quota if a.quota is not None else live_quota
    r = EXPAND.evaluate(camp, quota if quota is not None else EXPAND.LIMITS["vcpu_quota"])
    for w in r["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    if not r["valid"]:
        for e in r["errors"]:
            print(f"error: {e}", file=sys.stderr)
        return 1
    plan = r["plan"]
    if a.runs:
        want = [x.strip() for x in a.runs.split(",") if x.strip()]
        unknown = [x for x in want if x not in {p["run"] for p in plan["runs"]}]
        if unknown:
            print(f"error: no run {', '.join(unknown)} in {camp['name']}", file=sys.stderr)
            return 1
        plan = {**plan, "runs": [p for p in plan["runs"] if p["run"] in want],
                "waves": [[n for n in w if n in want] for w in plan["waves"]]}
        plan["worst_case_usd"] = round(sum(p["usd_per_hour"] * camp["shutdown_after_minutes"] / 60
                                           for p in plan["runs"]), 2)
    print(EXPAND.report(camp, r))
    print()

    in_use = vcpus_running()
    free = (quota - in_use) if quota is not None else None
    spent = a.spent if a.spent is not None else spent_so_far()
    # The bucket the hosts write to is in the account the campaign stack assumes a role in; every
    # listing, copy and delete here goes through the member-account profile, so the two must agree.
    member, stack_acct = member_account(), stack_account()
    bucket = results_bucket(member) if member and member == stack_acct else None
    found, unlisted = earlier_attempts(camp["name"], [p["run"] for p in plan["runs"]], bucket)
    earlier = {run: what for run, (files, objects) in found.items()
               if (what := earlier_attempt(camp["name"], run, files, objects))}
    pushed = head_pushed()
    why_not = refusals(path, plan, spent=spent, free_vcpus=free, head_pushed=pushed, earlier=earlier,
                       replace=a.replace, uncommitted=uncommitted())
    if quota is None:
        why_not.append("the vCPU quota couldn't be read")
    if member is None:
        why_not.append(f"the member-account profile's ({MEMBER_PROFILE}) account couldn't be read, so an earlier "
                       "attempt in the results bucket can't be ruled out")
    elif stack_acct is None:
        why_not.append("the account the campaign stack creates hosts in couldn't be read (member_role_arn in "
                       "infra/experiments/terraform.tfvars), so the results bucket they write to isn't known")
    elif member != stack_acct:
        why_not.append(f"the member-account profile ({MEMBER_PROFILE}, FLEETKIT_AWS_PROFILE) is for another account "
                       "than the one the campaign stack creates hosts in (member_role_arn in "
                       "infra/experiments/terraform.tfvars): the results bucket checked here for earlier attempts "
                       "wouldn't be the one the hosts write to")
    for run in unlisted:
        why_not.append(f"{run}: runs/{camp['name']}/{run}/ in the results bucket couldn't be listed, so an earlier "
                       "attempt there can't be ruled out")
    try:
        still = campaign_instances(camp["name"])
    except StepFailed:
        still = None
        why_not.append(f"the account's instances couldn't be listed, so another launcher of {camp['name']} still "
                       "running can't be ruled out")
    if still:
        why_not.append(f"instances of {camp['name']} are still there ({instances_text(still)}): another launcher of "
                       "it may be running, and this one would share its Terraform state, whose changes would destroy "
                       "them. Launch once they have ended (every host terminates itself after "
                       "shutdown_after_minutes)")
    cap = EXPAND.LIMITS["project_cap_usd"]
    print(f"Account: vCPU quota {live_quota if live_quota is not None else 'unknown'}"
          f"{f' (planning under {quota})' if a.quota is not None else ''}, {in_use} vCPUs running now, "
          f"{free if free is not None else '?'} free. Spent ${spent if spent is not None else '?'} of the "
          f"${cap:,.0f} cap{' (--spent)' if a.spent is not None else ' (Cost Explorer; lags up to a day)'}; "
          f"this campaign's worst case is ${plan['worst_case_usd']:,.2f}.")
    preview = archive_preview(camp["name"], {run: found[run] for run in earlier})
    if preview:
        print(f"\n{'--replace archives' if a.replace else 'With --replace, a launch would archive'} each earlier "
              "attempt just before that run's hosts are created (<UTC> is when):")
        for line in preview:
            print(f"  {line}")
    head = head_commit()
    log = Log(None if a.dry_run else RESULTS / camp["name"] / "launch.log")
    log.hide(bucket)
    for account in (member, stack_acct):  # an AWS error message can quote an ARN
        if account and account.isdigit():
            log.hide(account, HIDDEN_ACCOUNT)
    stack = Stack(camp["name"], camp["shutdown_after_minutes"], head, log)

    if a.dry_run:
        print(f"\nterraform plan of every run's hosts at once (commit {head[:12]}; no lock, nothing applied):")
        try:
            stack.init()
            try:
                stale = stale_hosts(stack, camp["name"])
                if stale:
                    print(f"  the campaign's state still holds hosts of {', '.join(sorted(stale))}, none of them still "
                          "there; a launch clears them from it first")
            except Refused as exc:
                if not still:  # else the instances still there are named above
                    why_not.append(str(exc))
            except StepFailed:
                why_not.append("the campaign's Terraform state, or the instances of the hosts it holds, couldn't be "
                               f"read, so another launcher of {camp['name']} still running can't be ruled out")
            runs = {p["run"]: {"worker_instance_type": p["worker_instance_type"],
                               "support_instance_type": p["support_instance_type"]} for p in plan["runs"]}
            _, changes, text = stack.plan(runs, lock=False)
            summary = next((line for line in text.splitlines() if line.startswith("Plan:")), "no changes")
            print(f"  {summary}")
            for addr, act in changes:
                print(f"  {act:7} {addr}")
        except StepFailed as exc:
            print(f"  terraform plan failed: {exc}")
            why_not.append("terraform plan failed")
        if why_not:
            print("\nA launch would be refused:")
            for w in why_not:
                print(f"  - {w}")
            return 1
        print("\nA launch would go ahead.")
        return 0

    if why_not:
        print("Refused:", file=sys.stderr)
        for w in why_not:
            print(f"  - {w}", file=sys.stderr)
        return 1

    cdir = RESULTS / camp["name"]
    cdir.mkdir(parents=True, exist_ok=True)
    as_launched = path.read_bytes()
    if (cdir / "campaign.json").exists() and (cdir / "campaign.json").read_bytes() != as_launched:
        print(f"{cdir}/campaign.json holds a different definition of {camp['name']}", file=sys.stderr)
        return 1
    (cdir / "campaign.json").write_bytes(as_launched)
    log(f"launching {camp['name']}: {len(plan['runs'])} runs, commit {head[:12]}, worst case "
        f"${plan['worst_case_usd']:,.2f}, {free} vCPUs free")
    c = Campaign(camp, plan, log, stack, head, replace=a.replace)
    try:
        stack.init()
        stale = stale_hosts(stack, camp["name"])  # another launcher may have started since the check
        if stale:
            log(f"the campaign's state still holds hosts of {', '.join(sorted(stale))}, none of them still there "
                "(a launcher stopped before its sweep): clearing them from it")
            stack.destroy(stale, everything=True)
        stack.setup()
        c.bucket = stack.bucket()
        log.hide(c.bucket)
        if c.bucket != bucket:  # the runs' prefixes were checked in the bucket the launcher named
            raise StepFailed("the campaign stack's results bucket isn't the one the launcher checked; not launching")
        c.launch(free)
    except KeyboardInterrupt:
        c.stop.set()
    except Refused as exc:
        log(f"REFUSED: {exc}")
    except Exception as exc:
        log(f"launcher error: {type(exc).__name__}: {exc}")
    finally:
        left = c.sweep()
        log("sweep: nothing this launcher started is left running" if not left else
            f"SWEEP: STILL RUNNING: {', '.join(left)}")
    log("runs: " + "; ".join(f"{n}: {s}" for n, s in sorted(c.status.items())))
    return 0 if all(s == "complete" for s in c.status.values()) and len(c.status) == len(plan["runs"]) and not left \
        else 1


if __name__ == "__main__":
    sys.exit(main())
