#!/usr/bin/env python
"""Idempotent backfill of legacy report files into the authz store.

Registers every managed ``cerebro_*.html`` in the report directory as an
explicit ``owner_hash = NULL, status = 'ready'`` row (R10 §4.4/D4): legacy
reports become visible ONLY to local stdio callers or configured admins —
missing metadata always denies, never "legacy fallback".

MUST run BEFORE owner-denial takes effect in the rollout, so no legitimate
report silently vanishes. Symlinks and unclassifiable filenames are
rejected and reported, never silently skipped.

Usage:
    scripts/backfill_report_index.py [--report-dir ~/.cerebro/reports]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _kind_from_filename(name: str) -> str | None:
    """Parse the kind out of ``cerebro_<kind>_<UTC>_<slug>_<id>.html``.

    Mirrors charts.py `_report_filename` naming (kind='report' | 'research'
    | 'case_study'); 'story' ships through the storyteller with the same
    scheme. Longest prefix first so `case_study` wins over a bare split.
    """
    for kind in ("case_study", "research", "report", "story"):
        if name.startswith(f"cerebro_{kind}_"):
            return kind
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-dir",
        default=None,
        help="report directory (default: CEREBRO_REPORT_DIR or ~/.cerebro/reports)",
    )
    args = parser.parse_args()

    import os

    report_dir = Path(
        args.report_dir
        or os.environ.get("CEREBRO_REPORT_DIR")
        or "~/.cerebro/reports"
    ).expanduser()

    from cerebro_mcp.workflow.authz_store import get_authz_store

    summary = get_authz_store().backfill_legacy(
        report_dir, kind_parser=_kind_from_filename
    )
    print(
        f"backfill: {summary['added']} added, {summary['skipped']} already "
        f"indexed, {len(summary['rejected'])} rejected"
    )
    for name, why in summary["rejected"]:
        print(f"  REJECTED {name}: {why}")
    return 0 if not summary["rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
