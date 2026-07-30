#!/usr/bin/env python3
"""Vendor-neutral command guard: given a shell command about to run against this
repo, report known-dangerous patterns before they execute.

This is the AUTHORITATIVE guard logic — agent-product hooks (e.g.
`.claude/hooks/bash_guard.py`) are thin adapters over it, so the same detection is
reusable by CI or another agent product. Safety never depends on it alone: the
tests and CI gates are the real enforcement. The guard exists to surface a warning
at the point of action, **citing the lesson record** that explains why.

Every finding names a lesson id in `src/cerebro_mcp/prompts/lessons/`, so the
warning is a pointer into the corpus rather than a standalone opinion that can
drift away from it.

Usage:
    python scripts/agent_context/guard.py "<command string>"

Output: JSON on stdout:
    {"verdict": "ok" | "warn", "findings": [{"pattern","message","lesson"}]}

Exit code is always 0 for ok/warn (advisory); 2 on usage error.
"""

from __future__ import annotations

import json
import re
import sys


def analyze(command: str) -> dict:
    findings: list[dict[str, str]] = []

    # Hand-editing the generated bundles. They are git-tracked, which makes them
    # look editable; they are build output and any edit is lost on the next build.
    if re.search(
        r"(?:^|[|&;]|\s)(?:vim?|nano|code|sed\s+-i|tee|>>?)\s[^|&;]*"
        r"src/cerebro_mcp/static/",
        command,
    ):
        findings.append({
            "pattern": "edit-generated-bundle",
            "message": (
                "src/cerebro_mcp/static/ is BUILD OUTPUT (git-tracked, which makes it "
                "look editable). Edit ui/src/... and run `make build-ui-<app>`; a "
                "hand-edit here is lost on the next build"
            ),
            "lesson": "stale-prebuilt-miniapp-bundle",
        })

    # A dev server proves nothing about the served bundle.
    if re.search(r"\bmake\s+dev\b", command) or re.search(r"npm\s+run\s+dev", command):
        findings.append({
            "pattern": "dev-server-cannot-reproduce-bundle-bug",
            "message": (
                "`make dev` serves LIVE ui/src, not the prebuilt bundles the MCP "
                "serves — it cannot reproduce a bundle bug, and a fix verified here "
                "is not verified for the served app. Rebuild with `make build-ui-<app>`"
            ),
            "lesson": "stale-prebuilt-miniapp-bundle",
        })

    # The user commits their own work. Stated in AGENTS.md; easy to forget.
    if re.search(r"\bgit\s+(commit|push)\b", command) or re.search(
        r"\bgit\s+add\b", command
    ):
        findings.append({
            "pattern": "user-commits-their-own-work",
            "message": (
                "the user commits their own work in this repo — stop at 'changes ready "
                "in the working tree' rather than committing, pushing or staging"
            ),
            "lesson": "",
        })

    # Negated non-POSIX tool in a gate: absence becomes a pass.
    if re.search(r"!\s*(rg|fd|jq|yq)\b", command):
        findings.append({
            "pattern": "negated-non-posix-tool",
            "message": (
                "a negated non-POSIX tool passes when the tool is MISSING (exit 127 "
                "negated is success), which silently disables the gate — use POSIX "
                "`grep -E`, or check for the tool explicitly"
            ),
            "lesson": "negated-grep-passes-when-tool-absent",
        })

    # Restarting after a .sql edit is the fix; NOT restarting is the trap. Warn on
    # the query-plane edit itself so the restart is remembered.
    if re.search(
        r"(?:^|[|&;]|\s)(?:vim?|nano|code|sed\s+-i|tee|>>?)\s[^|&;]*"
        r"tools/visualization/queries/",
        command,
    ):
        findings.append({
            "pattern": "sql-edit-needs-restart",
            "message": (
                "sql_loader is lru_cache'd — a .sql edit needs a SERVER RESTART to "
                "take effect (rebuilding the UI does not help)"
            ),
            "lesson": "sql-loader-cache-needs-restart",
        })

    return {"verdict": "warn" if findings else "ok", "findings": findings}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: guard.py \"<command>\"", file=sys.stderr)
        return 2
    print(json.dumps(analyze(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
