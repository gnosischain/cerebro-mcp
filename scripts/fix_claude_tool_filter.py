"""Re-enable every cerebro MCP tool in Claude Desktop's per-session filter.

WHAT THIS FIXES
---------------

Claude Desktop stores a per-session ``enabledMcpTools`` map in the session
JSON file. Each entry is ``"<scope>:<server>:<tool>": bool``. When every
entry for a given server prefix is ``false``, Claude Desktop's
``LocalMcpServerManager.createAllServers`` code logs::

    Filtering out local MCP server "cerebro" (extension: undefined) — all
    tools disabled

and drops the **entire server** before the session's model ever sees its
tools. The result is that the chat model reports "only the storyteller
tools are available" (or similar) even though the MCP server is healthy
and returning every tool correctly.

CRITICAL: Claude Desktop holds the ``enabledMcpTools`` state **in memory**
while it is running and flushes it back to the session JSON on every
session activity. If you patch the JSON while Claude Desktop is open, the
next save will clobber your patch byte-for-byte with the stale in-memory
state. **You MUST fully quit Claude Desktop (Cmd+Q) before running this
script.** The script refuses to run if it detects a live Claude Desktop
process.

USAGE
-----

Fix the most-recently-touched session that has disabled cerebro tools::

    uv run python scripts/fix_claude_tool_filter.py

Fix a specific session file::

    uv run python scripts/fix_claude_tool_filter.py <path-to-session.json>

Dry-run (report what would change, don't write anything). Safe to run
while Claude Desktop is open::

    uv run python scripts/fix_claude_tool_filter.py --dry-run

Fix every session file globally (scan all Claude Desktop session dirs)::

    uv run python scripts/fix_claude_tool_filter.py --all

Also re-enable other local MCPs that got caught in the same mass-disable
(e.g. ``xmtp-docs``, ``Claude in Chrome``)::

    uv run python scripts/fix_claude_tool_filter.py --server '*'

``--server`` accepts a glob. The default is ``cerebro*`` which matches
both ``cerebro`` and ``cerebro-dev``.

Override the running-Claude-Desktop guard (only useful for dry runs or if
you are certain the write will not be clobbered)::

    uv run python scripts/fix_claude_tool_filter.py --force

RECOMMENDED WORKFLOW
--------------------

1. ``Cmd+Q`` Claude Desktop (menu ``Claude → Quit Claude``, not just close
   the window).
2. ``uv run python scripts/fix_claude_tool_filter.py`` from the
   cerebro-mcp repo.
3. Reopen Claude Desktop and reopen the affected chat. Tools should be
   visible immediately.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

SESSION_ROOTS = [
    Path.home()
    / "Library/Application Support/Claude/claude-code-sessions",
    Path.home()
    / "Library/Application Support/Claude/local-agent-mode-sessions",
]


def claude_desktop_is_running() -> bool:
    """Return True if Claude Desktop appears to be running.

    Uses ``ps ax`` to list every process's full command line and looks for
    the Electron main process binary inside ``/Applications/Claude.app``.
    The process name is ``Claude`` with no suffix (the helper processes are
    named ``Claude Helper``, ``Claude Helper (Renderer)``, etc., which
    contain spaces and defeat ``pgrep -x``).

    Returns ``False`` on any error so that the guard fails open.
    """
    try:
        result = subprocess.run(
            ["ps", "ax", "-o", "command"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        # Match the main Electron process, not the helpers. The main process
        # command line is exactly "/Applications/Claude.app/Contents/MacOS/Claude"
        # with no trailing flags.
        stripped = line.strip()
        if stripped.endswith("/Claude.app/Contents/MacOS/Claude"):
            return True
    return False


def iter_session_files() -> Iterable[Path]:
    for root in SESSION_ROOTS:
        if not root.exists():
            continue
        yield from root.rglob("local_*.json")


def tool_keys_for_server(
    emt: dict, server_glob: str
) -> list[tuple[str, str]]:
    """Return [(full_key, server_name), ...] for every matching entry."""
    out: list[tuple[str, str]] = []
    for key in emt:
        # Keys look like "local:<server>:<toolname>" or "<uuid>:<tool>".
        # We only touch the "local:" ones.
        if not key.startswith("local:"):
            continue
        rest = key[len("local:") :]
        parts = rest.split(":", 1)
        if len(parts) != 2:
            continue
        server_name = parts[0]
        if fnmatch.fnmatchcase(server_name, server_glob):
            out.append((key, server_name))
    return out


def find_dirty_sessions(
    server_glob: str,
) -> list[tuple[float, Path, dict[str, dict]]]:
    """Return [(mtime, path, stats_by_server), ...] for every session file
    where at least one matching server has every tool set to false."""
    results: list[tuple[float, Path, dict[str, dict]]] = []
    for p in iter_session_files():
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        emt = d.get("enabledMcpTools") or {}
        if not isinstance(emt, dict):
            continue
        matches = tool_keys_for_server(emt, server_glob)
        if not matches:
            continue
        by_server: dict[str, dict] = {}
        for key, server in matches:
            bucket = by_server.setdefault(
                server, {"true": 0, "false": 0}
            )
            if emt[key] is True:
                bucket["true"] += 1
            elif emt[key] is False:
                bucket["false"] += 1
        # A server is "dirty" if it has false entries AND either zero trues
        # or the trues are not real tool names. We keep the file if ANY
        # matching server has any false entries.
        has_any_false = any(
            b["false"] > 0 for b in by_server.values()
        )
        if has_any_false:
            results.append((p.stat().st_mtime, p, by_server))
    results.sort(reverse=True)
    return results


def patch_session(
    path: Path, server_glob: str, dry_run: bool
) -> dict[str, tuple[int, int]]:
    """Patch one session file. Return {server: (falses_flipped, trues_kept)}."""
    d = json.loads(path.read_text())
    emt = d.get("enabledMcpTools") or {}
    matches = tool_keys_for_server(emt, server_glob)
    stats: dict[str, tuple[int, int]] = {}
    flipped_by_server: dict[str, int] = {}
    kept_by_server: dict[str, int] = {}
    for key, server in matches:
        if emt[key] is False:
            if not dry_run:
                emt[key] = True
            flipped_by_server[server] = flipped_by_server.get(server, 0) + 1
        elif emt[key] is True:
            kept_by_server[server] = kept_by_server.get(server, 0) + 1
    # Summary per server
    for server in sorted(set(flipped_by_server) | set(kept_by_server)):
        stats[server] = (
            flipped_by_server.get(server, 0),
            kept_by_server.get(server, 0),
        )
    if stats and not dry_run:
        backup = path.with_suffix(
            path.suffix + f".bak.{int(time.time())}"
        )
        shutil.copy2(path, backup)
        path.write_text(json.dumps(d, indent=2))
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-enable cerebro MCP tools in Claude Desktop session files."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        help=(
            "Specific session file to patch. If omitted, patches the most "
            "recently modified dirty session (use --all for every session)."
        ),
    )
    parser.add_argument(
        "--server",
        default="cerebro*",
        help=(
            "Glob of server names to re-enable (default: 'cerebro*'). Use "
            "'*' to include every local MCP server."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Patch every dirty session file, not just the most recent.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without modifying anything.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Skip the 'Claude Desktop is running' safety check. Only use "
            "this if you understand that Claude Desktop will overwrite the "
            "patched JSON on its next session save."
        ),
    )
    args = parser.parse_args()

    if not args.dry_run and not args.force and claude_desktop_is_running():
        print(
            "ERROR: Claude Desktop is currently running.\n"
            "\n"
            "Claude Desktop holds the enabledMcpTools state in memory and "
            "will overwrite any patch to the session JSON on its next save. "
            "Patching while it is running will appear to succeed and then "
            "silently revert within seconds.\n"
            "\n"
            "Do this instead:\n"
            "  1. Fully quit Claude Desktop: menu Claude -> Quit Claude, "
            "or press Cmd+Q on the Claude window. 'Close window' is NOT "
            "enough - the app must exit.\n"
            "  2. Re-run this script.\n"
            "  3. Reopen Claude Desktop and reopen the affected chat.\n"
            "\n"
            "If you really want to run anyway (e.g. for a --dry-run style "
            "inspection), pass --force.",
            file=sys.stderr,
        )
        return 3

    if args.path:
        target_files: list[Path] = [Path(args.path).expanduser()]
        if not target_files[0].exists():
            print(f"error: {target_files[0]} not found", file=sys.stderr)
            return 2
    else:
        dirty = find_dirty_sessions(args.server)
        if not dirty:
            print(
                f"No session files with disabled {args.server!r} tools found. "
                "Nothing to fix."
            )
            return 0
        if args.all:
            target_files = [p for _, p, _ in dirty]
        else:
            target_files = [dirty[0][1]]
            print(
                f"Found {len(dirty)} session file(s) with disabled "
                f"{args.server!r} tools. Patching the most recent one.\n"
                f"(Use --all to patch all of them.)\n"
            )

    total_flipped = 0
    total_files = 0
    for path in target_files:
        stats = patch_session(
            path, server_glob=args.server, dry_run=args.dry_run
        )
        if not stats:
            continue
        flipped = sum(f for f, _ in stats.values())
        total_flipped += flipped
        total_files += 1 if flipped else 0

        # Pretty-print
        mtime = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)
        )
        session_id = path.stem  # local_<uuid>
        try:
            title = json.loads(path.read_text()).get("title") or "(no title)"
        except Exception:
            title = "(unreadable)"
        title_str = title if len(title) <= 60 else title[:57] + "..."
        print(f"[{mtime}] {session_id}  title={title_str!r}")
        for server, (flipped_n, kept_n) in stats.items():
            action = "would flip" if args.dry_run else "flipped"
            print(
                f"    {server}: {action} {flipped_n} false→true "
                f"(kept {kept_n} already-true)"
            )
        if not args.dry_run and flipped:
            print(f"    backup written next to file (.bak.<timestamp>)")

    if args.dry_run:
        print(
            f"\nDry run: would have flipped {total_flipped} entries across "
            f"{total_files} file(s). Re-run without --dry-run to apply."
        )
    else:
        print(
            f"\nDone. Flipped {total_flipped} tool entries across "
            f"{total_files} session file(s)."
        )
        if total_files:
            print(
                "Restart Claude Desktop (or reopen the affected chat) and "
                "the cerebro tools will be visible again."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
