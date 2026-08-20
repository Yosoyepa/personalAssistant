"""Tests for WCT Gherkin acceptance pipeline and IR analysis."""

from __future__ import annotations

from pathlib import Path

from tools.wct.accept.pipeline import ir_dry, mutations, parse_feature


def test_parser_builds_canonical_ir(tmp_path: Path) -> None:
    feature = tmp_path / "sample.feature"
    feature.write_text(
        """Feature: Add
Scenario Outline: numbers
  Given value "<value>"
  Then result "<result>"
Examples:
  | value | result |
  | 2     | 4      |
""",
        encoding="utf-8",
    )

    result = parse_feature(feature)

    assert result["feature"] == "Add"
    assert result["scenarios"][0]["examples"] == [{"value": "2", "result": "4"}]
    assert len(mutations(result)) == 2


def test_ir_dry_finds_repeated_step_shape(tmp_path: Path) -> None:
    feature = tmp_path / "duplicate.feature"
    feature.write_text(
        """Feature: duplicate
Scenario: one
  Given value "1"
Scenario: two
  Given value "2"
""",
        encoding="utf-8",
    )

    report = ir_dry(parse_feature(feature))

    assert report["count"] == 1
    assert report["findings"][0]["kind"] == "placeholder-variant"


def test_placeholder_variant_names_step_and_both_scenarios(tmp_path: Path) -> None:
    """The finding must say WHICH step collided with WHICH scenario where."""
    feature = tmp_path / "duplicate.feature"
    feature.write_text(
        """Feature: duplicate
Scenario: one
  Given value "1"
Scenario: two
  Given value "2"
""",
        encoding="utf-8",
    )

    report = ir_dry(parse_feature(feature))
    finding = report["findings"][0]

    assert finding["step"] == 'value "1"'
    assert finding["other_scenario"] == "one"
    assert finding["scenario"] == "two"
    assert "one" in finding["message"]
    assert "two" in finding["message"]
    assert "value" in finding["message"]
