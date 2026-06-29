import json
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
RUNBOOK_DIR = ROOT / "docs" / "runbook"
REMOTION_PACKAGE = ROOT / "media" / "remotion" / "package.json"
REMOTION_VIDEO = ROOT / "media" / "remotion" / "out" / "personal-assistant-architecture.mp4"

EXCLUDED_DIRS = {
    ".agents",
    ".codex",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "htmlcov",
    "node_modules",
}
TEXT_SUFFIXES = {
    ".css",
    ".drawio",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "openai_or_compatible_key": re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{18,}\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    "telegram_bot_token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35,}\b"),
}
SECRET_ASSIGNMENT = re.compile(
    r"""(?im)^\s*(?:export\s+)?"""
    r"""(?P<name>[A-Z0-9_]+)"""
    r"""\s*[:=]\s*["']?(?P<value>[^"'\s#]*)"""
)
PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "disabled",
    "dummy",
    "example",
    "local",
    "local-admin-token",
    "placeholder",
    "test",
}


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_under_excluded_dir(path: Path) -> bool:
    try:
        relative_parts = path.relative_to(ROOT).parts
    except ValueError:
        return True
    return any(part in EXCLUDED_DIRS for part in relative_parts)


def _tracked_paths() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.splitlines())


def _text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or _is_under_excluded_dir(path):
            continue
        if path.name in {".env", ".env.local"}:
            continue
        if path.name == ".env.example" or path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return files


def _looks_like_secret_name(name: str) -> bool:
    return (
        name in {"API_KEY", "PASSWORD", "PRIVATE_KEY", "SECRET", "SECRET_KEY", "TOKEN"}
        or name.endswith(("_API_KEY", "_PASSWORD", "_PRIVATE_KEY", "_SECRET", "_SECRET_KEY", "_TOKEN"))
        or "API_KEY" in name
        or "PRIVATE_KEY" in name
    )


class PublicArtifactVerificationTest(unittest.TestCase):
    def test_readme_links_every_runbook(self) -> None:
        readme_text = README.read_text(encoding="utf-8")
        runbooks = sorted(RUNBOOK_DIR.glob("*.md"))

        self.assertTrue(runbooks, "expected at least one runbook under docs/runbook/")
        missing_links = [_relative(path) for path in runbooks if _relative(path) not in readme_text]

        self.assertEqual(
            [],
            missing_links,
            f"README.md must link every runbook; missing: {', '.join(missing_links)}",
        )

    def test_drawio_artifact_exists_and_is_valid_xml(self) -> None:
        drawio_files = sorted(
            [
                path
                for pattern in ("*.drawio", "*.drawio.xml")
                for path in ROOT.rglob(pattern)
                if path.is_file() and not _is_under_excluded_dir(path)
            ]
        )

        self.assertTrue(drawio_files, "expected a public draw.io artifact (*.drawio)")
        for path in drawio_files:
            try:
                tree = ET.parse(path)
            except ET.ParseError as exc:
                self.fail(f"{_relative(path)} is not valid XML: {exc}")

            root_tag = tree.getroot().tag.rsplit("}", 1)[-1]
            self.assertIn(
                root_tag,
                {"mxfile", "mxGraphModel"},
                f"{_relative(path)} should look like a draw.io XML file",
            )

    def test_linkedin_post_artifact_exists(self) -> None:
        linkedin_posts = sorted(
            path
            for path in _text_files()
            if "linkedin" in path.name.lower() and path.suffix.lower() in {".md", ".txt"}
        )

        self.assertTrue(
            linkedin_posts,
            "expected a LinkedIn post artifact with 'linkedin' in the filename",
        )
        empty_posts = [_relative(path) for path in linkedin_posts if not path.read_text(encoding="utf-8").strip()]
        self.assertEqual([], empty_posts, f"LinkedIn post artifacts cannot be empty: {empty_posts}")

    def test_remotion_package_has_scripts(self) -> None:
        self.assertTrue(
            REMOTION_PACKAGE.exists(),
            f"expected Remotion package.json at {_relative(REMOTION_PACKAGE)}",
        )

        package_data = json.loads(REMOTION_PACKAGE.read_text(encoding="utf-8"))
        scripts = package_data.get("scripts")

        self.assertIsInstance(scripts, dict, "Remotion package.json must define a scripts object")
        self.assertTrue(scripts, "Remotion package.json scripts object cannot be empty")
        self.assertTrue(
            any("remotion" in str(command).lower() for command in scripts.values()),
            "at least one Remotion package script should invoke remotion",
        )

    def test_remotion_video_artifact_exists(self) -> None:
        self.assertTrue(
            REMOTION_VIDEO.exists(),
            f"expected rendered Remotion video at {_relative(REMOTION_VIDEO)}",
        )
        self.assertGreater(
            REMOTION_VIDEO.stat().st_size,
            100_000,
            "rendered Remotion video should not be an empty placeholder",
        )

    def test_public_files_do_not_contain_secrets(self) -> None:
        tracked = _tracked_paths()

        self.assertNotIn(".env", tracked, ".env must not be tracked")
        self.assertNotIn(".env.local", tracked, ".env.local must not be tracked")

        findings: list[str] = []
        for path in _text_files():
            text = path.read_text(encoding="utf-8")
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(text):
                    findings.append(f"{_relative(path)} matched {label}")

            for match in SECRET_ASSIGNMENT.finditer(text):
                if not _looks_like_secret_name(match.group("name")):
                    continue
                value = match.group("value").strip().strip("'\"")
                normalized = value.lower()
                if (
                    normalized in PLACEHOLDER_VALUES
                    or normalized.startswith(("your_", "example_", "test_", "$", "<"))
                    or normalized.endswith("_here")
                ):
                    continue
                findings.append(f"{_relative(path)} has a non-placeholder secret assignment")

        self.assertEqual([], findings, "Potential secrets found in public/versionable files")


if __name__ == "__main__":
    unittest.main()
