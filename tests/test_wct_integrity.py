"""Tests for WCT integrity classification of protected-path drift."""

from __future__ import annotations

from tools.wct.integrity.engine import _classify


def test_classify_modified_file_is_violation() -> None:
    """A protected file whose hash changed is always a violation."""
    problems, warnings = _classify({"a.yml": "old"}, {"a.yml": "new"}, {"a.yml"})
    assert problems == ["modificado: a.yml"]
    assert warnings == []


def test_classify_new_protected_file_is_violation() -> None:
    """A new file under a protected route requires a bless."""
    problems, warnings = _classify({}, {"b.yml": "hash"}, {"b.yml"})
    assert problems == ["nuevo protegido: b.yml"]
    assert warnings == []


def test_classify_missing_tracked_file_is_violation() -> None:
    """A tracked protected file missing from disk is a real deletion."""
    problems, warnings = _classify({"c.yml": "hash"}, {}, {"c.yml"})
    assert problems == ["eliminado protegido: c.yml"]
    assert warnings == []


def test_classify_missing_untracked_file_is_warning() -> None:
    """A protected path that git does not track (e.g. gitignored local skills)
    cannot be attacked through a PR: warning, not violation."""
    problems, warnings = _classify({"skill/SKILL.md": "hash"}, {}, set())
    assert problems == []
    assert warnings == ["ausente no versionado (omitido): skill/SKILL.md"]


def test_classify_missing_fails_closed_without_git() -> None:
    """Without git tracking info every missing protected file stays a violation."""
    problems, warnings = _classify({"d.yml": "hash"}, {}, None)
    assert problems == ["eliminado protegido: d.yml"]
    assert warnings == []


def test_classify_in_sync() -> None:
    """No drift means no violations and no warnings."""
    problems, warnings = _classify({"e.yml": "h"}, {"e.yml": "h"}, {"e.yml"})
    assert problems == []
    assert warnings == []
