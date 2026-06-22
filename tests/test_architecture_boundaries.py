from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "personal_assistant"


def python_files_under(path: Path) -> list[Path]:
    return sorted(file for file in path.rglob("*.py") if "__pycache__" not in file.parts)


class ArchitectureBoundaryTests(unittest.TestCase):
    def assert_no_imports(self, path: Path, forbidden: tuple[str, ...]) -> None:
        for file in python_files_under(path):
            text = file.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{file.relative_to(PROJECT_ROOT)} imports {needle}")

    def test_domain_has_no_outer_layer_imports(self) -> None:
        self.assert_no_imports(
            SRC_ROOT / "domain",
            (
                "personal_assistant.application",
                "personal_assistant.adapters",
                "personal_assistant.contracts",
                "personal_assistant.infrastructure",
            ),
        )

    def test_application_does_not_import_adapters_or_infrastructure(self) -> None:
        self.assert_no_imports(
            SRC_ROOT / "application",
            (
                "personal_assistant.adapters",
                "personal_assistant.infrastructure",
            ),
        )

    def test_legacy_feature_packages_do_not_contain_python_modules(self) -> None:
        legacy_packages = (
            "agent_runtime",
            "agent_registry",
            "calendar",
            "channels",
            "documents",
            "memory",
            "notifications",
            "reminders",
            "scheduler",
            "shared",
            "stores",
            "tools",
        )
        for package in legacy_packages:
            package_path = SRC_ROOT / package
            tracked_like_files = [
                file
                for file in package_path.rglob("*.py")
                if package_path.exists() and "__pycache__" not in file.parts
            ]
            self.assertEqual(tracked_like_files, [], f"{package} still contains Python modules")

    def test_reminder_use_case_depends_on_ports_not_local_adapters(self) -> None:
        text = (SRC_ROOT / "application" / "use_cases" / "reminders.py").read_text(encoding="utf-8")
        for needle in ("LocalCalendarTool", "LocalNotificationTool", "InMemory", "personal_assistant.adapters"):
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
