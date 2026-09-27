"""The startup line's log dir must never carry the operator's absolute path into the bundle."""
from __future__ import annotations

import os

from hostd.__main__ import display_path


def test_relative_inside_cwd_stays_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("results/hostd")
    assert display_path("results/hostd") == os.path.join("results", "hostd")


def test_absolute_inside_cwd_is_made_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    full = os.path.join(str(tmp_path), "results", "hostd")
    shown = display_path(full)
    assert shown == os.path.join("results", "hostd")
    assert str(tmp_path) not in shown


def test_absolute_outside_cwd_is_basename_only(tmp_path, monkeypatch):
    inside = tmp_path / "work"
    outside = tmp_path / "elsewhere" / "hostd-logs"
    inside.mkdir()
    monkeypatch.chdir(inside)
    shown = display_path(str(outside))
    assert shown == "hostd-logs"
    assert str(tmp_path) not in shown
