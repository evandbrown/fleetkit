#!/usr/bin/env python3
"""Prove the observability pipeline with the standard library only.

Sends one span as OTLP-JSON to the collector's HTTP receiver, then waits for it to show up
in the file exporter's traces.jsonl and checks that Grafana answers. Exit 0 on success.

    python3 observability/check.py [--endpoint http://127.0.0.1:4318] [--otlp-dir results/dev/lgtm/otlp]
                                   [--grafana http://127.0.0.1:3000] [--timeout 30]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--endpoint", default=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318"))
    parser.add_argument("--otlp-dir", default="results/dev/lgtm/otlp")
    parser.add_argument("--grafana", default="http://127.0.0.1:3000")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    now = time.time_ns()
    trace_id = os.urandom(16).hex()
    span_id = os.urandom(8).hex()
    marker = f"lgtm-check-{now}"
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "lgtm-check"}}]},
                "scopeSpans": [
                    {
                        "scope": {"name": "observability/check.py"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": marker,
                                "kind": 1,
                                "startTimeUnixNano": str(now - 1_000_000),
                                "endTimeUnixNano": str(now),
                                "attributes": [{"key": "fleetkit.run_id", "value": {"stringValue": marker}}],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    req = urllib.request.Request(
        f"{args.endpoint.rstrip('/')}/v1/traces",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"collector accepted the span: HTTP {resp.status} trace_id={trace_id}")
    except (urllib.error.URLError, OSError) as exc:
        print(f"FAIL: collector at {args.endpoint} did not accept the span: {exc}")
        return 1

    traces_file = os.path.join(args.otlp_dir, "traces.jsonl")
    deadline = time.monotonic() + args.timeout
    found = False
    while time.monotonic() < deadline and not found:
        if os.path.exists(traces_file):
            with open(traces_file, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if marker in line and trace_id in line:
                        found = True
                        break
        if not found:
            time.sleep(0.5)
    if not found:
        print(f"FAIL: {traces_file} did not receive the span within {args.timeout:.0f}s")
        return 1
    print(f"file exporter wrote the span: {traces_file}")

    try:
        with urllib.request.urlopen(f"{args.grafana.rstrip('/')}/api/health", timeout=5) as resp:
            body = json.loads(resp.read().decode())
            print(f"grafana answers on {args.grafana}: database={body.get('database')} version={body.get('version')}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"FAIL: grafana at {args.grafana} did not answer: {exc}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
