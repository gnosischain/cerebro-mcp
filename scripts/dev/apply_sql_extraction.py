#!/usr/bin/env python
"""Splice ``sql_loader.load_sql(...)`` calls in place of extracted f-strings.

The ``.sql`` extraction is split in two on purpose. Writing the template files
parallelises perfectly (one file per query, no two writers ever touch the same
path); editing Python does not, because every builder in a mini-app lives in one
module and concurrent writers would clobber each other. So templates are written
first, and this applies every Python edit in a single deterministic pass.

Input is JSON — a list of ``{file, builder, python_var, load_call}`` records:

    python scripts/dev/apply_sql_extraction.py edits.json [--dry-run]

Edits are applied bottom-up within each file so earlier replacements cannot
shift the offsets of later ones. Anything ambiguous is refused rather than
guessed at: this rewrites source, and a wrong splice is far more expensive than
a failed run.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def find_assignment(tree: ast.Module, builder: str, var: str) -> ast.Assign | None:
    """The `var = <expr>` assignment inside `def builder`.

    Module-level constants are addressed with builder == "" — portfolio keeps
    its SQL there rather than inside a spec builder.
    """
    if builder:
        scopes = [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == builder
        ]
    else:
        scopes = [tree]
    hits: list[ast.Assign] = []
    for scope in scopes:
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == var:
                hits.append(node)
    if len(hits) != 1:
        return None
    return hits[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edits", help="JSON file: [{file, builder, python_var, load_call}]")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    edits = json.loads(Path(args.edits).read_text())
    by_file: dict[str, list[dict]] = {}
    for e in edits:
        by_file.setdefault(e["file"], []).append(e)

    failures: list[str] = []
    applied = 0

    for rel, group in sorted(by_file.items()):
        path = REPO / rel
        source = path.read_text()
        tree = ast.parse(source)
        lines = source.splitlines(keepends=True)
        # Byte offset of the start of each 1-indexed line.
        starts = [0]
        for line in lines:
            starts.append(starts[-1] + len(line))

        planned = []
        for e in group:
            node = find_assignment(tree, e.get("builder", ""), e["python_var"])
            if node is None:
                failures.append(
                    f"{rel}: could not uniquely locate "
                    f"{e.get('builder') or '<module>'}.{e['python_var']}"
                )
                continue
            value = node.value
            if not isinstance(value, (ast.JoinedStr, ast.Constant)):
                failures.append(
                    f"{rel}: {e['python_var']} is not a string literal "
                    f"({type(value).__name__}) — refusing to splice"
                )
                continue
            begin = starts[value.lineno - 1] + value.col_offset
            end = starts[value.end_lineno - 1] + value.end_col_offset
            planned.append((begin, end, e["load_call"], e["python_var"]))

        # Bottom-up: an earlier splice must not move a later one's offsets.
        planned.sort(key=lambda p: p[0], reverse=True)
        for begin, end, call, var in planned:
            source = source[:begin] + call + source[end:]
            applied += 1
            print(f"  {rel}: {var}")

        if not args.dry_run and planned:
            # Parse before writing: a splice that does not compile is worse
            # than no splice at all.
            try:
                ast.parse(source)
            except SyntaxError as exc:
                failures.append(f"{rel}: result does not parse ({exc}) — NOT written")
                continue
            path.write_text(source)

    print(f"\n{applied} assignment(s) {'would be ' if args.dry_run else ''}spliced")
    if failures:
        print(f"\n{len(failures)} FAILURE(S):", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
