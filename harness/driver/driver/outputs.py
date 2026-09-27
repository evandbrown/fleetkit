"""Incremental outputs: append-only CSV writers and atomic JSON writes, and the run directory's readers.

Rows are appended and flushed one at a time so every file is valid after any interruption.
Re-running ``driver trial`` into the same run directory appends; headers are written once, and
trials keep counting within their density. A run directory written before the glossary (D56) is
evidence: the driver never appends to it, and ``RunDir``'s readers translate its names on read
(driver/legacy.py).
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
from pathlib import Path

from . import legacy, schemas

NUMBERED_TRIAL_ID = re.compile(r"^d(\d+)-t(\d+)$")


class LegacyRunDirError(Exception):
    """The run directory was written before the glossary; the driver does not append to it."""


class CsvAppender:
    def __init__(self, path: Path, columns: list[str]):
        self.path = Path(path)
        self.columns = list(columns)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        if not new:
            # Appending to a file an earlier driver started: keep its header, so rows stay aligned
            # with it (columns it lacks are dropped from the new rows; readers use row.get()).
            # RunDir.refuse_if_legacy() keeps this from ever meeting a pre-glossary header.
            existing = _read_header(self.path)
            if existing:
                self.columns = existing
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

    def append_many(self, rows: list[dict]) -> None:
        """Several rows under one lock and one flush (guest samples arrive a few hundred at a time)."""
        if not rows:
            return
        clean = [{c: _cell(r.get(c)) for c in self.columns} for r in rows]
        with self._lock:
            self._writer.writerows(clean)
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except Exception:
                pass


def _read_header(path: Path) -> list[str] | None:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return next(csv.reader(fh), None)
    except OSError:
        return None


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
    return list(iter_csv(path))


def iter_csv(path: Path):
    path = Path(path)
    if not path.exists():
        return
    with open(path, newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


class RunDir:
    """Layout of results/<run-id>/ (design section 11), and its readers."""

    TABLES = ("tasks", "steps", "microvms", "host_metrics", "guest_metrics")

    def __init__(self, root: Path, run_id: str | None = None):
        self.root = Path(root)
        self.run_id = run_id or self.root.name
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_csv = self.root / "tasks.csv"
        self.steps_csv = self.root / "steps.csv"
        self.microvms_csv = self.root / "microvms.csv"
        self.host_metrics_csv = self.root / "host_metrics.csv"
        self.guest_metrics_csv = self.root / "guest_metrics.csv"
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
        self._legacy: bool | None = None
        self._legacy_ids: dict[str, dict] | None = None

    # ---- the schema this directory was written in ---------------------------------------------
    @property
    def legacy(self) -> bool:
        """True when the directory was written before the glossary (read through driver/legacy.py)."""
        if self._legacy is None:
            self._legacy = legacy.is_legacy_dir(self.root)
        return self._legacy

    def refuse_if_legacy(self) -> None:
        if self.legacy:
            raise LegacyRunDirError(
                f"{self.root} was written before the glossary (old trial ids, {legacy.LEGACY_MICROVMS_CSV} or old "
                "CSV columns); "
                "the driver reads it but never writes into it: use a new --out directory")

    def legacy_trial_ids(self) -> dict[str, dict]:
        """Old trial id -> {trial_id, sequence, density, trial_number, trial_kind}; empty for a new directory."""
        if self._legacy_ids is None:
            entries = []
            if self.legacy and self.trials_dir.exists():
                for p in self.trials_dir.iterdir():
                    if not p.is_dir():
                        continue
                    doc = read_json(p / "trial.json")
                    is_case = doc is None and (p / "case.json").exists()
                    entries.append((p.name, doc if doc is not None else read_json(p / "case.json"), is_case))
            self._legacy_ids = legacy.trial_id_map(entries)
        return self._legacy_ids

    # ---- writing ------------------------------------------------------------------------------
    def open_writers(self) -> "Writers":
        self.refuse_if_legacy()
        return Writers(self)

    def trial_dir(self, trial_id: str) -> Path:
        d = self.trials_dir / trial_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def trial_json_path(self, trial_id: str) -> Path:
        return self.trial_dir(trial_id) / "trial.json"

    def _trial_dir_names(self) -> list[str]:
        if not self.trials_dir.exists():
            return []
        return [p.name for p in self.trials_dir.iterdir() if p.is_dir()]

    def next_sequence(self) -> int:
        """Run-wide execution order from 1: every trial and smoke case takes the next one."""
        return len(self._trial_dir_names()) + 1

    def next_trial_number(self, density: int) -> int:
        """Trials are numbered from 1 within their density, across every invocation into this directory."""
        taken = [int(m.group(2)) for m in map(NUMBERED_TRIAL_ID.match, self._trial_dir_names())
                 if m and int(m.group(1)) == int(density)]
        return max(taken, default=0) + 1

    def unique_label(self, label: str) -> str:
        """``warmup``, then ``warmup-2``, ``warmup-3``: a label used again in one run directory."""
        names = set(self._trial_dir_names())
        if label not in names:
            return label
        k = 2
        while f"{label}-{k}" in names:
            k += 1
        return f"{label}-{k}"

    def update_run_json(self, **fields) -> None:
        cur = read_json(self.run_json, {}) or {}
        cur.setdefault("run_id", self.run_id)
        cur.update(fields)
        write_json_atomic(self.run_json, cur)

    # ---- reading (old directories translated) -------------------------------------------------
    def list_trials(self) -> list[dict]:
        """Every trial.json, in execution order (``sequence``), in the glossary's names."""
        out = []
        if not self.trials_dir.exists():
            return out
        ids = self.legacy_trial_ids()
        for p in self.trials_dir.glob("*/trial.json"):
            t = read_json(p)
            if not isinstance(t, dict):
                continue
            if self.legacy and p.parent.name in ids:
                t = legacy.translate_trial(t, ids[p.parent.name])
            t["_path"] = str(p)
            out.append(t)
        out.sort(key=lambda t: (t.get("sequence") is None, t.get("sequence") or 0, t["_path"]))
        return out

    def list_cases(self) -> list[dict]:
        """Smoke microVM cases (trials/<id>/case.json), in execution order."""
        out = []
        if not self.trials_dir.exists():
            return out
        ids = self.legacy_trial_ids()
        for p in self.trials_dir.glob("*/case.json"):
            c = read_json(p)
            if not isinstance(c, dict):
                continue
            if self.legacy:
                c = legacy.translate_case(c, ids.get(p.parent.name))
            out.append(c)
        out.sort(key=lambda c: (c.get("sequence") or 0, str(c.get("trial_id"))))
        return out

    def table_path(self, table: str) -> Path:
        if table == "microvms" and self.legacy and not self.microvms_csv.exists():
            return self.root / legacy.LEGACY_MICROVMS_CSV
        return self.root / f"{table}.csv"

    def iter_rows(self, table: str):
        """Rows of tasks, steps, microvms, host_metrics or guest_metrics.csv in the glossary's names."""
        if table not in self.TABLES:
            raise ValueError(f"unknown table {table!r}")
        rows = iter_csv(self.table_path(table))
        if not self.legacy:
            yield from rows
            return
        ids = self.legacy_trial_ids()
        for r in rows:
            yield legacy.translate_row(table, r, ids)

    def read_rows(self, table: str) -> list[dict]:
        return list(self.iter_rows(table))

    def read_run_json(self) -> dict:
        doc = read_json(self.run_json, {}) or {}
        if self.legacy and isinstance(doc, dict):
            doc = legacy.translate_run_json(doc, self.legacy_trial_ids())
        return doc if isinstance(doc, dict) else {}

    def read_smoke_state(self) -> dict | None:
        doc = read_json(self.smoke_state_json, None)
        if isinstance(doc, dict):
            return legacy.translate_smoke_state(doc)
        return None

    def hostd_microvm_dir(self, hostd_dir: Path) -> Path | None:
        """hostd's ``<log-dir>/microvms`` (legacy.LEGACY_MICROVM_LOG_DIR on an older hostd), or None."""
        for name in ("microvms", legacy.LEGACY_MICROVM_LOG_DIR):
            p = Path(hostd_dir) / name
            if p.exists():
                return p
        return None


class Writers:
    def __init__(self, rundir: RunDir):
        self.tasks = CsvAppender(rundir.tasks_csv, schemas.TASKS_COLUMNS)
        self.steps = CsvAppender(rundir.steps_csv, schemas.STEPS_COLUMNS)
        self.microvms = CsvAppender(rundir.microvms_csv, schemas.MICROVMS_COLUMNS)
        self.host_metrics = CsvAppender(rundir.host_metrics_csv, schemas.HOST_METRICS_COLUMNS)
        self.guest_metrics = CsvAppender(rundir.guest_metrics_csv, schemas.GUEST_METRICS_COLUMNS)

    def close(self) -> None:
        for w in (self.tasks, self.steps, self.microvms, self.host_metrics, self.guest_metrics):
            w.close()
