"""Closed enums, CSV schemas and step names, copied from the design (sections 4 and 11).

Keep these identical to docs/harness-design.md; the README copies them too. The words follow the
glossary in docs/method.md: a run carries out trials, a trial starts N microVMs at once and N is the
trial's density. Run directories written before the glossary are read through driver/legacy.py.
"""
from __future__ import annotations

# tasks.csv. trial_number is empty for trials that are labelled rather than numbered
# (warm-up, illustration, fault and case trials).
TASKS_COLUMNS = [
    "run_id", "trial_id", "backend", "density", "trial_number", "microvm_id", "slot", "task_id",
    "product_id", "dispatch_ts", "task_ms", "wall_ms", "ok", "failure_category", "failed_step",
    "bytes_received", "request_count", "guest_mem_available", "chromium_rss", "screenshot_path",
    "trace_id", "clock_offset_ns", "error",
    # capacity experiment additions, appended so older readers keep their column positions
    "timing_valid", "guestd_cpu_ms", "trial_kind",
]

# steps.csv
STEPS_COLUMNS = [
    "run_id", "trial_id", "task_id", "step_index", "name", "dispatch_ts", "settle_ts",
    "duration_ms", "error",
    "bytes_received", "request_count",
]

# microvms.csv: one row per microVM a trial started
MICROVMS_COLUMNS = [
    "run_id", "trial_id", "microvm_id", "slot", "backend", "vcpus", "mem_mib", "created_ts",
    "process_started_ts", "ready_ts", "destroyed_ts", "startup_ms", "cleanup_ms", "outcome", "error",
    # boot phases on the host clock, from the guest's /health at readiness (null when the guest lacks them)
    "kernel_start_ts", "guestd_start_ts", "chromium_launch_ts", "chromium_ready_ts",
]

# host_metrics.csv (long format). ``subject`` is what the row measures: a microVM id, the literal
# ``host``, ``driver`` for the driver's own CPU and RSS, or ``fixture`` for the fixture probe's
# fixture_rtt_ms.
HOST_METRICS_COLUMNS = ["ts", "subject", "metric", "value"]

# guest_metrics.csv (long format): the guest's proc_samples, one row per sample and metric. ``ts`` is on
# the host clock: (guest_clock_ns + t_ns + clock_offset_ns) / 1e9. Metrics: cpu_total_ms, cpu_idle_ms,
# mem_available, psi_cpu_some_total_us, and per process group cpu_ms.<group>, rss_bytes.<group>, procs.<group>.
GUEST_METRICS_COLUMNS = ["ts", "trial_id", "microvm_id", "task_id", "metric", "value"]

STEP_NAMES = ("home", "search", "open_product", "add_to_cart", "verify_cart")

FAILURE_CATEGORIES = (
    "ok", "step_timeout", "task_timeout", "assertion_failed", "navigation_error",
    "browser_crashed", "guest_unreachable", "microvm_not_ready",
)

MICROVM_OUTCOMES = (
    "completed", "startup_timeout", "startup_error", "lifetime_expired", "idle_expired",
    "task_failure_destroyed",
)

MICROVM_STATES = ("creating", "booting", "ready", "busy", "destroying", "destroyed", "failed")
TERMINAL_STATES = ("destroyed", "failed")

TRIAL_STATUSES = ("ok", "degraded", "failed")

# Trial kinds (trial.json and tasks.csv ``trial_kind``). Ladder and boundary trials count toward a
# density's verdict and the run's result; smoke trials count too, so a smoke run's report keeps its
# densities. Warm-up, illustration and fault trials are labelled, not numbered, and never count.
TRIAL_KINDS = ("ladder", "boundary", "warmup", "illustration", "fault", "smoke")
COUNTED_TRIAL_KINDS = ("ladder", "boundary", "smoke")

# Guest process groups in proc_samples (guest daemon, /proc/<pid>/cmdline)
GUEST_GROUPS = ("browser", "renderer", "gpu", "network", "utility", "zygote", "chromium_other", "guestd", "other")

BACKENDS = ("docker", "firecracker")

FAULTS = ("crash_on_start", "never_ready", "hang_task", "hang_step", "slow_step")

# Fault -> expected result (design section 4). ``microvm_state_after`` is checked before cleanup.
FAULT_EXPECTATIONS = {
    "crash_on_start": {"microvm_outcome": "startup_error", "task": None, "microvm_state_after": None},
    "never_ready": {"microvm_outcome": "startup_timeout", "task": None, "microvm_state_after": None},
    "hang_task": {"microvm_outcome": "task_failure_destroyed", "task": "guest_unreachable",
                  "microvm_state_after": None},
    "hang_step": {"microvm_outcome": "completed", "task": "step_timeout", "microvm_state_after": "ready"},
    "slow_step": {"microvm_outcome": "completed", "task": "step_timeout", "microvm_state_after": "ready"},
}


def fault_name(fault: str | None) -> str | None:
    """``slow_step:15000`` -> ``slow_step``; other faults unchanged."""
    if not fault:
        return None
    return fault.split(":", 1)[0]
