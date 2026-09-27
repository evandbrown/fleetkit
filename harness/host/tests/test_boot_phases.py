"""Boot phases and guest_info from the ready /health answer (host clock arithmetic)."""
from __future__ import annotations

import pytest

from hostd.guest import GUEST_INFO_KEYS, GuestResponse, boot_phases, guest_info

SEND_NS = 1_700_000_000_000_000_000          # 1.7e9 s on the host clock
RTT_NS = 4_000_000                            # 4 ms -> t_host = send + 2 ms

HEALTH = {"ready": True, "chromium_version": "Chrome/154.0.8037.57", "uptime_s": 2.5,
          "guestd_version": "0.2.0", "guestd_uptime_s": 2.5, "kernel_uptime_s": 3.0,
          "chromium_launch_s": 0.1, "chromium_ready_s": 1.9,
          "chromium_flags": ["--headless=new", "--remote-debugging-port=9222"],
          "kernel_cmdline": "console=ttyS0 reboot=k panic=1 pci=off", "vcpus": 2, "mem_total": 2_046_820_352}


def test_boot_phase_arithmetic():
    t_host = 1_700_000_000.002
    p = boot_phases(GuestResponse(200, HEALTH, rtt_ns=RTT_NS, send_ns=SEND_NS))
    assert p["kernel_start_ts"] == pytest.approx(t_host - 3.0, abs=1e-6)
    assert p["guestd_start_ts"] == pytest.approx(t_host - 2.5, abs=1e-6)
    assert p["chromium_launch_ts"] == pytest.approx(t_host - 2.5 + 0.1, abs=1e-6)
    assert p["chromium_ready_ts"] == pytest.approx(t_host - 2.5 + 1.9, abs=1e-6)
    assert p["kernel_start_ts"] < p["guestd_start_ts"] < p["chromium_launch_ts"] < p["chromium_ready_ts"] < t_host


def test_missing_inputs_give_none():
    old_guest = {"ready": True, "chromium_version": "154.0", "uptime_s": 1.25}
    p = boot_phases(GuestResponse(200, old_guest, rtt_ns=RTT_NS, send_ns=SEND_NS))
    # uptime_s is guestd's uptime under its original name, so guestd_start_ts survives
    assert p["guestd_start_ts"] == pytest.approx(1_700_000_000.002 - 1.25, abs=1e-6)
    assert p["kernel_start_ts"] is None and p["chromium_launch_ts"] is None and p["chromium_ready_ts"] is None
    # Chromium not launched yet (null), or a bogus type: that phase is None, the others stand
    body = dict(HEALTH, chromium_launch_s=None, chromium_ready_s=True, kernel_uptime_s="3")
    p = boot_phases(GuestResponse(200, body, rtt_ns=RTT_NS, send_ns=SEND_NS))
    assert p["guestd_start_ts"] is not None
    assert p["kernel_start_ts"] is None and p["chromium_launch_ts"] is None and p["chromium_ready_ts"] is None
    # no guestd uptime at all: the chromium phases have no anchor
    body = {k: v for k, v in HEALTH.items() if k not in ("uptime_s", "guestd_uptime_s")}
    p = boot_phases(GuestResponse(200, body, rtt_ns=RTT_NS, send_ns=SEND_NS))
    assert p["kernel_start_ts"] is not None
    assert p["guestd_start_ts"] is None and p["chromium_launch_ts"] is None and p["chromium_ready_ts"] is None
    assert boot_phases(GuestResponse(200, "not a dict", rtt_ns=0, send_ns=SEND_NS)) == {
        "kernel_start_ts": None, "guestd_start_ts": None, "chromium_launch_ts": None, "chromium_ready_ts": None}


def test_guest_info_keys():
    info = guest_info(HEALTH)
    assert set(info) == set(GUEST_INFO_KEYS) == {"guestd_version", "chromium_version", "chromium_flags",
                                                 "kernel_cmdline", "vcpus", "mem_total"}
    assert info["chromium_flags"] == HEALTH["chromium_flags"] and info["vcpus"] == 2
    assert guest_info({"chromium_version": "154.0"}) == {**{k: None for k in GUEST_INFO_KEYS},
                                                        "chromium_version": "154.0"}
    assert guest_info(None) == {k: None for k in GUEST_INFO_KEYS}


def test_microvm_record_carries_boot_phases(manager, guest, ctx):
    guest.ready.add("guest-0")
    guest.health_extra = {k: v for k, v in HEALTH.items() if k != "ready"}
    guest.health_send_ns, guest.health_rtt_ns = SEND_NS, RTT_NS
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    rec = manager.get(sid).record()
    assert rec["state"] == "ready"
    assert rec["kernel_start_ts"] == pytest.approx(1_700_000_000.002 - 3.0, abs=1e-6)
    assert rec["guestd_start_ts"] == pytest.approx(1_700_000_000.002 - 2.5, abs=1e-6)
    assert rec["chromium_launch_ts"] == pytest.approx(1_700_000_000.002 - 2.4, abs=1e-6)
    assert rec["chromium_ready_ts"] == pytest.approx(1_700_000_000.002 - 0.6, abs=1e-6)
    assert rec["guest_info"] == {k: HEALTH[k] for k in GUEST_INFO_KEYS}
    rec["guest_info"]["vcpus"] = 99                       # the record is a copy
    assert manager.get(sid).guest_info["vcpus"] == 2


def test_record_fields_are_null_before_ready_and_in_dry_run(backend, guest, clock, tel, ctx):
    from hostd.manager import Manager
    m = Manager(backend, tel, guest=guest, clock=clock.time, sleep=clock.sleep,
                       spawn=lambda fn, name: fn(), dry_run=True)
    rec = m.get(m.create_microvms({"count": 1}, ctx)[0]["id"]).record()
    assert rec["state"] == "ready"
    for k in ("kernel_start_ts", "guestd_start_ts", "chromium_launch_ts", "chromium_ready_ts", "guest_info"):
        assert k in rec and rec[k] is None, k
