"""Gates for the command guard.

The guard is itself a gate, and this repo has already been bitten by a gate that
silently stopped guarding (`negated-grep-passes-when-tool-absent`). So: every
pattern is asserted to fire on input it should catch, an innocuous command is
asserted NOT to fire, and every cited lesson id is asserted to resolve — a warning
pointing at a record that does not exist is worse than no warning, because it reads
as authoritative.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from cerebro_mcp.loaders.cerebro_lessons import cerebro_lessons

GUARD_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "agent_context" / "guard.py"
)


def _load_guard():
    """Imported by path: scripts/ is not a package, and keeping the guard
    vendor-neutral (a plain CLI) is deliberate so CI and other agent products can
    call it without importing cerebro_mcp."""
    spec = importlib.util.spec_from_file_location("cerebro_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


guard = _load_guard()

# (command, expected pattern id). Each is a command an agent plausibly runs.
WARN_CASES = [
    ("sed -i '' 's/a/b/' src/cerebro_mcp/static/governance.html", "edit-generated-bundle"),
    ("vim src/cerebro_mcp/static/assets/governance/app.js", "edit-generated-bundle"),
    ("make dev", "dev-server-cannot-reproduce-bundle-bug"),
    ("npm run dev --prefix ui", "dev-server-cannot-reproduce-bundle-bug"),
    ("git commit -m 'wip'", "user-commits-their-own-work"),
    ("git push origin main", "user-commits-their-own-work"),
    ("git add -A", "user-commits-their-own-work"),
    ("! rg 'token' dist/", "negated-non-posix-tool"),
    ("! jq -e .x file.json", "negated-non-posix-tool"),
    (
        "vim src/cerebro_mcp/tools/visualization/queries/cow/events.sql",
        "sql-edit-needs-restart",
    ),
]

OK_CASES = [
    ".venv/bin/python -m pytest tests/ -q",
    "npm test --prefix ui",
    "make build-ui-governance",
    "grep -rqE '0xv1c' ui/dist",  # the FIXED form must not trip the negated check
    "git status --porcelain",
    "git log --oneline -5",
    "cat src/cerebro_mcp/tools/visualization/queries/cow/events.sql",
]


@pytest.mark.parametrize("command,pattern", WARN_CASES)
def test_dangerous_command_warns_with_the_expected_pattern(command, pattern):
    result = guard.analyze(command)
    assert result["verdict"] == "warn", command
    assert pattern in [f["pattern"] for f in result["findings"]], command


@pytest.mark.parametrize("command", OK_CASES)
def test_innocuous_command_is_not_flagged(command):
    """A guard that cries wolf gets turned off. `git status` and reading a .sql
    must stay silent, and the FIXED `grep -rqE` form must not trip the
    negated-tool check that exists to catch `! rg`."""
    assert guard.analyze(command)["verdict"] == "ok", command


def test_every_cited_lesson_resolves():
    """A finding pointing at a non-existent record is worse than none — it reads
    as authoritative and cannot be looked up."""
    for command, _ in WARN_CASES:
        for finding in guard.analyze(command)["findings"]:
            lesson = finding.get("lesson")
            if lesson:
                assert lesson in cerebro_lessons.lessons, (
                    f"{finding['pattern']} cites {lesson!r}, which has no record"
                )


def test_findings_carry_a_message_and_pattern():
    for command, _ in WARN_CASES:
        for finding in guard.analyze(command)["findings"]:
            assert finding.get("pattern")
            assert finding.get("message")


def test_guard_never_denies_only_warns():
    """The verdict vocabulary is deliberately ok/warn with no deny: the tests and
    CI are the authority, and a hook that blocks work it merely suspects is a hook
    people disable."""
    verdicts = {guard.analyze(c)["verdict"] for c, _ in WARN_CASES}
    verdicts |= {guard.analyze(c)["verdict"] for c in OK_CASES}
    assert verdicts <= {"ok", "warn"}
