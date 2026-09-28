"""Timeouts and trial parameters, with the defaults from design section 4."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass
class Timeouts:
    step_timeout_ms: int = 10000
    task_timeout_ms: int = 45000
    ready_timeout_s: float = 60.0  # 60 local; 90 on AWS for the first density-1 trial, then 3x observed startup_ms
    max_lifetime_s: float = 600.0
    idle_timeout_s: float = 120.0
    launch_interval_ms: int = 0
    proxy_margin_ms: int = 5000  # host proxy deadline = task_timeout_ms + proxy_margin_ms
    client_margin_ms: int = 5000  # driver client timeout = proxy deadline + client_margin_ms

    @property
    def proxy_deadline_ms(self) -> int:
        return self.task_timeout_ms + self.proxy_margin_ms

    @property
    def client_timeout_ms(self) -> int:
        return self.proxy_deadline_ms + self.client_margin_ms

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["proxy_deadline_ms"] = self.proxy_deadline_ms
        d["client_timeout_ms"] = self.client_timeout_ms
        return d


@dataclass
class TrialConfig:
    run_id: str
    backend: str
    density: int  # N: how many microVMs the trial starts at once
    timeouts: Timeouts = field(default_factory=Timeouts)
    vcpus: int = 2
    mem_mib: int = 2048
    fault: str | None = None
    fixture_check_url: str = "http://127.0.0.1:8081"
    fixture_base_url: str | None = None  # what the guest uses; defaults per backend
    products_source: str | None = None  # URL or path; default <fixture_check_url>/task-products.json
    host_id: str = ""
    # None: a numbered trial, ``d<density>-t<number>``, numbered from 1 within its density. Otherwise
    # the trial is labelled, not numbered (``warmup``, ``illustration``, ``fault-<name>``), and a label
    # already used in the run directory gets ``-2``, ``-3`` and so on.
    trial_label: str | None = None
    trial_kind: str = "ladder"  # schemas.TRIAL_KINDS; recorded in trial.json and tasks.csv
    criteria: dict | None = None  # criteria.evaluate_trial targets; None or empty = protocol only
    settle_s: float = 0.0  # wait before the trial, recording host cpu_util as trial.json pre_trial
    sample_interval_ms: int = 200  # guest proc sampling interval, sent in every task payload (0 = off)
    screenshot_each_step: bool = False  # untimed per-step screenshots (the illustration trial)
    hypervisor: dict | None = None  # the spec's hypervisor section, sent with every create (None: the daemon's own)
    release_after_ready_s: float = 0.0  # wait between every microVM ready and the tasks' release
    chromium_extra_flags: list = field(default_factory=list)  # the spec's, sent with every create
    # What every microVM's Chromium must run with after its binary (base + extra flags + start page), checked
    # against what each guest reads back from its browser process once ready; None: not checked.
    chromium_flags_expected: list | None = None
    # The spec's guest console and memory pages, sent with every create when the spec sets something other than
    # the default (None: not sent, and each microVM boots as every microVM did before the fields existed). A
    # console is checked once every microVM is ready: on a hypervisor, the guest's own kernel command line must
    # carry the mode's words (CONSOLE_KERNEL_ARGS); on docker, which has no guest kernel, the guest must report
    # the mode the host passed.
    console: str | None = None
    memory_pages: str | None = None

    def guest_fixture_base_url(self) -> str:
        if self.fixture_base_url:
            return self.fixture_base_url
        return DEFAULT_FIXTURE_BASE_URL[self.backend]


# The words each console mode puts on the guest kernel's command line: hostd.model.CONSOLE_BOOT_ARGS, which
# builds the command line (a test holds the two equal).
CONSOLE_KERNEL_ARGS = {
    "verbose": [],
    "quiet": ["quiet", "loglevel=3"],
    "quiet-i8042": ["quiet", "loglevel=3", "i8042.noaux", "i8042.nomux", "i8042.dumbkbd"],
}

# Design section 3: guests reach the fixture by service name on docker, by the bridge address on firecracker.
DEFAULT_FIXTURE_BASE_URL = {
    "docker": "http://fixture",
    "firecracker": "http://10.200.0.1:8081",
    "cloud-hypervisor": "http://10.200.0.1:8081",
}

DEFAULT_HOST_URL = "http://127.0.0.1:8090"
DEFAULT_FIXTURE_CHECK_URL = "http://127.0.0.1:8081"
DEFAULT_OTLP_ENDPOINT = "http://127.0.0.1:4318"
DEFAULT_SAMPLE_INTERVAL_MS = 200
FIXTURE_PROBE_INTERVAL_S = 1.0
FIXTURE_PROBE_TIMEOUT_S = 2.0
READY_POLL_INTERVAL_S = 0.25
READY_GRACE_S = 30.0  # driver waits ready_timeout_s + this for hostd to reach a terminal state
DESTROY_GRACE_S = 60.0
