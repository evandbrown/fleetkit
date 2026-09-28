"""The spec's extra Chromium flags on their way to the guest: checked in the create request, kept on the
microVM record, sent as FLEETKIT_CHROMIUM_EXTRA_FLAGS (docker) or fleetkit.chromium_extra_flags= (kernel
command line), and absent from both when there are none, so the default microVM is what it always was."""
from __future__ import annotations

import shlex

import pytest

from hostd.backends import CloudHypervisorBackend, FirecrackerBackend
from hostd.backends.docker import DockerBackend
from hostd.manager import ApiError
from hostd.model import MicroVM, encode_chromium_flags, validate_chromium_flags
from hostd.runner import Runner

# guestd's tests hold the same literal (guestd.chromium.decode_extra_flags reverses it).
VECTOR = (["--renderer-process-limit=1", "--js-flags=--max-old-space-size=512 --jitless"],
          "WyItLXJlbmRlcmVyLXByb2Nlc3MtbGltaXQ9MSIsIi0tanMtZmxhZ3M9LS1tYXgtb2xkLXNwYWNlLXNpemU9NTEyIC0taml0bGVzcyJd")


def _microvm(backend, flags=(), slot=3):
    return MicroVM(id="s%03d-abcdef01" % slot, slot=slot, backend=backend.name, address=backend.address(slot),
                   vcpus=2, mem_mib=2048, fault=None, ready_timeout_s=60, max_lifetime_s=600, idle_timeout_s=120,
                   fixture_base_url=backend.fixture_base_url, hypervisor={"name": backend.name},
                   chromium_extra_flags=list(flags))


def test_encoding_is_the_guests():
    flags, word = VECTOR
    assert encode_chromium_flags(flags) == word
    assert " " not in word and "=" not in word and '"' not in word


def test_the_shape_is_checked_not_the_allowed_list():
    assert validate_chromium_flags(None) == [] and validate_chromium_flags([]) == []
    assert validate_chromium_flags(["--anything-goes=1"]) == ["--anything-goes=1"]  # expand.py owns the list
    for bad in ("--no-zygote", ["no-dashes"], [3], ["--a\n"], ["--é"], ["--x"] * 17, ["--" + "x" * 511]):
        with pytest.raises(ValueError):
            validate_chromium_flags(bad)


def test_docker_passes_the_flags_in_the_environment():
    b = DockerBackend(Runner(dry_run=True), "/tmp/logs")
    flags, word = VECTOR
    text = shlex.join(b.run_argv(_microvm(b, flags)))
    assert "-e FLEETKIT_CHROMIUM_EXTRA_FLAGS=" + word in text
    assert "CHROMIUM" not in shlex.join(b.run_argv(_microvm(b)))


@pytest.mark.parametrize("make", [
    lambda: FirecrackerBackend(Runner(dry_run=True), "/var/log/fleetkit", firecracker_bin="/usr/local/bin/firecracker",
                               kernel="/k", rootfs="/r"),
    lambda: CloudHypervisorBackend(Runner(dry_run=True), "/var/log/fleetkit", kernel="/k", rootfs="/r"),
])
def test_hypervisors_pass_the_flags_on_the_kernel_command_line(make):
    b = make()
    flags, word = VECTOR
    plain = b.boot_args(_microvm(b))
    assert "chromium" not in plain
    assert b.boot_args(_microvm(b, flags)) == plain + " fleetkit.chromium_extra_flags=" + word
    assert b.sidecar(_microvm(b, flags))["chromium_extra_flags"] == flags


def test_create_keeps_the_flags_on_the_record_and_refuses_bad_ones(manager, guest, ctx):
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1, "chromium_extra_flags": ["--no-zygote"]}, ctx)[0]["id"]
    assert manager.get(sid).record()["chromium_extra_flags"] == ["--no-zygote"]
    with pytest.raises(ApiError) as e:
        manager.create_microvms({"count": 1, "chromium_extra_flags": "--no-zygote"}, ctx)
    assert e.value.status == 400 and "chromium_extra_flags" in e.value.message


def test_no_flags_by_default(manager, guest, ctx):
    guest.ready.add("guest-0")
    sid = manager.create_microvms({"count": 1}, ctx)[0]["id"]
    assert manager.get(sid).record()["chromium_extra_flags"] == []
