"""Attribute names shared by every span, log record and metric the harness emits.

Design section 8: correlation keys on every signal are the run id, trial id, microVM id,
task id, backend and host id. All of them live under the ``fleetkit.`` namespace.
"""
from __future__ import annotations

from typing import Any, Mapping

ATTR_RUN_ID = "fleetkit.run_id"
ATTR_TRIAL_ID = "fleetkit.trial_id"
ATTR_MICROVM_ID = "fleetkit.microvm_id"
ATTR_TASK_ID = "fleetkit.task_id"
ATTR_BACKEND = "fleetkit.backend"
ATTR_HOST_ID = "fleetkit.host_id"

CORRELATION_KEYS: tuple[str, ...] = (
    ATTR_RUN_ID,
    ATTR_TRIAL_ID,
    ATTR_MICROVM_ID,
    ATTR_TASK_ID,
    ATTR_BACKEND,
    ATTR_HOST_ID,
)

# Short names accepted by helpers such as ``Telemetry.correlation(run_id=...)``.
SHORT_NAMES: Mapping[str, str] = {
    "run_id": ATTR_RUN_ID,
    "trial_id": ATTR_TRIAL_ID,
    "microvm_id": ATTR_MICROVM_ID,
    "task_id": ATTR_TASK_ID,
    "backend": ATTR_BACKEND,
    "host_id": ATTR_HOST_ID,
}

CORRELATION_PREFIX = "fleetkit."

# service.name values, one per component (section 1 and section 8 of the design).
SERVICE_DRIVER = "driver"
SERVICE_HOSTD = "hostd"
SERVICE_GUEST = "guest-daemon"

# Log event names.
EVENT_MICROVM_STATE = "microvm.state"

# Guest span attribute names (set by ``emit_guest_task``).
ATTR_STEP = "fleetkit.step"
ATTR_STEP_INDEX = "fleetkit.step_index"
ATTR_FAILURE_CATEGORY = "fleetkit.failure_category"
ATTR_FAILED_STEP = "fleetkit.failed_step"
ATTR_CLOCK_OFFSET_NS = "fleetkit.clock_offset_ns"
ATTR_GUEST_CLOCK_NS = "fleetkit.guest_clock_ns"
ATTR_TASK_MS = "fleetkit.task_ms"
ATTR_DURATION_MS = "fleetkit.duration_ms"
ATTR_BYTES_RECEIVED = "fleetkit.bytes_received"
ATTR_REQUEST_COUNT = "fleetkit.request_count"
ATTR_OK = "fleetkit.ok"


def correlation_attributes(**keys: Any) -> dict[str, str]:
    """Normalise ``run_id=...`` or ``**{"fleetkit.run_id": ...}`` into full attribute names.

    ``None`` values are dropped; everything else is stringified so it is safe as baggage.
    """
    out: dict[str, str] = {}
    for key, value in keys.items():
        if value is None:
            continue
        name = SHORT_NAMES.get(key, key)
        if not name.startswith(CORRELATION_PREFIX):
            name = CORRELATION_PREFIX + name
        out[name] = str(value)
    return out
