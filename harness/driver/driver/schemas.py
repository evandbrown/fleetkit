"""Closed enums, CSV schemas and step names, copied from the design (sections 4 and 11).

Keep these identical to docs/harness-design.md; the README copies them too.
"""
from __future__ import annotations

# tasks.csv
TASKS_COLUMNS = [
    "run_id", "trial_id", "backend", "level_n", "repeat", "session_id", "slot", "task_id",
    "product_id", "dispatch_ts", "task_ms", "wall_ms", "ok", "failure_category", "failed_step",
    "bytes_received", "request_count", "guest_mem_available", "chromium_rss", "screenshot_path",
    "trace_id", "clock_offset_ns", "error",
]

# steps.csv
STEPS_COLUMNS = [
    "run_id", "trial_id", "task_id", "step_index", "name", "dispatch_ts", "settle_ts",
    "duration_ms", "error",
]

# sessions.csv
SESSIONS_COLUMNS = [
    "run_id", "trial_id", "session_id", "slot", "backend", "vcpus", "mem_mib", "created_ts",
    "process_started_ts", "ready_ts", "destroyed_ts", "startup_ms", "cleanup_ms", "outcome", "error",
]

# host_metrics.csv (long format; session_id is a session id or the literal ``host``)
HOST_METRICS_COLUMNS = ["ts", "session_id", "metric", "value"]

STEP_NAMES = ("home", "search", "open_product", "add_to_cart", "verify_cart")

FAILURE_CATEGORIES = (
    "ok", "step_timeout", "task_timeout", "assertion_failed", "navigation_error",
    "browser_crashed", "guest_unreachable", "session_not_ready",
)

SESSION_OUTCOMES = (
    "completed", "startup_timeout", "startup_error", "lifetime_expired", "idle_expired",
    "task_failure_destroyed",
)

SESSION_STATES = ("creating", "booting", "ready", "busy", "destroying", "destroyed", "failed")
TERMINAL_STATES = ("destroyed", "failed")

TRIAL_STATUSES = ("ok", "degraded", "failed")

BACKENDS = ("docker", "firecracker")

FAULTS = ("crash_on_start", "never_ready", "hang_task", "hang_step", "slow_step")

# Fault -> expected result (design section 4). ``session_state_after`` is checked before cleanup.
FAULT_EXPECTATIONS = {
    "crash_on_start": {"session_outcome": "startup_error", "task": None, "session_state_after": None},
    "never_ready": {"session_outcome": "startup_timeout", "task": None, "session_state_after": None},
    "hang_task": {"session_outcome": "task_failure_destroyed", "task": "guest_unreachable",
                  "session_state_after": None},
    "hang_step": {"session_outcome": "completed", "task": "step_timeout", "session_state_after": "ready"},
    "slow_step": {"session_outcome": "completed", "task": "step_timeout", "session_state_after": "ready"},
}


def fault_name(fault: str | None) -> str | None:
    """``slow_step:15000`` -> ``slow_step``; other faults unchanged."""
    if not fault:
        return None
    return fault.split(":", 1)[0]
