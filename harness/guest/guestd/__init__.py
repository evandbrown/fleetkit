"""fleetkit guest daemon (``guestd``).

Runs inside every session (a container locally, a microVM on AWS), starts Chromium,
runs one browser task at a time over the DevTools protocol and answers the host daemon
on port 8080. The contract is docs/harness-design.md, sections 2, 4 and 6.

The daemon has no telemetry SDK: it echoes the ``traceparent`` it received, returns a
realtime clock sample at request receipt and monotonic step offsets, and the host daemon
turns those into spans after the fact.
"""

from __future__ import annotations

__version__ = "0.2.0"
