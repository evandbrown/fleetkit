"""``driver bundle``: assemble results/<run-id>/ into the evidence bundle of design section 11,
with manifest.json and evidence.md. Succeeds on a partial run directory and lists missing items;
with --strict it exits non-zero if any mandatory item is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from . import __version__
from .evidence import line_carries_run_id, iter_lines
from .outputs import RunDir, read_csv, read_json, write_json_atomic

REPO_ROOT = Path(__file__).resolve().parents[3]
OTLP_FILES = ("traces.jsonl", "metrics.jsonl", "logs.jsonl")


def default_sibling(rundir: RunDir, given: str | None, name: str) -> str | None:
    """results/<run-id>/../<name> (results/lgtm, results/hostd as the Makefile lays them out) when it exists."""
    if given:
        return given
    cand = rundir.root.parent / name
    return str(cand) if cand.exists() else None


def copy_otlp(lgtm_dir: Path, rundir: RunDir) -> dict:
    """Copy the OTLP-JSON lines carrying this run's fleetkit.run_id into results/<run-id>/otlp/."""
    src = Path(lgtm_dir) / "otlp"
    out = {"source": str(src), "files": {}}
    if not src.exists():
        out["error"] = "no otlp directory"
        return out
    rundir.otlp_dir.mkdir(parents=True, exist_ok=True)
    for name in OTLP_FILES:
        sp = src / name
        if not sp.exists():
            continue
        kept = 0
        with open(rundir.otlp_dir / name, "w", encoding="utf-8") as fh:
            for obj, line in iter_lines(sp):
                if line_carries_run_id(obj, rundir.run_id):
                    fh.write(line + "\n")
                    kept += 1
        out["files"][name] = kept
    return out


def snapshot_lgtm(lgtm_dir: Path, compose_file: Path, rundir: RunDir, log) -> dict:
    """compose stop lgtm, tar results/lgtm/data into lgtm-data.tgz, compose start lgtm."""
    data = Path(lgtm_dir) / "data"
    res = {"path": str(rundir.root / "lgtm-data.tgz")}
    if not data.exists():
        res["error"] = f"{data} does not exist"
        return res
    compose = ["docker", "compose", "-f", str(compose_file)]
    try:
        subprocess.run(compose + ["stop", "lgtm"], check=True, capture_output=True, timeout=120)
    except Exception as exc:
        res["error"] = f"compose stop failed: {exc}"
        return res
    try:
        with tarfile.open(rundir.root / "lgtm-data.tgz", "w:gz") as tf:
            tf.add(data, arcname="data")
        res["bytes"] = (rundir.root / "lgtm-data.tgz").stat().st_size
    except Exception as exc:
        res["error"] = f"tar failed: {exc}"
    finally:
        try:
            subprocess.run(compose + ["start", "lgtm"], check=True, capture_output=True, timeout=120)
        except Exception as exc:
            res["restart_error"] = f"compose start failed: {exc}"
            if log:
                log.error(res["restart_error"])
    return res


def _parse_env(path: Path | None) -> dict:
    out = {}
    if not path or not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _git_commit() -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            commit = r.stdout.strip()
            d = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10)
            if d.returncode == 0 and d.stdout.strip():
                commit += "-dirty"
            return commit
    except Exception:
        pass
    return None


def _find_key(d: dict, *needles: str):
    """First value whose lower-cased key contains every needle."""
    for k, v in (d or {}).items():
        lk = k.lower()
        if all(n in lk for n in needles):
            return v
    return None


def _guest_manifest_fields(guest: dict) -> dict:
    """Read the manifest images/guest/build-rootfs.sh writes (``rootfs: {sha256, ...}``,
    ``base_image: <ref>@sha256:<digest>``, ``chromium_version``, ``image_id``), with flat
    ``rootfs_sha256`` / ``base_digest`` keys and a top-level key search as fallbacks."""
    if not isinstance(guest, dict):
        return {}
    rootfs = guest.get("rootfs") if isinstance(guest.get("rootfs"), dict) else {}
    base_image = str(guest.get("base_image") or "")
    return {
        "rootfs_sha256": guest.get("rootfs_sha256") or rootfs.get("sha256") or _find_key(guest, "rootfs", "sha256"),
        "base_digest": (guest.get("base_digest") or (base_image.split("@", 1)[1] if "@" in base_image else None)
                        or _find_key(guest, "base", "digest")),
        "base_image": base_image or None,
        "chromium_version": guest.get("chromium_version") or _find_key(guest, "chromium"),
        "image_id": guest.get("image_id"),
        "arch": guest.get("arch"),
        "rootfs_size_bytes": rootfs.get("size_bytes"),
    }


HOST_INFO_FACTS = ("cpu_model", "cpu_count", "threads_per_core", "cores_per_socket", "sockets", "mem_total",
                   "kernel_release", "virtualized", "kvm")


def _host_info(run_json: dict) -> dict:
    """run.json ``inputs.host_info`` (the host daemon's GET /host/info at run start), or {}."""
    inputs = run_json.get("inputs") if isinstance(run_json.get("inputs"), dict) else {}
    hi = inputs.get("host_info")
    return hi if isinstance(hi, dict) else {}


def build_manifest(rundir: RunDir, trials: list[dict], opts: dict) -> dict:
    lock = _parse_env(opts.get("lock_env"))
    guest = read_json(Path(opts["guest_manifest"]), {}) if opts.get("guest_manifest") else {}
    if not isinstance(guest, dict):
        guest = {}
    gm = _guest_manifest_fields(guest)
    ami = read_json(Path(opts["ami_lock"]), {}) if opts.get("ami_lock") else {}
    run_json = read_json(rundir.run_json, {}) or {}
    starts = [t["timestamps"].get("create_start") for t in trials if t.get("timestamps", {}).get("create_start")]
    ends = [t["timestamps"].get("verify_clean_pass") or t.get("written_at") for t in trials]
    ends = [e for e in ends if e]
    timeouts = [t.get("timeouts") for t in trials if t.get("timeouts")]
    uniq = []
    for t in timeouts:
        if t not in uniq:
            uniq.append(t)
    debian = lock.get("DEBIAN_IMAGE", "")
    kernel_sha = {k: v for k, v in lock.items() if "KERNEL" in k and "SHA256" in k}
    prior = read_json(rundir.manifest_json, {}) or {}
    hi = _host_info(run_json)
    ec2 = hi.get("ec2") if isinstance(hi.get("ec2"), dict) else {}
    fc = hi.get("firecracker") if isinstance(hi.get("firecracker"), dict) else {}
    manifest = {
        "run_id": rundir.run_id,
        "driver_version": __version__,
        "git_commit": opts.get("git_commit") or _git_commit(),
        "guest_image_base_digest": gm.get("base_digest") or (debian.split("@", 1)[1] if "@" in debian else None),
        "guest_image_base": gm.get("base_image") or debian or None,
        "guest_image_id": gm.get("image_id"),
        "guest_arch": gm.get("arch"),
        "guest_chromium_version": gm.get("chromium_version"),
        "rootfs_sha256": gm.get("rootfs_sha256"),
        "rootfs_size_bytes": gm.get("rootfs_size_bytes"),
        "kernel_version": lock.get("KERNEL_VERSION"),
        "kernel_sha256": kernel_sha or (guest.get("kernel_sha256") if guest else None) or None,
        "firecracker_version": lock.get("FIRECRACKER_VERSION") or _find_key(guest, "firecracker"),
        "ami_id": (ami.get("ami_id") or ami.get("id") or ami.get("image_id") or _find_key(ami, "ami")
                   if isinstance(ami, dict) else None) or opts.get("ami_id"),
        "instance_type": opts.get("instance_type") or run_json.get("instance_type"),
        "vcpu_quota": opts.get("vcpu_quota") if opts.get("vcpu_quota") is not None else run_json.get("vcpu_quota"),
        "host_provisioning_s": opts.get("host_provisioning_s") if opts.get("host_provisioning_s") is not None
        else run_json.get("host_provisioning_s"),
        "host_id": run_json.get("host_id") or next((t.get("host_id") for t in trials if t.get("host_id")), None),
        "run_start_ts": min(starts) if starts else run_json.get("started_ts"),
        "run_end_ts": max(ends) if ends else None,
        "timeout_parameters": uniq[0] if len(uniq) == 1 else uniq,
        "backends": sorted({t["backend"] for t in trials}),
        "trials": [{"trial_id": t["trial_id"], "backend": t["backend"], "level_n": t["level_n"],
                    "repeat": t.get("repeat"), "fault": t.get("fault"), "status": t.get("status"),
                    "level_passed": t.get("level_passed"), "kind": t.get("kind"),
                    "passed": (t.get("evaluation") or {}).get("passed")} for t in trials],
        "bundled_at": time.time(),
        "labels": {"git_commit": "measured", "timestamps": "measured",
                   "instance_type": "operator input, else the host daemon's GET /host/info (IMDS)",
                   "ami_id": "AMI lock or operator input, else GET /host/info (IMDS)",
                   "host facts": "GET /host/info at run start (run.json inputs.host_info)",
                   "vcpu_quota": "operator input at run time", "prices": "not in this file; see report inputs"},
    }
    # cpu and memory facts of the host the run measured, from the host daemon (null on old runs);
    # host_kernel_release is the host's kernel, kernel_version the guest's
    for k in HOST_INFO_FACTS:
        manifest["host_" + k] = hi.get(k)
    # keep operator-provided values from earlier bundles when this call did not supply them
    for k in ("instance_type", "vcpu_quota", "ami_id", "host_provisioning_s", "rootfs_sha256", "rootfs_size_bytes",
              "guest_image_base_digest", "guest_image_id", "guest_arch", "guest_chromium_version",
              "firecracker_version", "kernel_sha256") + tuple("host_" + k for k in HOST_INFO_FACTS):
        if manifest.get(k) in (None, {}, "") and prior.get(k) not in (None, {}, ""):
            manifest[k] = prior[k]
    # what the operator did not give, from the host daemon's view of the host (IMDS on EC2)
    for k, v in (("instance_type", ec2.get("instance_type")), ("ami_id", ec2.get("ami_id")),
                 ("firecracker_version", fc.get("version"))):
        if manifest.get(k) in (None, {}, "") and v not in (None, ""):
            manifest[k] = v
    return manifest


def _copy_into(src: Path | None, dst: Path, log) -> bool:
    if not src:
        return False
    src = Path(src)
    if not src.exists():
        return False
    try:
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            for p in src.iterdir():
                if p.is_file():
                    shutil.copy2(p, dst / p.name)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
        return True
    except Exception as exc:
        if log:
            log.warn(f"copy {src} -> {dst} failed: {exc}")
        return False


def run_bundle(run: str, strict: bool = False, lgtm_dir: str | None = None, snapshot: bool = False,
               compose_file: str | None = None, hostd_dir: str | None = None, hostd_log: str | None = None,
               console_logs_dir: str | None = None, cloud_init_log: str | None = None,
               hostcheck_output: str | None = None, aws: bool = False, lock_env: str | None = None,
               guest_manifest: str | None = None, ami_lock: str | None = None, instance_type: str | None = None,
               vcpu_quota: int | None = None, host_provisioning_s: float | None = None,
               git_commit: str | None = None, ami_id: str | None = None, log=None) -> dict:
    rundir = RunDir(Path(run))
    notes = []
    trials = rundir.list_trials()
    lgtm_dir = default_sibling(rundir, lgtm_dir, "lgtm")
    hostd_dir = default_sibling(rundir, hostd_dir, "hostd")

    # host daemon files
    hd = Path(hostd_dir) if hostd_dir else None
    if hd and hd.exists():
        for name, dst in (("hostd.log", rundir.root / "hostd.log"), ("spans.jsonl", rundir.root / "hostd-spans.jsonl"),
                          ("logs.jsonl", rundir.root / "hostd-logs.jsonl"),
                          ("hostd-spans.jsonl", rundir.root / "hostd-spans.jsonl"),
                          ("hostd-logs.jsonl", rundir.root / "hostd-logs.jsonl")):
            if (hd / name).exists():
                _copy_into(hd / name, dst, log)
        for sub in ("console", "console-logs"):
            if (hd / sub).exists():
                _copy_into(hd / sub, rundir.console_logs_dir, log)
                break
        # hostd writes sessions/<id>/console.log (and firecracker.log on that backend)
        if (hd / "sessions").exists():
            for sdir in sorted(p for p in (hd / "sessions").iterdir() if p.is_dir()):
                for name in ("console.log", "firecracker.log"):
                    if (sdir / name).exists():
                        _copy_into(sdir / name, rundir.console_logs_dir / sdir.name / name, log)
    if hostd_log:
        _copy_into(Path(hostd_log), rundir.root / "hostd.log", log)
    if console_logs_dir:
        _copy_into(Path(console_logs_dir), rundir.console_logs_dir, log)
    if cloud_init_log:
        _copy_into(Path(cloud_init_log), rundir.root / "cloud-init-output.log", log)
    if hostcheck_output:
        _copy_into(Path(hostcheck_output), rundir.root / "hostcheck-output.txt", log)

    # collector copies
    otlp = None
    if lgtm_dir:
        otlp = copy_otlp(Path(lgtm_dir), rundir)
        notes.append(f"otlp: {json.dumps(otlp)}")
    snap = None
    if snapshot and lgtm_dir:
        snap = snapshot_lgtm(Path(lgtm_dir), Path(compose_file or REPO_ROOT / "observability" / "compose.yaml"), rundir, log)
        notes.append(f"lgtm snapshot: {json.dumps(snap)}")

    manifest = build_manifest(rundir, trials, {
        "lock_env": lock_env or (str(REPO_ROOT / "images" / "lock.env") if (REPO_ROOT / "images" / "lock.env").exists() else None),
        "guest_manifest": guest_manifest, "ami_lock": ami_lock, "instance_type": instance_type,
        "vcpu_quota": vcpu_quota, "host_provisioning_s": host_provisioning_s, "git_commit": git_commit,
        "ami_id": ami_id})
    write_json_atomic(rundir.manifest_json, manifest)

    inventory = check_inventory(rundir, trials, aws=aws, collector_ran=bool(otlp and otlp.get("files")),
                                snapshotted=bool(snap and not snap.get("error")))
    evidence = render_evidence(rundir, trials, manifest, inventory, notes)
    rundir.evidence_md.write_text(evidence, encoding="utf-8")
    result = {"run_id": rundir.run_id, "manifest": manifest, "inventory": inventory, "notes": notes,
              "ok": not inventory["mandatory_missing"], "strict": strict}
    return result


def check_inventory(rundir: RunDir, trials: list[dict], aws: bool, collector_ran: bool, snapshotted: bool) -> dict:
    r = rundir.root
    mandatory = {
        "sessions.csv": rundir.sessions_csv.exists(), "tasks.csv": rundir.tasks_csv.exists(),
        "steps.csv": rundir.steps_csv.exists(), "host_metrics.csv": rundir.host_metrics_csv.exists(),
        "trials/*/trial.json": bool(trials), "driver.log": rundir.driver_log.exists(),
        "spans.jsonl": rundir.spans_jsonl.exists(), "logs.jsonl": rundir.logs_jsonl.exists(),
        "manifest.json": rundir.manifest_json.exists(), "evidence.md": True,  # written right after this check
        "hostd.log": (r / "hostd.log").exists(),
    }
    tasks = read_csv(rundir.tasks_csv)
    shots_expected = [t["screenshot_path"] for t in tasks if t.get("screenshot_path")]
    shots_missing = [p for p in shots_expected if not (r / p).exists()]
    mandatory["screenshots for every task that reached the screenshot phase"] = not shots_missing
    sessions = read_csv(rundir.sessions_csv)
    # Section 11: the bundle always contains every VM console log and per-session guest log tail.
    # hostd writes sessions/<id>/console.log on both backends; the guest log tail exists for every
    # task the guest answered, i.e. every category but guest_unreachable and session_not_ready.
    console_ids = []
    for s in sessions:
        sid = s.get("session_id")
        if sid and sid not in console_ids:
            console_ids.append(sid)
    console_missing = [sid for sid in console_ids if not (rundir.console_logs_dir / sid / "console.log").exists()]
    guest_ids = []
    for t in tasks:
        sid = t.get("session_id")
        if sid and t.get("failure_category") not in ("guest_unreachable", "session_not_ready") and sid not in guest_ids:
            guest_ids.append(sid)
    guest_missing = [sid for sid in guest_ids if not (rundir.guest_logs_dir / f"{sid}.jsonl").exists()]
    mandatory[_ids_label("console-logs/<session_id>/console.log for every session in sessions.csv",
                         console_ids, console_missing)] = not console_missing
    mandatory[_ids_label("guest-logs/<session_id>.jsonl for every session whose guest answered a task",
                         guest_ids, guest_missing)] = not guest_missing
    optional = {
        "otlp/ (collector copies)": rundir.otlp_dir.exists() and any(rundir.otlp_dir.iterdir()),
        "lgtm-data.tgz": (r / "lgtm-data.tgz").exists(),
        "hostd-spans.jsonl": (r / "hostd-spans.jsonl").exists(),
        "hostd-logs.jsonl": (r / "hostd-logs.jsonl").exists(),
        "report.md": rundir.report_md.exists(),
        "smoke-report.md": rundir.smoke_report_md.exists(),
        "cloud-init-output.log": (r / "cloud-init-output.log").exists(),
        "hostcheck-output.txt": (r / "hostcheck-output.txt").exists(),
    }
    if collector_ran:
        mandatory["otlp/ (collector ran)"] = optional.pop("otlp/ (collector copies)")
    if snapshotted:
        mandatory["lgtm-data.tgz (snapshotted)"] = optional.pop("lgtm-data.tgz")
    if aws:
        mandatory["cloud-init-output.log (AWS)"] = optional.pop("cloud-init-output.log")
        mandatory["hostcheck-output.txt (AWS)"] = optional.pop("hostcheck-output.txt")
    files = []
    for p in sorted(r.rglob("*")):
        if p.is_file():
            files.append({"path": str(p.relative_to(r)), "bytes": p.stat().st_size})
    return {"mandatory": mandatory, "optional": optional,
            "mandatory_missing": [k for k, v in mandatory.items() if not v],
            "optional_absent": [k for k, v in optional.items() if not v],
            "screenshots_expected": len(shots_expected), "screenshots_missing": shots_missing,
            "console_logs_expected": len(console_ids), "console_logs_missing": console_missing,
            "guest_logs_expected": len(guest_ids), "guest_logs_missing": guest_missing,
            "files": files, "total_bytes": sum(f["bytes"] for f in files)}


def _ids_label(base: str, expected: list[str], missing: list[str]) -> str:
    """Inventory label for a per-session item; names the missing ids so evidence.md and
    ``mandatory_missing`` say which sessions lack it."""
    if not missing:
        return f"{base} ({len(expected)} present)"
    shown = ", ".join(missing[:10]) + (f", ... {len(missing) - 10} more" if len(missing) > 10 else "")
    return f"{base} (missing {len(missing)} of {len(expected)}: {shown})"


def render_evidence(rundir: RunDir, trials: list[dict], manifest: dict, inv: dict, notes: list[str]) -> str:
    L = [f"# Evidence: run {rundir.run_id}", "",
         f"Bundled {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(manifest['bundled_at']))}; git commit "
         f"{manifest.get('git_commit') or 'unknown'}; backends {', '.join(manifest.get('backends') or []) or 'none'}; "
         f"driver {manifest['driver_version']}.", "", "## What ran", ""]
    if not trials:
        L.append("No trials in this run directory.")
    else:
        L += ["| trial | backend | n | repeat | fault | status | complete | sessions ready | tasks ok | clean | level passed |",
              "|---|---|---:|---:|---|---|---|---|---|---|---|"]
        for t in trials:
            c = t.get("counts") or {}
            vc = (t.get("verify_clean") or {}).get("clean")
            L.append(f"| {t['trial_id']} | {t['backend']} | {t['level_n']} | {t.get('repeat')} | {t.get('fault') or ''} | "
                     f"{t.get('status')} | {t.get('complete')} | {c.get('sessions_ready')}/{c.get('sessions_requested')} | "
                     f"{c.get('tasks_ok')}/{c.get('tasks_dispatched')} | {vc} | {t.get('level_passed')} |")
    cases = sorted(rundir.trials_dir.glob("*/case.json")) if rundir.trials_dir.exists() else []
    if cases:
        L += ["", "Smoke session cases:", ""]
        for p in cases:
            c = read_json(p, {}) or {}
            L.append(f"- {c.get('trial_id')}: {c.get('case')} expected {c.get('expected_outcome')}, got {c.get('outcome')} "
                     f"({'pass' if c.get('passed') else 'FAIL'})")
    L += ["", "## What passed", ""]
    passed = {}
    for t in trials:
        if t.get("fault"):
            continue
        b = t["backend"]
        if t.get("level_passed"):
            passed[b] = max(passed.get(b, 0), int(t["level_n"]))
        passed.setdefault(b, passed.get(b, 0))
    if passed:
        for b, n in passed.items():
            L.append(f"- {b}: highest N tested successfully (protocol only; targets are applied by `driver report`) = {n}")
    else:
        L.append("- no level passed")
    st = read_json(rundir.smoke_state_json, None)
    if st:
        last = st["runs"][-1] if st.get("runs") else {}
        L.append(f"- smoke: last run {'GREEN' if last.get('green') else 'RED'}, consecutive green {st.get('consecutive_green')}, "
                 f"{len(st.get('runs', []))} run(s), {st.get('cumulative_s', 0) / 60:.1f} min cumulative (see smoke-report.md)")
    else:
        L.append("- smoke: not run in this run directory")
    L += ["", "## What is missing", ""]
    if inv["mandatory_missing"]:
        for m in inv["mandatory_missing"]:
            L.append(f"- MANDATORY: {m}")
    else:
        L.append("- no mandatory item is missing")
    for m in inv["optional_absent"]:
        L.append(f"- optional, absent: {m}")
    if inv["screenshots_missing"]:
        L.append(f"- screenshots referenced but absent: {', '.join(inv['screenshots_missing'][:10])}")
    if inv.get("console_logs_missing"):
        L.append(f"- sessions without a console log: {', '.join(inv['console_logs_missing'][:20])}")
    if inv.get("guest_logs_missing"):
        L.append(f"- sessions without a guest log tail: {', '.join(inv['guest_logs_missing'][:20])}")
    L += ["", "## Manifest", "", "```json", json.dumps({k: v for k, v in manifest.items() if k != "trials"},
                                                        indent=2, sort_keys=True, default=str), "```", ""]
    if notes:
        L += ["## Bundle notes", ""] + [f"- {n}" for n in notes] + [""]
    L += ["## Files", "", f"{len(inv['files'])} files, {inv['total_bytes']} bytes", ""]
    for f in inv["files"][:400]:
        L.append(f"- {f['path']} ({f['bytes']} B)")
    if len(inv["files"]) > 400:
        L.append(f"- ... {len(inv['files']) - 400} more")
    L.append("")
    return "\n".join(L)
