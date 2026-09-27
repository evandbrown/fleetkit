#!/usr/bin/env python3
"""Launch a campaign: one command from a campaign definition to results/<campaign>/.

    experiments/launch.sh CAMPAIGN.json [--dry-run] [--spent USD] [--runs RUN,RUN] [--quota N]

1. Check. The campaign is expanded and checked by experiments/schema/expand.py. The launcher
   refuses it if it isn't valid; if it's under experiments/campaigns/examples/ (written, not
   approved); if its worst case is above the limit for one campaign, or would cross what's left of
   the project cap (spend read from Cost Explorer, or --spent when that can't be read); if a run
   needs more vCPUs than the quota leaves free; if a worker host isn't allowed by the account's
   instance-type guardrail; if HEAD isn't pushed or the files the hosts run have uncommitted
   changes (the hosts check out HEAD from GitHub); or if the campaign's results directory already
   holds one of its runs.
2. Plan. Runs start in the order of expand.py's waves, each as soon as its worker and support
   hosts' vCPUs fit under the live quota, less what's already running in the account.
3. Run. For each run: create its own worker host and support host (Terraform,
   infra/experiments/campaign, one state per campaign, keyed by run name), set both up over SSM,
   carry out the run's spec (images/host/stage.sh run), bundle it, pull it into
   results/<campaign>/<run>/, and destroy its hosts as soon as it ends. One run failing doesn't
   stop the others. Every host also shuts itself down shutdown_after_minutes after boot.
4. Sweep. Destroy what's left in the campaign's state, terminate any instance still tagged with
   the campaign, and check nothing of it is left running.

--dry-run does 1 and 2 and a `terraform plan` of every run's hosts at once (no lock, no apply),
and changes nothing. Operational events go to results/<campaign>/launch.log and each run's
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
             head_pushed: bool, existing_runs: list[str], uncommitted: list[str] = ()) -> list[str]:
    """Every reason not to launch this (valid) campaign plan; empty when it may launch."""
    out = []
    limits = EXPAND.LIMITS
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
    for run in existing_runs:
        out.append(f"results/{plan['campaign']}/{run}/ already exists; launch that run again under a new "
                   "campaign name, or move the directory")
    return list(dict.fromkeys(out))


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


class Log:
    def __init__(self, path: Path | None):
        self.path = path
        self.lock = threading.Lock()

    def __call__(self, msg: str, run: str | None = None) -> None:
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

    def plan(self, runs: dict, *, destroy: bool = False, lock: bool = True, refresh: bool = True):
        out = self.data_dir / "tfplan"
        args = ["plan", "-input=false", "-no-color", f"-out={out}", *self._var_args(runs)]
        args += [] if lock else ["-lock=false"]
        args += [] if refresh else ["-refresh=false"]
        args += ["-destroy"] if destroy else []
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

    def destroy_all(self) -> list[tuple[str, str]]:
        with self.lock:
            planfile, changes, _ = self.plan({}, destroy=True)
            bad = [c for c in changes if c[1] != "delete"]
            if bad:
                raise StepFailed(f"terraform (destroy): unexpected {bad}; not applied")
            if changes:
                self.tf("apply", "-input=false", "-no-color", str(planfile))
            self.runs = {}
            return changes

    def bucket(self) -> str:
        return json.loads(self.tf("output", "-json", "results_bucket").stdout)


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

    def __init__(self, camp: dict, plan: dict, log: Log, stack: Stack, head: str):
        self.camp, self.plan, self.log, self.stack, self.head = camp, plan, log, stack, head
        self.name = camp["name"]
        self.dir = RESULTS / self.name
        self.timer = camp["shutdown_after_minutes"]
        self.stop = threading.Event()
        self.bucket = ""
        self.status: dict[str, str] = {}

    def carry_out(self, run: dict) -> None:
        name = run["run"]
        rid = f"{self.name}/{name}"
        rdir = self.dir / name
        ops = rdir / "ops"
        say = lambda m: self.log(m, name)  # noqa: E731
        created = False
        started = time.time()
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
            say(f"creating a {run['worker_instance_type']} worker host and a {run['support_instance_type']} "
                "support host")
            created = True  # from here on the run's hosts are destroyed at the end, even if this apply fails midway
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
                self.pull(rid, rdir, say)
                for line in (rdir / "ops.jsonl").read_text().splitlines() if (rdir / "ops.jsonl").exists() else []:
                    e = json.loads(line)
                    say("ops: " + " ".join(f"{k}={e[k]}" for k in ("event", "trial_id", "density", "cause", "next")
                                          if e.get(k) is not None))
            got = run_status(EXPAND.read_json(rdir / "run.json") if (rdir / "run.json").exists() else None)
            self.status.setdefault(name, got)
            if self.status[name] != got:
                self.status[name] += f" ({got})"
            say(f"ended: {self.status[name]} after {int((time.time() - started) / 60)} min")

    def pull(self, rid: str, rdir: Path, say) -> None:
        src = f"s3://{self.bucket}/runs/{rid}"
        for sub, dest, extra in (("run/", rdir, []), ("support/", rdir / "support", []),
                                 ("", rdir / "ops" / "host", ["--exclude", "run/*", "--exclude", "support/*"])):
            p = aws("s3", "sync", "--quiet", f"{src}/{sub}", str(dest), *extra, check=False, parse=False)
            if p.returncode != 0:
                say(f"pull {sub or 'host files'}: exit {p.returncode} {p.stderr.strip()[-200:]}")
        say(f"pulled into results/{rid}/")

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
        """Destroy what's left in the state, terminate anything still tagged with the campaign, and
        return what is still running (should be nothing)."""
        try:
            gone = self.stack.destroy_all()
            self.log(f"sweep: terraform destroyed {len(gone)} resources")
        except Exception as exc:
            self.log(f"sweep: terraform destroy failed: {exc}")
        tag = [f"Name=tag:Campaign,Values={self.name}"]
        live = ["Name=instance-state-name,Values=pending,running,stopping,stopped"]
        ids = aws("ec2", "describe-instances", "--filters", *tag, *live,
                  "--query", "Reservations[].Instances[].InstanceId", check=False) or []
        if ids:
            self.log(f"sweep: terminating {len(ids)} instances still tagged Campaign={self.name}: {' '.join(ids)}")
            aws("ec2", "terminate-instances", "--instance-ids", *ids, check=False, parse=False)
            time.sleep(20)
        left = aws("ec2", "describe-instances", "--filters", *tag, *live,
                   "--query", "Reservations[].Instances[].[InstanceId, State.Name]", check=False) or []
        return [f"{i} {s}" for i, s in left]


# ------------------------------------------------------------------ command line
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="experiments/launch.sh", description=__doc__.splitlines()[0])
    ap.add_argument("campaign", help="the campaign definition, experiments/campaigns/<name>.json")
    ap.add_argument("--dry-run", action="store_true", help="check, plan waves and terraform plan; change nothing")
    ap.add_argument("--spent", type=float, default=None,
                    help="project spend so far in USD, when Cost Explorer can't be read")
    ap.add_argument("--runs", default=None, help="launch only these runs of the campaign (comma-separated)")
    ap.add_argument("--quota", type=int, default=None, help="with --dry-run: plan waves under this vCPU quota")
    a = ap.parse_args(argv)
    if a.quota is not None and not a.dry_run:
        ap.error("--quota is only for --dry-run; a launch reads the live quota")
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
    existing = [p["run"] for p in plan["runs"] if (RESULTS / camp["name"] / p["run"]).exists()]
    pushed = head_pushed()
    why_not = refusals(path, plan, spent=spent, free_vcpus=free, head_pushed=pushed, existing_runs=existing,
                       uncommitted=uncommitted())
    if quota is None:
        why_not.append("the vCPU quota couldn't be read")
    cap = EXPAND.LIMITS["project_cap_usd"]
    print(f"Account: vCPU quota {live_quota if live_quota is not None else 'unknown'}"
          f"{f' (planning under {quota})' if a.quota is not None else ''}, {in_use} vCPUs running now, "
          f"{free if free is not None else '?'} free. Spent ${spent if spent is not None else '?'} of the "
          f"${cap:,.0f} cap{' (--spent)' if a.spent is not None else ' (Cost Explorer; lags up to a day)'}; "
          f"this campaign's worst case is ${plan['worst_case_usd']:,.2f}.")
    head = head_commit()
    log = Log(None if a.dry_run else RESULTS / camp["name"] / "launch.log")
    stack = Stack(camp["name"], camp["shutdown_after_minutes"], head, log)

    if a.dry_run:
        print(f"\nterraform plan of every run's hosts at once (commit {head[:12]}; no lock, nothing applied):")
        try:
            stack.init()
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
    c = Campaign(camp, plan, log, stack, head)
    try:
        stack.init()
        stack.setup()
        c.bucket = stack.bucket()
        c.launch(free)
    except KeyboardInterrupt:
        c.stop.set()
    except Exception as exc:
        log(f"launcher error: {type(exc).__name__}: {exc}")
    finally:
        left = c.sweep()
        log("sweep: nothing of the campaign is left running" if not left else
            f"SWEEP: STILL RUNNING: {', '.join(left)}")
    log("runs: " + "; ".join(f"{n}: {s}" for n, s in sorted(c.status.items())))
    return 0 if all(s == "complete" for s in c.status.values()) and len(c.status) == len(plan["runs"]) and not left \
        else 1


if __name__ == "__main__":
    sys.exit(main())
