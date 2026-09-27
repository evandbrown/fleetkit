from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from driver.bundle import REPO_ROOT, build_manifest, copy_otlp, run_bundle
from driver.outputs import RunDir, read_csv, read_json
from tests.conftest import common, run_cli


def _otlp_line(run_id: str, kind: str = "spans") -> str:
    res = {"attributes": [{"key": "service.name", "value": {"stringValue": "hostd"}},
                          {"key": "fleetkit.run_id", "value": {"stringValue": run_id}}]}
    if kind == "spans":
        return json.dumps({"resourceSpans": [{"resource": res, "scopeSpans": [{"spans": [
            {"traceId": "ab" * 16, "spanId": "cd" * 8, "name": "x", "startTimeUnixNano": "1", "endTimeUnixNano": "2"}]}]}]})
    return json.dumps({"resourceLogs": [{"resource": res, "scopeLogs": [{"logRecords": [
        {"timeUnixNano": "1", "body": {"stringValue": "hi"}, "traceId": "ab" * 16}]}]}]})


def test_bundle_partial_then_strict(hostd, fixture_site, tmp_path):
    out = tmp_path / "results" / "run-b"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "2") == 0
    # partial: no hostd.log yet -> non-strict succeeds and lists it, strict fails
    assert run_cli("bundle", "--run", str(out), "--quiet") == 0
    ev = (out / "evidence.md").read_text()
    assert "MANDATORY: hostd.log" in ev and "## What ran" in ev and "| d2-t1 | ladder | docker | 2 | 1 |" in ev
    assert "highest density whose trials passed" in ev
    assert run_cli("bundle", "--run", str(out), "--strict", "--quiet") == 1
    # with the host daemon's files, strict passes
    rc = run_cli("bundle", "--run", str(out), "--strict", "--hostd-dir", str(hostd.telemetry_dir),
                 "--instance-type", "m8i.xlarge", "--vcpu-quota", "32", "--host-provisioning-s", "241.5", "--quiet")
    assert rc == 0
    assert (out / "hostd.log").exists() and (out / "hostd-spans.jsonl").exists()
    # section 11: every microVM's console log and guest log tail, copied from hostd
    for s in read_csv(out / "microvms.csv"):
        assert (out / "console-logs" / s["microvm_id"] / "console.log").exists()
        assert (out / "guest-logs" / f"{s['microvm_id']}.jsonl").exists()
    m = read_json(out / "manifest.json")
    for k in ("git_commit", "guest_image_base_digest", "rootfs_sha256", "kernel_sha256", "firecracker_version",
              "ami_id", "instance_type", "vcpu_quota", "run_start_ts", "run_end_ts", "timeout_parameters"):
        assert k in m
    assert m["instance_type"] == "m8i.xlarge" and m["vcpu_quota"] == 32 and m["host_provisioning_s"] == 241.5
    assert m["timeout_parameters"]["task_timeout_ms"] == 1000 and m["run_start_ts"] < m["run_end_ts"]
    assert m["trials"][0] == {"trial_id": "d2-t1", "sequence": 1, "backend": "docker", "density": 2, "trial_number": 1,
                              "fault": None, "status": "ok", "trial_kind": "ladder", "passed": True}
    ev = (out / "evidence.md").read_text()
    assert "no mandatory item is missing" in ev and "MANDATORY: evidence.md" not in ev
    # operator values survive a re-bundle that does not pass them again
    assert run_cli("bundle", "--run", str(out), "--strict", "--quiet") == 0
    assert read_json(out / "manifest.json")["instance_type"] == "m8i.xlarge"
    # the report works from the bundle alone
    assert run_cli("report", "--run", str(out), "--price-per-hour", "0.2117", "--quiet") == 0
    # AWS items become mandatory with --aws
    assert run_bundle(str(out), strict=True, aws=True)["inventory"]["mandatory_missing"] == [
        "cloud-init-output.log (AWS)", "hostcheck-output.txt (AWS)"]


def test_missing_screenshot_is_mandatory(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-shot"
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1") == 0
    shot = next((out / "screenshots").iterdir())
    shot.unlink()
    res = run_bundle(str(out), hostd_dir=str(hostd.telemetry_dir))
    assert any("screenshot" in m for m in res["inventory"]["mandatory_missing"])
    assert res["inventory"]["screenshots_missing"] == [f"screenshots/{shot.name}"]


def test_console_and_guest_logs_are_mandatory_per_microvm(hostd, fixture_site, tmp_path):
    out = tmp_path / "results" / "run-logs"  # no results/hostd sibling: bundle copies only with --hostd-dir
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "2") == 0
    assert run_cli("trial", *common(hostd, fixture_site, out), "--densities", "1", "--fault", "hang_task") == 1
    microvms = read_csv(out / "microvms.csv")
    tasks = read_csv(out / "tasks.csv")
    unreachable = next(t for t in tasks if t["failure_category"] == "guest_unreachable")["microvm_id"]
    ok_mid = next(t for t in tasks if t["ok"] == "true")["microvm_id"]
    # the hang_task microVM answered nothing, so it owes no guest log tail; it still owes a console log
    assert not (out / "guest-logs" / f"{unreachable}.jsonl").exists()
    res = run_bundle(str(out), hostd_dir=str(hostd.telemetry_dir), strict=True)
    inv = res["inventory"]
    assert res["ok"] and inv["console_logs_expected"] == 3 and inv["guest_logs_expected"] == 2
    assert inv["console_logs_missing"] == [] and inv["guest_logs_missing"] == []
    # remove one of each: strict fails and the ids are named
    (out / "console-logs" / unreachable / "console.log").unlink()
    (out / "guest-logs" / f"{ok_mid}.jsonl").unlink()
    res = run_bundle(str(out), strict=True)  # no --hostd-dir: nothing is copied back
    inv = res["inventory"]
    assert not res["ok"] and inv["console_logs_missing"] == [unreachable] and inv["guest_logs_missing"] == [ok_mid]
    missing = "\n".join(inv["mandatory_missing"])
    assert "console-logs/<microvm_id>/console.log" in missing and unreachable in missing
    assert "guest-logs/<microvm_id>.jsonl" in missing and ok_mid in missing
    ev = (out / "evidence.md").read_text()
    assert f"microVMs without a console log: {unreachable}" in ev and f"microVMs without a guest log tail: {ok_mid}" in ev
    assert run_cli("bundle", "--run", str(out), "--strict", "--quiet") == 1
    for s in microvms:
        assert (out / "console-logs" / s["microvm_id"]).exists()


def _rootfs_manifest_from_build_script(tmp_path: Path) -> Path:
    """Run the Python block of images/guest/build-rootfs.sh with the shell variables it interpolates
    filled in, so the test feeds build_manifest exactly what the rootfs builder writes."""
    script = REPO_ROOT / "images" / "guest" / "build-rootfs.sh"
    if not script.exists():
        pytest.skip("images/guest/build-rootfs.sh not present")
    text = script.read_text()
    m = re.search(r"python3 - \"\$manifest\" \"\$work/packages\.txt\" <<EOF\n(.*?)\nEOF\n", text, re.S)
    assert m, "build-rootfs.sh no longer writes the manifest from an inline Python block"
    values = {
        "ARCH": "amd64", "IMAGE": "fleetkit-guest:dev", "image_id": "sha256:" + "ab" * 32,
        "DEBIAN_IMAGE": "debian:bookworm-slim@sha256:" + "cd" * 32, "ALPINE_IMAGE": "alpine:3.22@sha256:" + "ef" * 32,
        "ext4_name": "guest-amd64.ext4", "sha256": "12" * 32, "size_bytes": "1234567890", "size_mib": "1500",
        "chromium_version": "154.0.7200.55-1~deb12u1",
    }
    block = re.sub(r"\$(\w+)", lambda mm: values[mm.group(1)], m.group(1))
    packages = tmp_path / "packages.txt"
    packages.write_text("Listing...\nchromium/stable,now 154.0.7200.55-1~deb12u1 amd64 [installed]\n"
                        "tini/stable,now 0.19.0-1 amd64 [installed]\n")
    manifest = tmp_path / "guest-amd64.manifest.json"
    subprocess.run([sys.executable, "-", str(manifest), str(packages)], input=block, text=True, check=True, timeout=30)
    return manifest


def test_manifest_reads_the_rootfs_builders_shape(tmp_path):
    manifest = _rootfs_manifest_from_build_script(tmp_path)
    guest = read_json(manifest)
    assert guest["rootfs"]["sha256"] == "12" * 32 and "@sha256:" in guest["base_image"]  # the builder's shape
    rd = RunDir(tmp_path / "results" / "run-m")
    m = build_manifest(rd, [], {"guest_manifest": str(manifest)})
    assert m["rootfs_sha256"] == "12" * 32
    assert m["guest_image_base_digest"] == "sha256:" + "cd" * 32
    assert m["guest_image_base"] == "debian:bookworm-slim@sha256:" + "cd" * 32
    assert m["guest_chromium_version"] == "154.0.7200.55-1~deb12u1"
    assert m["guest_image_id"] == "sha256:" + "ab" * 32 and m["guest_arch"] == "amd64"
    assert m["rootfs_size_bytes"] == 1234567890
    # a lock.env with a different pin does not override the manifest's own base digest
    lock = tmp_path / "lock.env"
    lock.write_text("DEBIAN_IMAGE=debian:bookworm-slim@sha256:" + "99" * 32 + "\nKERNEL_VERSION=6.18.48\n")
    m = build_manifest(rd, [], {"guest_manifest": str(manifest), "lock_env": str(lock)})
    assert m["guest_image_base_digest"] == "sha256:" + "cd" * 32 and m["kernel_version"] == "6.18.48"
    # without a guest manifest the lock.env pin is the fallback and rootfs fields are null
    m = build_manifest(rd, [], {"lock_env": str(lock)})
    assert m["guest_image_base_digest"] == "sha256:" + "99" * 32 and m["rootfs_sha256"] is None
    # flat keys keep working
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"rootfs_sha256": "aa" * 32, "base_digest": "sha256:" + "bb" * 32}))
    m = build_manifest(rd, [], {"guest_manifest": str(flat)})
    assert m["rootfs_sha256"] == "aa" * 32 and m["guest_image_base_digest"] == "sha256:" + "bb" * 32


def test_copy_otlp_filters_by_run_id(tmp_path):
    lgtm = tmp_path / "lgtm" / "otlp"
    lgtm.mkdir(parents=True)
    (lgtm / "traces.jsonl").write_text(_otlp_line("run-a") + "\n" + _otlp_line("run-b") + "\n" + _otlp_line("run-a") + "\n")
    (lgtm / "logs.jsonl").write_text(_otlp_line("run-b", "logs") + "\n" + _otlp_line("run-a", "logs") + "\n")
    rd = RunDir(tmp_path / "results" / "run-a")
    res = copy_otlp(tmp_path / "lgtm", rd)
    assert res["files"] == {"traces.jsonl": 2, "logs.jsonl": 1}
    assert len((rd.otlp_dir / "traces.jsonl").read_text().splitlines()) == 2
    # the smoke trace check reads the collector shape
    from driver.evidence import services_for_trace, logs_for_trace
    assert services_for_trace([rd.otlp_dir / "traces.jsonl"], "ab" * 16) == {"hostd": 2}
    assert logs_for_trace([rd.otlp_dir / "logs.jsonl"], "ab" * 16) == {"hostd": 1}
