#!/usr/bin/env python
"""Verify an extracted ``.sql`` template reproduces its f-string byte-for-byte.

The ``.sql`` migration is a purely mechanical refactor, so the emitted SQL must
be IDENTICAL afterwards. This compares a rendered template against a snapshot of
every spec's SQL taken before the migration started.

    python scripts/dev/check_sql_extraction.py governance delegation_power \\
        --frag src=rpc_log_indexer.v_delegate_events_gnosis \\
        --frag gov_db=governance_db --frag space=gnosis.eth \\
        --frag delegation_match=delegation --frag cap=200

Exit 0 on an exact match. On a mismatch it prints a unified diff and exits 1 —
the diff is the whole point, so read it rather than guessing at the fragments.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cerebro_mcp.tools.visualization import sql_loader  # noqa: E402

DEFAULT_BASELINE = os.environ.get(
    "SQL_BASELINE",
    str(REPO / ".sql-baseline.json"),
)


def baseline_sql(baseline: dict, spec_key: str) -> list[str]:
    """Every DISTINCT SQL body recorded for a spec key.

    A key can legitimately render more than one body — the snapshot sweeps a
    filter grid, and a filter that changes a predicate changes the SQL. An
    extraction is correct when the template can reproduce ALL of them, so the
    caller checks against each in turn.
    """
    bodies = []
    for full_key, entry in baseline.items():
        if full_key.rsplit("|", 1)[-1] != spec_key:
            continue
        sql = entry.get("sql")
        if sql is not None and sql not in bodies:
            bodies.append(sql)
    return bodies


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app")
    ap.add_argument("spec_key")
    ap.add_argument("--frag", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--template", default=None,
                    help="template stem, when it differs from spec_key")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    args = ap.parse_args()

    fragments = {}
    for item in args.frag:
        if "=" not in item:
            print(f"bad --frag {item!r}, expected NAME=VALUE", file=sys.stderr)
            return 2
        name, _, value = item.partition("=")
        fragments[name] = value

    baseline = json.loads(Path(args.baseline).read_text())
    wanted = baseline_sql(baseline, args.spec_key)
    if not wanted:
        print(f"no baseline entry for spec key {args.spec_key!r}", file=sys.stderr)
        return 2

    sql_loader.reset_cache_for_tests()
    try:
        rendered = sql_loader.load_sql(args.app, args.template or args.spec_key, **fragments)
    except sql_loader.SqlTemplateError as exc:
        print(f"TEMPLATE ERROR: {exc}", file=sys.stderr)
        return 1

    if rendered in wanted:
        print(f"OK  {args.app}/{args.template or args.spec_key}.sql matches baseline "
              f"({len(wanted)} distinct body/bodies recorded)")
        return 0

    print(f"MISMATCH  {args.app}/{args.template or args.spec_key}.sql", file=sys.stderr)
    closest = min(wanted, key=lambda w: len(set(w.splitlines()) ^ set(rendered.splitlines())))
    diff = difflib.unified_diff(
        closest.splitlines(), rendered.splitlines(),
        "baseline", "rendered", lineterm="",
    )
    for line in diff:
        print(line, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
