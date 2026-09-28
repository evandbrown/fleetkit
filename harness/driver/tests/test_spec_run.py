"""``driver trial --spec``: a run from a spec, refusals, re-running a trial that failed outside the
experiment, and "not tested" when it can't be run cleanly."""
from __future__ import annotations

import copy
import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from driver.clean import outside_cause
from driver.evidence import iter_spans
from driver.spec import SpecError, load as load_spec, refusals

from tests.conftest import run_cli
from tests.stub_hostd import StubServer

SPEC = {
    "worker_host": {"instance_type": "m8i.4xlarge"},
    "hypervisor": {"name": "firecracker", "virtio_transport": "mmio", "virtio_rng": False},
    "microvm": {"vcpus": 2, "memory_mib": 2048},
    "densities": [1, 2, 3],
    "criteria": {"step_p50_target_ms": 1000, "step_p95_target_ms": 2000, "task_p95_target_ms": 5000,
                 "ready_timeout_s": 10, "step_timeout_ms": 5000, "task_timeout_ms": 10000},
    "procedure": {"trials_per_density": 1, "boundary_trials": 0, "settle_s": 0},
    "support_host": {"instance_type": "m8i.xlarge"},
}


def write_spec(path: Path, spec=None, wrapped=True, **changes) -> Path:
    s = copy.deepcopy(spec or SPEC)
    for k, v in changes.items():
        s[k] = v
    doc = {"campaign": "test-1", "run": "base-r1", "spec_name": "base", "replica": 1,
           "shutdown_after_minutes": 45, "spec": s} if wrapped else s
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def fc_hostd(tmp_path):
    with StubServer(telemetry_dir=str(tmp_path / "hostd"), proxy_margin_ms=300, startup_ms=40, step_ms=5,
                    backend="firecracker", max_slots=200) as s:
        yield s


class StubSupport:
    """The support host's health service: ``problems(call)`` decides each /window answer (call from 1)."""

    def __init__(self, problems):
        self.calls = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.calls += 1
                body = json.dumps({"problems": problems(outer.calls)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def spec_args(hostd, fixture_site, out: Path, spec: Path, *extra) -> list:
    return ["trial", "--spec", spec, "--host-url", hostd.url, "--fixture-check-url", fixture_site.url,
            "--out", out, "--quiet", "--no-lgtm", *extra]


def trial_ids(out: Path) -> list[str]:
    return sorted(p.name for p in (out / "trials").iterdir())


def ops(out: Path) -> list[dict]:
    p = out / "ops.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def rows(out: Path, table: str) -> list[dict]:
    with open(out / f"{table}.csv", newline="") as fh:
        return list(csv.DictReader(fh))


# ---- a run from a spec ----------------------------------------------------------------------

def test_a_run_from_a_spec_records_the_spec_and_follows_it(tmp_path, fc_hostd, fixture_site):
    out = tmp_path / "run"
    spec = write_spec(tmp_path / "spec.json")
    assert run_cli(*spec_args(fc_hostd, fixture_site, out, spec)) == 0
    run = json.loads((out / "run.json").read_text())
    assert run["spec"] == SPEC
    assert (run["campaign"], run["run"], run["spec_name"], run["replica"]) == ("test-1", "base-r1", "base", 1)
    h = run["harness"]
    assert h["warmup_trials"] == 1 and h["metrics_hz"] == 5.0 and h["reruns"] == 1
    assert (h["ready_timeout_s"], h["step_timeout_ms"], h["task_timeout_ms"]) == (10.0, 5000, 10000)
    assert h["idle_timeout_s"] > h["ready_timeout_s"] and h["max_lifetime_s"] > h["idle_timeout_s"]
    assert run["plan"]["stop_reason"] == "ladder_complete" and run["plan"]["complete"]
    assert run["criteria"]["step_p50_target_ms"] == 1000.0
    assert trial_ids(out) == ["d1-t1", "d2-t1", "d3-t1", "illustration", "warmup"]
    reqs = [s.request for s in fc_hostd.host.microvms.values()]
    assert reqs and all(r["hypervisor"] == SPEC["hypervisor"] and r["backend"] == "firecracker" for r in reqs)
    assert all(r["vcpus"] == 2 and r["mem_mib"] == 2048 and r["ready_timeout_s"] == 10.0 for r in reqs)
    t = json.loads((out / "trials" / "d2-t1" / "trial.json").read_text())
    assert t["hypervisor"] == SPEC["hypervisor"] and t["passed"] is True
    assert not (out / "ops.jsonl").exists()


def test_a_run_that_stops_at_its_first_miss_is_complete(tmp_path, fixture_site):
    # The usual AWS run: the ladder stops at the first density that misses, the boundary trials run, then the
    # illustration. That is the procedure finished normally, so plan.complete is true: the site reads it to tell a
    # finished run from one stopped early (a shutdown timer, an interruption).
    # Steps take 5 ms + 150 ms per live microVM beyond the first: density 1 passes a 100 ms median, 2 misses it.
    crit = {**SPEC["criteria"], "step_p50_target_ms": 100}
    proc = {**SPEC["procedure"], "boundary_trials": 1}
    spec = write_spec(tmp_path / "spec.json", criteria=crit, procedure=proc)
    out = tmp_path / "run"
    with StubServer(telemetry_dir=str(tmp_path / "hostd"), proxy_margin_ms=300, startup_ms=40, step_ms=5,
                    step_ms_per_n=150, backend="firecracker", max_slots=200) as hostd:
        assert run_cli(*spec_args(hostd, fixture_site, out, spec)) == 0
    assert trial_ids(out) == ["d1-t1", "d1-t2", "d2-t1", "d2-t2", "illustration", "warmup"]
    plan = json.loads((out / "run.json").read_text())["plan"]
    assert plan["complete"] is True and plan["stop_reason"] == "first_miss"
    assert plan["boundary"]["last_pass"] == 1 and plan["boundary"]["first_miss"] == 2
    assert plan["densities_not_run"] == [3]


def test_a_bare_spec_works_too(tmp_path, fc_hostd, fixture_site):
    out = tmp_path / "run"
    spec = write_spec(tmp_path / "spec.json", wrapped=False, densities=[1])
    assert run_cli(*spec_args(fc_hostd, fixture_site, out, spec)) == 0
    run = json.loads((out / "run.json").read_text())
    assert run["spec"]["densities"] == [1] and "campaign" not in run


def test_options_the_spec_owns_are_refused(tmp_path, fc_hostd, fixture_site, capsys):
    spec = write_spec(tmp_path / "spec.json")
    rc = run_cli(*spec_args(fc_hostd, fixture_site, tmp_path / "run", spec, "--densities", "1,2",
                            "--settle-s=3"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "--densities: comes from the spec" in err and "--settle-s: comes from the spec" in err
    assert not (tmp_path / "run").exists()


def test_an_invalid_spec_is_refused_with_its_field(tmp_path):
    with pytest.raises(SpecError) as e:
        load_spec(write_spec(tmp_path / "spec.json", densities=[1, 4, 2]))
    assert any(p.startswith("densities:") for p in e.value.problems)
    bad = copy.deepcopy(SPEC)
    bad["hypervisor"] = {"name": "cloud-hypervisor", "virtio_transport": "mmio", "virtio_rng": True}
    with pytest.raises(SpecError) as e:
        load_spec(write_spec(tmp_path / "spec.json", spec=bad))
    assert any("hypervisor.virtio_transport" in p for p in e.value.problems)
    (tmp_path / "dup.json").write_text('{"spec": {}, "spec": {}}')
    with pytest.raises(SpecError):
        load_spec(tmp_path / "dup.json")


def test_what_the_host_cannot_carry_out_is_refused(tmp_path):
    rs = load_spec(write_spec(tmp_path / "spec.json"))
    assert refusals(rs, {"backend": "firecracker"}, {}) == []
    assert refusals(rs, {"backend": "docker"}, {}) == \
        ["hypervisor.name: this worker host's daemon runs docker, not firecracker"]
    pci = copy.deepcopy(SPEC)
    pci["hypervisor"].update(virtio_transport="pci", virtio_rng=True)
    rs = load_spec(write_spec(tmp_path / "spec.json", spec=pci))
    got = refusals(rs, {"backend": "firecracker"}, None)  # an older daemon offers the old setup only
    assert [p.split(":")[0] for p in got] == ["hypervisor.virtio_transport", "hypervisor.virtio_rng"]
    assert refusals(rs, {"backend": "firecracker"}, {"hypervisor_options": {
        "virtio_transport": ["mmio", "pci"], "virtio_rng": [False, True]}}) == []
    big = load_spec(write_spec(tmp_path / "spec.json", densities=[1, 64, 65]))
    assert refusals(big, {"backend": "firecracker"}, {"max_slots": 64}) == \
        ["densities: 65 microVMs at once is more than this host daemon's 64 slots"]
    assert refusals(big, {"backend": "firecracker"}, {"ec2": {"instance_type": "m8i.2xlarge"}}) == \
        ["worker_host.instance_type: this worker host is m8i.2xlarge, not m8i.4xlarge"]


def test_a_refused_spec_starts_no_trial(tmp_path, fixture_site, capsys):
    with StubServer(backend="docker") as hostd:
        rc = run_cli(*spec_args(hostd, fixture_site, tmp_path / "run", write_spec(tmp_path / "spec.json")))
        assert rc == 2 and not hostd.host.microvms
    assert "daemon runs docker, not firecracker" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


def test_backend_docker_checks_a_spec_locally_and_records_docker(tmp_path, fixture_site, capsys):
    rs = load_spec(write_spec(tmp_path / "spec.json", densities=[1, 2]))
    assert refusals(rs, {"backend": "docker"}, {}, local_docker=True) == []
    assert refusals(rs, {"backend": "firecracker"}, {}, local_docker=True) == \
        ["--backend docker: this host's daemon runs firecracker, not docker"]
    out = tmp_path / "run"
    with StubServer(telemetry_dir=str(tmp_path / "hostd"), proxy_margin_ms=300, startup_ms=40, step_ms=5,
                    backend="docker") as hostd:
        spec = write_spec(tmp_path / "spec.json", densities=[1, 2])
        assert run_cli(*spec_args(hostd, fixture_site, out, spec, "--backend", "docker")) == 0
        made = list(hostd.host.microvms.values())
        assert made and all("hypervisor" not in m.request for m in made)
    run = json.loads((out / "run.json").read_text())
    assert run["spec"]["densities"] == [1, 2] and run["spec"]["hypervisor"]["name"] == "firecracker"
    assert run["backend"] == "docker" and run["harness"]["backend"] == "docker"
    # The spec still owns every other option.
    assert run_cli(*spec_args(hostd, fixture_site, tmp_path / "other", spec, "--backend", "firecracker")) == 2
    assert "--backend: comes from the spec" in capsys.readouterr().err


def test_a_run_directory_holds_one_spec_and_resumes_it(tmp_path, fc_hostd, fixture_site, capsys):
    out = tmp_path / "run"
    spec = write_spec(tmp_path / "spec.json")
    assert run_cli(*spec_args(fc_hostd, fixture_site, out, spec)) == 0
    other = write_spec(tmp_path / "other.json", densities=[1, 2])
    assert run_cli(*spec_args(fc_hostd, fixture_site, out, other)) == 2
    assert "different spec" in capsys.readouterr().err
    # the same spec resumes: the illustration was cut off, so it alone is set aside and run again
    ill = out / "trials" / "illustration" / "trial.json"
    doc = json.loads(ill.read_text())
    doc["complete"] = False
    ill.write_text(json.dumps(doc))
    before = len(fc_hostd.host.microvms)
    assert run_cli(*spec_args(fc_hostd, fixture_site, out, spec)) == 0
    assert len(fc_hostd.host.microvms) == before + 1
    assert trial_ids(out) == ["d1-t1", "d2-t1", "d3-t1", "illustration", "warmup"]
    events = [e["event"] for e in ops(out)]
    assert events == ["trial_set_aside", "resumed"]
    assert json.loads(ill.read_text())["complete"] is True


def test_a_trial_without_a_spec_is_never_mixed_in(tmp_path, fc_hostd, fixture_site, capsys):
    out = tmp_path / "run"
    assert run_cli("trial", "--backend", "firecracker", "--host-url", fc_hostd.url, "--fixture-check-url",
                   fixture_site.url, "--out", out, "--quiet", "--no-lgtm", "--densities", "1") == 0
    assert run_cli(*spec_args(fc_hostd, fixture_site, out, write_spec(tmp_path / "spec.json"))) == 2
    assert "without this spec" in capsys.readouterr().err


# ---- the wait after ready (a warm start) ------------------------------------------------------

def _trial(out: Path, tid: str) -> dict:
    return json.loads((out / "trials" / tid / "trial.json").read_text())


def test_timeouts_allow_for_the_wait_after_ready_and_no_wait_leaves_them_as_they_were(tmp_path):
    cold = load_spec(write_spec(tmp_path / "cold.json"))
    assert "release_after_ready_s" not in cold.spec["procedure"] and cold.release_after_ready_s == 0.0
    # exactly the formula from before the wait existed: idle after ready_timeout_s + 120, lifetime + task + 300
    assert cold.timeouts() == {"ready_timeout_s": 10.0, "step_timeout_ms": 5000, "task_timeout_ms": 10000,
                               "idle_timeout_s": 130.0, "max_lifetime_s": 320.0, "launch_interval_ms": 0}
    zero = load_spec(write_spec(tmp_path / "zero.json", procedure={**SPEC["procedure"], "release_after_ready_s": 0}))
    assert zero.timeouts() == cold.timeouts()
    warm = load_spec(write_spec(tmp_path / "warm.json", procedure={**SPEC["procedure"], "release_after_ready_s": 60}))
    t = warm.timeouts()
    assert t["idle_timeout_s"] == 190.0 and t["max_lifetime_s"] == 380.0
    with pytest.raises(SpecError) as e:
        load_spec(write_spec(tmp_path / "long.json", procedure={**SPEC["procedure"], "release_after_ready_s": 61}))
    assert e.value.problems == ["procedure.release_after_ready_s: must be at most 60"]


def test_the_reapers_outlast_the_longest_wait_on_the_largest_metal_trial(tmp_path):
    # A metal worker host at its full 192 microVMs, the metal ready timeout (900 s) and the longest wait (60 s):
    # a microVM ready at once sits idle until the driver sees the last one ready or failed (at most
    # ready_timeout_s + READY_GRACE_S), then through the wait and the fixture recheck (5 s timeout), before its
    # task starts; and it lives that long plus the task (at most the client timeout) and its cleanup.
    from driver.config import DESTROY_GRACE_S, READY_GRACE_S, Timeouts
    from driver.products import fixture_check
    import inspect
    recheck_s = inspect.signature(fixture_check).parameters["timeout_s"].default
    for task_timeout_ms in (45000, 600000):
        rs = load_spec(write_spec(
            tmp_path / "metal.json", worker_host={"instance_type": "m8i.metal-48xl"}, densities=[1, 96, 192],
            criteria={**SPEC["criteria"], "ready_timeout_s": 900, "task_timeout_ms": task_timeout_ms},
            procedure={**SPEC["procedure"], "release_after_ready_s": 60}))
        t = rs.timeouts()
        assert (t["idle_timeout_s"], t["max_lifetime_s"]) == (1080.0, 1260.0 + task_timeout_ms / 1000)
        longest_idle = 900 + READY_GRACE_S + 60 + recheck_s
        client_s = Timeouts(task_timeout_ms=task_timeout_ms).client_timeout_ms / 1000
        longest_life = longest_idle + client_s + DESTROY_GRACE_S
        assert t["idle_timeout_s"] - longest_idle >= 60 and t["max_lifetime_s"] - longest_life >= 120


def test_a_warm_start_waits_after_ready_records_the_wait_and_keeps_it_out_of_task_times(tmp_path, fc_hostd,
                                                                                        fixture_site):
    wait = 0.5
    out = tmp_path / "run"
    spec = write_spec(tmp_path / "spec.json", densities=[1, 2],
                      procedure={**SPEC["procedure"], "release_after_ready_s": wait})
    assert run_cli(*spec_args(fc_hostd, fixture_site, out, spec)) == 0
    run = json.loads((out / "run.json").read_text())
    assert run["spec"]["procedure"]["release_after_ready_s"] == wait
    assert run["harness"]["release_after_ready_s"] == wait  # a spec field now; harness records the wait carried out
    assert (run["harness"]["idle_timeout_s"], run["harness"]["max_lifetime_s"]) == (130.5, 320.5)
    reqs = [m.request for m in fc_hostd.host.microvms.values()]
    assert reqs and all((r["idle_timeout_s"], r["max_lifetime_s"]) == (130.5, 320.5) for r in reqs)

    tasks, vms = rows(out, "tasks"), rows(out, "microvms")
    # every trial waits, the labelled ones too
    assert trial_ids(out) == ["d1-t1", "d2-t1", "illustration", "warmup"]
    for tid in trial_ids(out):
        t = _trial(out, tid)
        assert t["release_after_ready_s"] == wait and t["passed"] in (True, None)
        ts = t["timestamps"]
        assert ts["create_start"] < ts["all_ready"] <= ts["release_wait_start"] < ts["release_wait_end"] \
            <= ts["barrier_release"] < ts["last_task_return"] < ts["verify_clean_pass"]
        assert ts["release_wait_end"] - ts["release_wait_start"] >= wait
        assert ts["release_wait_end"] - ts["release_wait_start"] < wait + 0.25
        # every microVM was ready before the wait began, and no task went out before the release
        ready = [float(v["ready_ts"]) for v in vms if v["trial_id"] == tid]
        assert len(ready) == t["density"] and max(ready) <= ts["release_wait_start"]
        assert ts["barrier_release"] - max(ready) >= wait
        sent = [float(r["dispatch_ts"]) for r in tasks if r["trial_id"] == tid]
        assert len(sent) == t["density"] and min(sent) >= ts["barrier_release"]
        # a task's time starts at the release: the stub's tasks take about 25 ms, never the wait
        assert all(float(r["task_ms"]) < 250 for r in tasks if r["trial_id"] == tid)
    spans = [s for s in iter_spans(out / "spans.jsonl") if s["name"] == "release_wait"]
    assert len(spans) == 4 and all(float(s["attributes"]["fleetkit.release_after_ready_s"]) == wait for s in spans)

    # the report: the execution window runs from the release, the observed one takes the wait in
    assert run_cli("report", "--run", out, "--price-per-hour", "1", "--quiet") == 0
    rep = json.loads((out / "report.json").read_text())
    assert rep["inputs"]["release_after_ready_s"] == wait
    for tr in rep["trials"]:
        t, w = _trial(out, tr["trial_id"]), tr["windows_s"]
        ts = t["timestamps"]
        assert w["execution_s"] == pytest.approx(ts["last_task_return"] - ts["barrier_release"])
        assert w["observed_s"] == pytest.approx(ts["verify_clean_pass"] - ts["create_start"])
        assert w["release_wait_s"] == pytest.approx(ts["release_wait_end"] - ts["release_wait_start"])
        assert w["observed_s"] > w["execution_s"] + w["release_wait_s"]
    md = (out / "report.md").read_text()
    assert "Warm start: every trial released its tasks 0.5 s after its last microVM was ready" in md
    assert "| wait after ready s | exec window s |" in md
    assert "the barrier opens after the wait after ready, so the wait is outside this window" in md


def test_no_wait_after_ready_is_the_cold_start_it_always_was(tmp_path, fc_hostd, fixture_site):
    out = tmp_path / "run"
    assert run_cli(*spec_args(fc_hostd, fixture_site, out, write_spec(tmp_path / "spec.json", densities=[1]))) == 0
    for tid in trial_ids(out):
        t = _trial(out, tid)
        assert t["release_after_ready_s"] == 0.0
        assert t["timestamps"]["release_wait_start"] is None and t["timestamps"]["release_wait_end"] is None
    assert not [s for s in iter_spans(out / "spans.jsonl") if s["name"] == "release_wait"]
    assert run_cli("report", "--run", out, "--quiet") == 0
    rep = json.loads((out / "report.json").read_text())
    assert rep["inputs"]["release_after_ready_s"] == 0.0
    assert all(tr["windows_s"]["release_wait_s"] is None for tr in rep["trials"])
    md = (out / "report.md").read_text()
    assert "Warm start" not in md and "wait after ready" not in md
    # run.json still says what the wait was, as it did when HARNESS held it: 0, with the spec leaving it out
    run = json.loads((out / "run.json").read_text())
    assert "release_after_ready_s" not in run["spec"]["procedure"] and run["harness"]["release_after_ready_s"] == 0.0


def test_the_wait_after_ready_comes_from_the_spec_or_the_option(tmp_path, fc_hostd, fixture_site, capsys):
    spec = write_spec(tmp_path / "spec.json")
    assert run_cli(*spec_args(fc_hostd, fixture_site, tmp_path / "a", spec, "--release-after-ready-s", "1")) == 2
    assert "--release-after-ready-s: comes from the spec" in capsys.readouterr().err
    base = ["trial", "--backend", "firecracker", "--host-url", fc_hostd.url, "--fixture-check-url", fixture_site.url,
            "--quiet", "--no-lgtm", "--densities", "1"]
    assert run_cli(*base, "--out", tmp_path / "b", "--release-after-ready-s", "-1") == 2
    assert run_cli(*base, "--out", tmp_path / "c", "--release-after-ready-s", "120") == 2
    assert "--ready-timeout-s plus the wait must be below --idle-timeout-s" in capsys.readouterr().err
    # below --idle-timeout-s (120) on its own, but a microVM ready at once idles up to --ready-timeout-s (60) first
    assert run_cli(*base, "--out", tmp_path / "e", "--release-after-ready-s", "60") == 2
    assert run_cli(*base, "--out", tmp_path / "f", "--release-after-ready-s", "100") == 2
    # the lifetime reaper too: 60 + 30 + 45 s of task timeout is past a 120 s lifetime
    assert run_cli(*base, "--out", tmp_path / "g", "--idle-timeout-s", "600", "--max-lifetime-s", "120",
                   "--release-after-ready-s", "30") == 2
    assert "--ready-timeout-s plus the wait must be below --idle-timeout-s" in capsys.readouterr().err
    assert not any((tmp_path / d).exists() for d in "cefg")
    assert run_cli(*base, "--out", tmp_path / "d", "--release-after-ready-s", "0.3") == 0
    t = _trial(tmp_path / "d", "d1-t1")
    assert t["release_after_ready_s"] == 0.3
    assert t["timestamps"]["release_wait_end"] - t["timestamps"]["release_wait_start"] >= 0.3
    assert json.loads((tmp_path / "d" / "run.json").read_text())["spec"]["release_after_ready_s"] == 0.3


# ---- failures outside the experiment --------------------------------------------------------

def test_a_trial_the_support_host_spoiled_runs_again(tmp_path, fc_hostd, fixture_site):
    # call 1 is the warm-up, call 2 d1-t1's first attempt, call 3 its second
    sup = StubSupport(lambda call: ["fixture container at 97% of its CPU"] if call == 2 else [])
    try:
        out = tmp_path / "run"
        rc = run_cli(*spec_args(fc_hostd, fixture_site, out, write_spec(tmp_path / "spec.json"),
                                "--support-health-url", sup.url))
    finally:
        sup.close()
    assert rc == 0
    assert trial_ids(out) == ["d1-t1", "d2-t1", "d3-t1", "illustration", "warmup"]
    e = ops(out)
    assert [x["event"] for x in e] == ["trial_set_aside"]
    assert e[0]["trial_id"] == "d1-t1" and e[0]["next"] == "run again" and e[0]["attempt"] == 1
    assert e[0]["cause"] == "support host: fixture container at 97% of its CPU"
    aside = out / e[0]["set_aside"]
    assert (aside / "trial.json").exists() and (aside / "tasks.csv").exists()
    # the results hold the clean trial only: one task at d1-t1, not two
    assert [r["trial_id"] for r in rows(out, "tasks")].count("d1-t1") == 1
    assert [r["trial_id"] for r in rows(aside, "tasks")] == ["d1-t1"]
    assert [p.name for p in (out / "screenshots").glob("d1-t1-slot*")] == ["d1-t1-slot000.jpg"]
    assert [p.name for p in (aside / "screenshots").iterdir()] == ["d1-t1-slot000.jpg"]
    run = json.loads((out / "run.json").read_text())
    assert run["plan"]["complete"] and run["plan"]["stop_reason"] == "ladder_complete"
    assert "fixture container" not in (out / "trials" / "d1-t1" / "trial.json").read_text()


def test_a_density_that_cannot_be_run_cleanly_is_not_tested(tmp_path, fc_hostd, fixture_site):
    # from call 3 on (d2-t1's first attempt; call 1 is the warm-up, 2 is d1-t1) the support host is overloaded
    sup = StubSupport(lambda call: ["support host CPU at 99%"] if call >= 3 else [])
    try:
        out = tmp_path / "run"
        rc = run_cli(*spec_args(fc_hostd, fixture_site, out, write_spec(tmp_path / "spec.json"),
                                "--support-health-url", sup.url))
    finally:
        sup.close()
    assert rc == 1
    plan = json.loads((out / "run.json").read_text())["plan"]
    assert plan["stop_reason"] == "not_clean" and not plan["complete"]
    assert [d["density"] for d in plan["densities_run"]] == [1] and plan["densities_not_run"] == [2, 3]
    assert plan["boundary"]["last_pass"] == 1 and plan["boundary"]["first_miss"] is None
    # no failed trial at density 2 in the results; the illustration couldn't be run cleanly either
    assert trial_ids(out) == ["d1-t1", "warmup"]
    e = ops(out)
    assert [(x["event"], x.get("trial_id"), x.get("next")) for x in e] == [
        ("trial_set_aside", "d2-t1", "run again"), ("trial_set_aside", "d2-t1", "give up"),
        ("density_not_tested", None, None),
        ("trial_set_aside", "illustration", "run again"), ("trial_set_aside", "illustration", "give up")]
    assert e[2]["density"] == 2 and e[2]["cause"] == "support host: support host CPU at 99%"
    assert sorted(p.name for p in (out / "ops" / "set-aside").iterdir()) == \
        ["d2-t1-1", "d2-t1-2", "illustration-1", "illustration-2"]
    assert {r["trial_id"] for r in rows(out, "tasks")} == {"warmup", "d1-t1"}


def test_an_unreachable_support_health_service_is_noted_once(tmp_path, fc_hostd, fixture_site):
    out = tmp_path / "run"
    rc = run_cli(*spec_args(fc_hostd, fixture_site, out, write_spec(tmp_path / "spec.json", densities=[1]),
                            "--support-health-url", "http://127.0.0.1:9"))
    assert rc == 0
    assert [x["event"] for x in ops(out)] == ["support_health_unavailable"]


# ---- what counts as outside the experiment -----------------------------------------------------

def _doc(**kw):
    d = {"error": None, "verify_clean": {"clean": True, "leftovers": []}, "microvms": []}
    d.update(kw)
    return d


def test_outside_causes():
    assert outside_cause(_doc()) is None
    assert outside_cause(_doc(), harness_error="KeyError: 'x'") == "harness: KeyError: 'x'"
    assert outside_cause(_doc(error="fixture check failed: HTTP 502")).startswith("fixture: ")
    assert outside_cause(_doc(error="host daemon: HTTP 500")) == "host daemon: HTTP 500"
    assert outside_cause(_doc(verify_clean={"clean": False, "leftovers": [], "error": "refused"})) \
        .startswith("host daemon: verify-clean")
    refused = {"microvm_id": "a", "task": {"error": "driver client: [Errno 61] Connection refused"}}
    assert outside_cause(_doc(microvms=[refused])).startswith("host daemon: no answer to a task request")
    assert outside_cause(_doc(), health_error="timed out").startswith("host daemon: no answer after")
    assert outside_cause(_doc(), health_after={"uptime_s": 3.0}, trial_s=40.0).startswith("host daemon: restarted")
    assert outside_cause(_doc(), health_after={"uptime_s": 300.0}, trial_s=40.0) is None
    assert outside_cause(_doc(), support={"problems": ["a", "b"]}) == "support host: a; b"
    assert outside_cause(_doc(), support={"problems": []}) is None
    changed = {"microvm_id": "s007", "task": {"error": "http://10.42.0.15:8081/: net::ERR_NETWORK_CHANGED",
                                                "failure_category": "navigation_error"}}
    assert outside_cause(_doc(microvms=[changed])) == "guest network: changed under a navigation in microVM s007"


def test_what_the_experiment_itself_causes_is_a_result():
    # leftovers on the worker host, a slow task, a guest that stopped answering, a client timeout
    assert outside_cause(_doc(error="verify-clean failed: ['tap7']",
                              verify_clean={"clean": False, "leftovers": ["tap7"]})) is None
    for err in ("", "step search timed out", "guest unreachable: 502", "driver client: timed out",
                "http://10.42.0.15:8081/: net::ERR_TIMED_OUT"):
        m = {"microvm_id": "a", "task": {"error": err, "failure_category": "step_timeout"}}
        assert outside_cause(_doc(microvms=[m])) is None
