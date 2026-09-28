"""Extra Chromium flags: the flag list, the encoding the host sends, the read-back from /proc."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path

import pytest

from guestd import __main__ as guestd_main
from guestd import chromium as chromium_mod
from guestd import minihttp, server
from guestd.chromium import (EXTRA_FLAGS_ENV, chromium_flags, decode_extra_flags, encode_extra_flags,
                             read_process_flags)
from guestd.log import LogRing

FLAGS_JSON = Path(__file__).resolve().parents[3] / "experiments" / "schema" / "chromium-flags.json"

# The flag list every run had before extra flags existed; with none, it must stay exactly this.
BASE = [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--remote-debugging-port=9222",
    "--user-data-dir=/tmp/profile", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--disable-default-apps", "--window-size=1280,800", "about:blank",
]
# hostd's tests hold the same literal (hostd.model.encode_chromium_flags).
VECTOR = (["--renderer-process-limit=1", "--js-flags=--max-old-space-size=512 --jitless"],
          "WyItLXJlbmRlcmVyLXByb2Nlc3MtbGltaXQ9MSIsIi0tanMtZmxhZ3M9LS1tYXgtb2xkLXNwYWNlLXNpemU9NTEyIC0taml0bGVzcyJd")


def test_no_extra_flags_is_the_old_flag_list_exactly():
    assert chromium_flags() == BASE
    assert chromium_flags(extra=()) == BASE


def test_the_base_flags_are_the_schemas():
    # expand.py, the site's builder and the driver's read-back check use chromium-flags.json's base list.
    doc = json.loads(FLAGS_JSON.read_text())
    assert chromium_flags() == doc["base"] + [doc["start_page"]]


def test_extra_flags_go_after_the_base_flags_before_the_start_page():
    flags = chromium_flags(9333, "/p", ["--renderer-process-limit=1", "--no-zygote"])
    assert flags[:13] == [f.replace("9222", "9333").replace("/tmp/profile", "/p") for f in BASE[:13]]
    assert flags[13:] == ["--renderer-process-limit=1", "--no-zygote", "about:blank"]


def test_encoding_round_trip_and_the_shared_vector():
    flags, word = VECTOR
    assert encode_extra_flags(flags) == word
    assert decode_extra_flags(word) == flags
    assert "=" not in word and " " not in word
    assert decode_extra_flags(None) == [] and decode_extra_flags("") == []
    assert decode_extra_flags(encode_extra_flags([])) == []


@pytest.mark.parametrize("bad", [
    "not base64 at all!",
    encode_extra_flags.__call__(["ok"]).upper(),
    chromium_mod.base64.urlsafe_b64encode(b'{"a": 1}').decode(),
    chromium_mod.base64.urlsafe_b64encode(b'["--a", 3]').decode(),
    chromium_mod.base64.urlsafe_b64encode(b'["no-dashes"]').decode(),
    chromium_mod.base64.urlsafe_b64encode(json.dumps(["--a\n--b"]).encode()).decode(),
    chromium_mod.base64.urlsafe_b64encode(json.dumps(["--x"] * 17).encode()).decode(),
    chromium_mod.base64.urlsafe_b64encode(json.dumps(["--" + "x" * 511]).encode()).decode(),
])
def test_a_bad_encoding_is_refused(bad):
    with pytest.raises(ValueError):
        decode_extra_flags(bad)


def test_read_process_flags(tmp_path):
    (tmp_path / "10").mkdir()
    (tmp_path / "10" / "cmdline").write_bytes(b"/usr/lib/chromium/chromium\0--headless=new\0--js-flags=--a --b\0about:blank\0")
    (tmp_path / "11").mkdir()
    (tmp_path / "11" / "cmdline").write_bytes(b"/usr/lib/chromium/chromium --type=renderer --lang=en\0\0\0")
    (tmp_path / "12").mkdir()
    (tmp_path / "12" / "cmdline").write_bytes(b"")
    assert read_process_flags(10, str(tmp_path)) == ["--headless=new", "--js-flags=--a --b", "about:blank"]
    assert read_process_flags(11, str(tmp_path)) == ["--type=renderer", "--lang=en"]
    assert read_process_flags(12, str(tmp_path)) is None
    assert read_process_flags(13, str(tmp_path)) is None
    assert read_process_flags(None, str(tmp_path)) is None


class _Proc:
    def __init__(self, pid, returncode=None):
        self.pid, self.returncode = pid, returncode


def test_running_flags_come_from_the_current_process(tmp_path):
    (tmp_path / "7").mkdir()
    (tmp_path / "7" / "cmdline").write_bytes(b"chromium\0--no-zygote\0about:blank\0")
    c = chromium_mod.Chromium(LogRing(stream=io.StringIO()), extra_flags=["--no-zygote"], proc_root=str(tmp_path))
    assert c.running_flags() is None  # nothing launched
    c.proc = _Proc(7)
    assert c.running_flags() == ["--no-zygote", "about:blank"]
    assert c.flags[-2:] == ["--no-zygote", "about:blank"]
    c.proc = _Proc(7, returncode=0)
    assert c.running_flags() is None  # exited


class _StubChromium:
    ready = False
    product = None
    client = None
    extra_flags = ["--renderer-process-limit=1"]
    flags = BASE[:-1] + extra_flags + BASE[-1:]

    async def start(self):
        pass

    async def stop(self):
        pass

    def running_flags(self):
        return list(self.flags)


def test_health_reports_the_extra_and_the_running_flags():
    async def go():
        daemon = server.Daemon(LogRing(stream=io.StringIO()), _StubChromium(), None, proc_root="/nonexistent-proc")
        port = await daemon.start("127.0.0.1", 0)
        try:
            _, body = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
        finally:
            await daemon.stop()
        assert body["chromium_extra_flags"] == ["--renderer-process-limit=1"]
        assert body["chromium_running_flags"] == body["chromium_flags"] == _StubChromium.flags

    asyncio.run(go())


def test_a_bad_flag_word_stops_guestd_before_chromium(monkeypatch):
    monkeypatch.setenv(EXTRA_FLAGS_ENV, "!!!")
    monkeypatch.delenv("FLEETKIT_FAULT", raising=False)
    assert guestd_main.main(["--bind", "127.0.0.1", "--port", "0", "--chromium", os.devnull]) == 2
