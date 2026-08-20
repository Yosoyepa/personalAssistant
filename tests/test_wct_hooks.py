"""Tests for WCT hook PreToolUse guard (human-only blessings and protected paths)."""

from __future__ import annotations

from pathlib import Path

from tools.wct.hooks.guard import pre_tool_use


def test_pre_tool_hook_blocks_no_verify() -> None:
    root = Path(__file__).parents[1]
    payload = {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify"}}

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_blocks_protected_write() -> None:
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(root / "governance/thresholds.yaml")},
    }

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_allows_source_write() -> None:
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(root / "src/personal_assistant/model.py")},
    }

    assert pre_tool_use(root, payload) == 0


def test_pre_tool_hook_blocks_agent_self_blessing() -> None:
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "uv run wct integrity bless --approved-by agent"},
    }

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_blocks_module_form_of_blessing() -> None:
    """`python -m tools.wct ...` is the same command wearing a different hat."""
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "python -m tools.wct integrity bless --approved-by agent"
        },
    }

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_blocks_uv_run_module_form_of_blessing() -> None:
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "uv run python -m tools.wct integrity bless --approved-by agent"
        },
    }

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_blocks_python3_module_form_of_blessing() -> None:
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "python3 -m tools.wct integrity bless --approved-by agent"
        },
    }

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_blocks_env_module_form_of_blessing() -> None:
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "PYTHONPATH=. uv run python -m tools.wct integrity bless --approved-by agent"
        },
    }

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_blocks_self_approving_manifest_update() -> None:
    """update-manifest with --approved-by regenerates the lock: human-only."""
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "uv run wct mutate update-manifest --approved-by agent --reason porque"
        },
    }

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_blocks_module_self_approving_manifest_update() -> None:
    root = Path(__file__).parents[1]
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "python -m tools.wct mutate update-manifest --approved-by agent --reason porque"
        },
    }

    assert pre_tool_use(root, payload) == 2


def test_pre_tool_hook_allows_plain_manifest_update() -> None:
    root = Path(__file__).parents[1]
    for command in (
        "uv run wct mutate update-manifest",
        "python -m tools.wct mutate update-manifest",
        "uv run python -m tools.wct mutate update-manifest",
    ):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        assert pre_tool_use(root, payload) == 0


def test_pre_tool_hook_allows_integrity_check() -> None:
    root = Path(__file__).parents[1]
    for command in (
        "uv run wct integrity check",
        "python -m tools.wct integrity check",
        "uv run python -m tools.wct integrity check",
        "PYTHONPATH=. uv run python -m tools.wct integrity check",
    ):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        assert pre_tool_use(root, payload) == 0
