"""Incremental outputs: append-only CSV writers and atomic JSON writes.

Rows are appended and flushed one at a time so every file is valid after any interruption.
Re-running ``driver trial`` into the same run directory appends; headers are written once.
"""
from __future__ import annotations

import csv
import json
import os
import threading
from pathlib import Path

from . import schemas


class CsvAppender:
    def __init__(self, path: Path, columns: list[str]):
        self.path = Path(path)
        self.columns = list(columns)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=self.columns, extrasaction="ignore")
        if new:
            self._writer.writeheader()
            self._fh.flush()

    def append(self, row: dict) -> None:
        clean = {c: _cell(row.get(c)) for c in self.columns}
        with self._lock:
            self._writer.writerow(clean)
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except Exception:
                pass


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v) if v != int(v) or abs(v) > 1e15 else f"{v:.6f}".rstrip("0").rstrip(".")
    return v


def write_json_atomic(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def read_csv(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class RunDir:
    """Layout of results/<run-id>/ (design section 11)."""

    def __init__(self, root: Path, run_id: str | None = None):
        self.root = Path(root)
        self.run_id = run_id or self.root.name
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_csv = self.root / "tasks.csv"
        self.steps_csv = self.root / "steps.csv"
        self.sessions_csv = self.root / "sessions.csv"
        self.host_metrics_csv = self.root / "host_metrics.csv"
        self.trials_dir = self.root / "trials"
        self.screenshots_dir = self.root / "screenshots"
        self.guest_logs_dir = self.root / "guest-logs"
        self.console_logs_dir = self.root / "console-logs"
        self.otlp_dir = self.root / "otlp"
        self.spans_jsonl = self.root / "spans.jsonl"
        self.logs_jsonl = self.root / "logs.jsonl"
        self.driver_log = self.root / "driver.log"
        self.manifest_json = self.root / "manifest.json"
        self.evidence_md = self.root / "evidence.md"
        self.report_md = self.root / "report.md"
        self.report_json = self.root / "report.json"
        self.smoke_report_md = self.root / "smoke-report.md"
        self.smoke_state_json = self.root / "smoke-state.json"
        self.run_json = self.root / "run.json"

    def open_writers(self) -> "Writers":
        return Writers(self)

    def trial_dir(self, trial_id: str) -> Path:
        d = self.trials_dir / trial_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def trial_json_path(self, trial_id: str) -> Path:
        return self.trial_dir(trial_id) / "trial.json"

    def list_trials(self) -> list[dict]:
        out = []
        if not self.trials_dir.exists():
            return out
        for p in sorted(self.trials_dir.glob("*/trial.json")):
            t = read_json(p)
            if isinstance(t, dict):
                t["_path"] = str(p)
                out.append(t)
        return out

    def next_trial_seq(self) -> int:
        if not self.trials_dir.exists():
            return 1
        return len([p for p in self.trials_dir.iterdir() if p.is_dir()]) + 1

    def update_run_json(self, **fields) -> None:
        cur = read_json(self.run_json, {}) or {}
        cur.setdefault("run_id", self.run_id)
        cur.update(fields)
        write_json_atomic(self.run_json, cur)


class Writers:
    def __init__(self, rundir: RunDir):
        self.tasks = CsvAppender(rundir.tasks_csv, schemas.TASKS_COLUMNS)
        self.steps = CsvAppender(rundir.steps_csv, schemas.STEPS_COLUMNS)
        self.sessions = CsvAppender(rundir.sessions_csv, schemas.SESSIONS_COLUMNS)
        self.host_metrics = CsvAppender(rundir.host_metrics_csv, schemas.HOST_METRICS_COLUMNS)

    def close(self) -> None:
        for w in (self.tasks, self.steps, self.sessions, self.host_metrics):
            w.close()
