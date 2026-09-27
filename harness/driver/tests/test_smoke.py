from __future__ import annotations

from driver.outputs import read_json
from tests.conftest import common, run_cli
from tests.stub_hostd import StubServer

SMOKE = ["--never-ready-timeout-s", "1", "--idle-case-s", "1", "--lifetime-case-s", "2", "--case-grace-s", "5",
         "--crash-bound-s", "3"]


def _assertions(out):
    st = read_json(out / "smoke-state.json")
    md = (out / "smoke-report.md").read_text()
    rows = [l for l in md.splitlines() if l.startswith("| ") and ("PASS" in l or "FAIL" in l)]
    return st, md, rows


def test_smoke_green_then_counter_increments(hostd, fixture_site, tmp_path):
    out = tmp_path / "run-s"
    args = ["smoke", *common(hostd, fixture_site, out), "--hostd-dir", str(hostd.telemetry_dir), *SMOKE]
    assert run_cli(*args) == 0
    st, md, rows = _assertions(out)
    assert st["consecutive_green"] == 1 and len(rows) == 11 and all("PASS" in r for r in rows)
    assert [r.split("|")[1].strip() for r in rows] == ["1a", "1b", "2a", "2b", "2c", "2d", "2e", "3a", "3b", "4", "5"]
    assert "GREEN" in md and "Consecutive green runs: **1**" in md
    # the fault table held
    for want in ("startup_error", "startup_timeout", "guest_unreachable", "step_timeout", "idle_expired", "lifetime_expired"):
        assert want in md
    # session cases wrote sessions.csv rows and case.json
    cases = sorted(p.name for p in (out / "trials").iterdir() if (p / "case.json").exists())
    assert cases == ["t008-case-idle", "t009-case-lifetime"]

    assert run_cli(*args) == 0
    st, md, rows = _assertions(out)
    assert st["consecutive_green"] == 2 and len(st["runs"]) == 2


def test_smoke_red_resets_counter(fixture_site, tmp_path):
    out = tmp_path / "run-red"
    with StubServer(telemetry_dir=str(tmp_path / "hostd"), proxy_margin_ms=300) as hostd:
        assert run_cli("smoke", *common(hostd, fixture_site, out), "--hostd-dir", str(tmp_path / "hostd"), *SMOKE) == 0
    assert read_json(out / "smoke-state.json")["consecutive_green"] == 1
    with StubServer(telemetry_dir=str(tmp_path / "hostd"), proxy_margin_ms=300, leak_after_trial=True) as hostd:
        rc = run_cli("smoke", *common(hostd, fixture_site, out), "--hostd-dir", str(tmp_path / "hostd"), *SMOKE)
    assert rc == 1
    st, md, rows = _assertions(out)
    assert st["consecutive_green"] == 0 and "RED" in md
    failed = [r.split("|")[1].strip() for r in rows if "FAIL" in r]
    assert "4" in failed  # verify-clean after every case
    assert st["runs"][-1]["failed"]


def test_smoke_without_hostd_spans_fails_assertion_5(fixture_site, tmp_path):
    out = tmp_path / "run-5"
    with StubServer(proxy_margin_ms=300) as hostd:  # no telemetry dir: no hostd/guest-daemon spans anywhere
        rc = run_cli("smoke", *common(hostd, fixture_site, out), *SMOKE)
    assert rc == 1
    st, md, rows = _assertions(out)
    failed = [r.split("|")[1].strip() for r in rows if "FAIL" in r]
    assert failed == ["5"] and "missing spans from ['hostd', 'guest-daemon']" in md
