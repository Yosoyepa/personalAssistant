"""Tests for single-source package version derived from importlib.metadata."""

from __future__ import annotations

import importlib
import importlib.metadata
from unittest.mock import patch

import personal_assistant
from personal_assistant import _canonical_version, _PackageVersion


def test_package_version_matches_installed_metadata() -> None:
    """Ensure __version__ resolves from installed package metadata."""
    expected = importlib.metadata.version("personal-assistant")
    assert personal_assistant.__version__ == expected
    assert isinstance(personal_assistant.__version__, str)


def test_package_version_falls_back_when_package_not_installed() -> None:
    """Ensure __version__ falls back to 0.0.0+local when package metadata is missing."""
    with patch(
        "importlib.metadata.version",
        side_effect=importlib.metadata.PackageNotFoundError("personal-assistant"),
    ):
        reloaded = importlib.reload(personal_assistant)
        assert reloaded.__version__ == "0.0.0+local"

    # Restore clean state
    importlib.reload(personal_assistant)


def test_package_version_pep440_equivalence() -> None:
    """Ensure _PackageVersion supports PEP 440 pre-release equivalence comparisons."""
    v_alpha = _PackageVersion("0.2.0a2")
    assert v_alpha == "0.2.0a2"
    assert v_alpha == "0.2.0-alpha.2"
    assert v_alpha == "0.2.0.alpha2"
    assert v_alpha != "0.2.0-beta.1"
    assert v_alpha != "0.3.0"
    assert v_alpha.__eq__(42) is False
    assert v_alpha.__eq__(None) is False

    v_beta = _PackageVersion("0.2.0b1")
    assert v_beta == "0.2.0-beta.1"
    assert v_beta != "0.2.0a2"

    v_rc = _PackageVersion("0.2.0rc1")
    assert v_rc == "0.2.0-rc.1"
    assert v_rc != "0.2.0a2"


def test_package_version_hash_and_canonical_helpers() -> None:
    """Ensure _PackageVersion maintains string hash and canonicalization coverage."""
    v = _PackageVersion("0.2.0a2")
    assert hash(v) == hash("0.2.0a2")
    assert _canonical_version(" 1.0.0rc2 ") == "1.0.0rc2"
    assert _canonical_version("1.0.0-beta.3") == "1.0.0b3"
