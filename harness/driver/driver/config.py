"""Timeouts and trial parameters, with the defaults from design section 4."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass
class Timeouts:
    step_timeout_ms: int = 10000
    task_timeout_ms: int = 45000
    ready_timeout_s: float = 60.0  # 60 local; 90 on AWS for the first n=1 trial, then 3x observed startup_ms
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
    level_n: int
    repeat: int
    timeouts: Timeouts = field(default_factory=Timeouts)
    vcpus: int = 2
    mem_mib: int = 2048
    fault: str | None = None
    fixture_check_url: str = "http://127.0.0.1:8081"
    fixture_base_url: str | None = None  # what the guest uses; defaults per backend
    products_source: str | None = None  # URL or path; default <fixture_check_url>/task-products.json
    host_id: str = ""
    trial_label: str | None = None  # smoke uses this to name fault trials; warmup and illustration too
    kind: str = "ladder"  # schemas.TRIAL_KINDS; recorded in trial.json and tasks.csv
    criteria: dict | None = None  # criteria.evaluate_trial targets; None or empty = protocol only
    settle_s: float = 0.0  # wait before the trial, recording host cpu_util as trial.json pre_trial
    sample_interval_ms: int = 200  # guest proc sampling interval, sent in every task payload (0 = off)
    screenshot_each_step: bool = False  # untimed per-step screenshots (the illustration trial)

    def guest_fixture_base_url(self) -> str:
        if self.fixture_base_url:
            return self.fixture_base_url
        return DEFAULT_FIXTURE_BASE_URL[self.backend]


# Design section 3: guests reach the fixture by service name on docker, by the bridge address on firecracker.
DEFAULT_FIXTURE_BASE_URL = {
    "docker": "http://fixture",
    "firecracker": "http://10.200.0.1:8081",
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
