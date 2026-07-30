#!/usr/bin/env python3
"""Claude Code PreToolUse adapter over scripts/agent_context/guard.py.

Thin by design: all detection logic lives in the vendor-neutral guard so CI and
other agent products can reuse it. On a 'warn' verdict this asks for confirmation
(permissionDecision: ask) with the guard's message and the lesson id; it never
hard-denies — the tests and CI gates are the authoritative enforcement, and a hook
that blocks work it merely suspects is a hook people disable.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "agent_context" / "guard.py"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return 0
    try:
        out = subprocess.check_output(
            [sys.executable, str(GUARD), command], text=True, timeout=10
        )
        result = json.loads(out)
    except Exception:
        return 0  # guard trouble must never block normal work

    if result.get("verdict") != "warn":
        return 0

    reasons = "; ".join(
        f["message"] + (f" [lesson: {f['lesson']}]" if f.get("lesson") else "")
        for f in result.get("findings", [])
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reasons,
        }
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
