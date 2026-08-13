"""Exit-code and output contract for the behavioral tier CLI.

The distinction these tests defend: exit 1 is a *harness* failure and exit 0
does not mean the model agreed with the labels. A run where every label
disagrees still exits 0, because the tier publishes disagreement rather than
gating on it. If that ever inverts, CI starts depending on a provider matching
one person's judgement, which is the coupling ADR-006 refuses.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from personal_assistant.evals.behavioral.__main__ import main
from personal_assistant.evals.behavioral.replay import read_cassette, write_cassette
from personal_assistant.evals.behavioral.runner import (
    EXTRACTION_SURFACE,
    INTENT_SURFACE,
    cassette_path,
)

SHIPPED_CORPUS = Path(__file__).resolve().parents[1] / "eval" / "behavioral"


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _json_run(*argv: str) -> tuple[int, dict[str, object], str]:
    code, out, err = _run(*argv, "--json")
    return code, json.loads(out), err


def _results(report: dict[str, object]) -> list[dict[str, object]]:
    rows = report["results"]
    assert isinstance(rows, list)
    return rows


class ShippedCorpusReplayTests(unittest.TestCase):
    """The committed corpus and cassettes must replay clean, offline."""

    def test_replay_exits_zero(self) -> None:
        code, report, _ = _json_run("--corpus", str(SHIPPED_CORPUS))
        self.assertEqual(code, 0)
        summary = report["summary"]
        assert isinstance(summary, dict)
        self.assertEqual(summary["errored"], 0)
        self.assertEqual(summary["completed"], summary["selected"])
        self.assertGreaterEqual(summary["selected"], 100)

    def test_replay_is_byte_identical_across_runs(self) -> None:
        _, first, _ = _run("--corpus", str(SHIPPED_CORPUS), "--json")
        _, second, _ = _run("--corpus", str(SHIPPED_CORPUS), "--json")
        self.assertEqual(first, second)

    def test_synthetic_cassettes_are_not_calibration_evidence(self) -> None:
        """The committed cassettes are a fixture, and must say so."""
        code, report, _ = _json_run("--corpus", str(SHIPPED_CORPUS))
        self.assertEqual(code, 0)
        self.assertEqual(report["provenance"], "synthetic")
        self.assertIs(report["calibrationEvidence"], False)

    def test_human_output_warns_when_rates_are_not_evidence(self) -> None:
        code, out, _ = _run("--corpus", str(SHIPPED_CORPUS))
        self.assertEqual(code, 0)
        self.assertIn("WARNING provenance=synthetic", out)
        self.assertIn("not a provider", out)

    def test_surface_filter_selects_one_surface(self) -> None:
        code, report, _ = _json_run(
            "--corpus", str(SHIPPED_CORPUS), "--surface", INTENT_SURFACE
        )
        self.assertEqual(code, 0)
        surfaces = {row["surface"] for row in _results(report)}
        self.assertEqual(surfaces, {INTENT_SURFACE})

    def test_split_filter_selects_one_split(self) -> None:
        code, report, _ = _json_run(
            "--corpus", str(SHIPPED_CORPUS), "--split", "holdout"
        )
        self.assertEqual(code, 0)
        splits = {row["split"] for row in _results(report)}
        self.assertEqual(splits, {"holdout"})

    def test_tag_filter_narrows_the_selection(self) -> None:
        code, full, _ = _json_run("--corpus", str(SHIPPED_CORPUS))
        self.assertEqual(code, 0)
        tagged_code, tagged, _ = _json_run(
            "--corpus", str(SHIPPED_CORPUS), "--tag", "relative-time"
        )
        self.assertEqual(tagged_code, 0)
        self.assertGreater(len(_results(tagged)), 0)
        self.assertLess(len(_results(tagged)), len(_results(full)))

    def test_replay_never_builds_a_live_provider(self) -> None:
        """Replay must not touch settings, secrets, or the egress allowlist.

        Asserted by making the live factory explode: if `--mode replay` ever
        reaches for a configured provider, this fails instead of quietly
        acquiring a network dependency in CI.
        """
        import personal_assistant.evals.behavioral.__main__ as cli

        def boom() -> object:
            raise AssertionError("replay must not construct a live provider")

        original = cli._live_provider_factory
        cli._live_provider_factory = boom  # type: ignore[assignment]  # reason: el test inyecta una factory que explota; su firma exacta no importa
        self.addCleanup(setattr, cli, "_live_provider_factory", original)
        code, _, _ = _json_run("--corpus", str(SHIPPED_CORPUS))
        self.assertEqual(code, 0)


class BrokenCassetteTests(unittest.TestCase):
    """A cassette that cannot answer is a hard failure, never a quiet pass."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.corpus = Path(self._tmp.name) / "behavioral"
        shutil.copytree(SHIPPED_CORPUS, self.corpus)

    def test_missing_entry_errors_the_label_and_exits_one(self) -> None:
        path = cassette_path(self.corpus, INTENT_SURFACE)
        cassette = read_cassette(path)
        trimmed = cassette.model_copy(update={"entries": list(cassette.entries[1:])})
        write_cassette(path, trimmed)

        code, report, _ = _json_run(
            "--corpus", str(self.corpus), "--surface", INTENT_SURFACE
        )
        self.assertEqual(code, 1)
        summary = report["summary"]
        assert isinstance(summary, dict)
        self.assertGreater(summary["errored"], 0)
        errors = [row["error"] for row in _results(report) if row["error"]]
        self.assertTrue(any("CassetteError" in str(error) for error in errors))

    def test_missing_cassette_file_exits_two(self) -> None:
        cassette_path(self.corpus, INTENT_SURFACE).unlink()
        code, _, err = _run("--corpus", str(self.corpus), "--surface", INTENT_SURFACE)
        self.assertEqual(code, 2)
        self.assertIn("INVALID CORPUS", err)
        self.assertIn("record", err)

    def test_malformed_cassette_json_exits_two(self) -> None:
        cassette_path(self.corpus, EXTRACTION_SURFACE).write_text(
            "{not json", encoding="utf-8"
        )
        code, _, err = _run(
            "--corpus", str(self.corpus), "--surface", EXTRACTION_SURFACE
        )
        self.assertEqual(code, 2)
        self.assertIn("INVALID CORPUS", err)

    def test_cassette_without_provenance_is_rejected(self) -> None:
        """An unlabeled cassette must not inherit the flattering default."""
        path = cassette_path(self.corpus, INTENT_SURFACE)
        raw = json.loads(path.read_text(encoding="utf-8"))
        del raw["provenance"]
        path.write_text(json.dumps(raw), encoding="utf-8")
        code, _, err = _run("--corpus", str(self.corpus), "--surface", INTENT_SURFACE)
        self.assertEqual(code, 2)
        self.assertIn("INVALID CORPUS", err)


class InvalidCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_absent_corpus_directory_exits_two(self) -> None:
        code, _, err = _run("--corpus", str(self.root / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("INVALID CORPUS", err)

    def test_invalid_manifest_exits_two_with_json_error(self) -> None:
        (self.root / "manifest.json").write_text(
            json.dumps({"schemaVersion": 1, "corpusId": "x"}), encoding="utf-8"
        )
        code, report, _ = _json_run("--corpus", str(self.root))
        self.assertEqual(code, 2)
        self.assertEqual(report["schemaVersion"], 1)
        self.assertIn("error", report)

    def test_unsafe_label_path_exits_two(self) -> None:
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "corpusId": "x",
                    "labelFiles": ["../../etc/passwd.json"],
                }
            ),
            encoding="utf-8",
        )
        code, _, err = _run("--corpus", str(self.root))
        self.assertEqual(code, 2)
        self.assertIn("INVALID CORPUS", err)


class LiveModeGuardTests(unittest.TestCase):
    def test_live_mode_without_a_provider_exits_two(self) -> None:
        """No provider configured must fail loudly, not fall back to replay."""
        code, _, err = _run("--corpus", str(SHIPPED_CORPUS), "--mode", "live")
        self.assertEqual(code, 2)
        self.assertIn("INVALID CORPUS", err)

    def test_unknown_mode_is_rejected_by_the_parser(self) -> None:
        with (
            self.assertRaises(SystemExit) as ctx,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            main(["--corpus", str(SHIPPED_CORPUS), "--mode", "wat"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":  # pragma: no cover  # reason: entrada directa de unittest; no se ejecuta bajo pytest
    unittest.main()
