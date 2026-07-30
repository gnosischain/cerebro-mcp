"""SQL in the mini-app query planes lives in ``.sql`` files, not in Python.

Rule 0 of ``queries/AGENTS.md``. The reasons are practical, not aesthetic: a
``.sql`` file pastes into a client so a production failure is reproducible in one
step; ClickHouse's ``{name:Type}`` bound parameters stop fighting Python's braces;
and a clause built in Python has nowhere to record WHY it is shaped that way.

That last one is not hypothetical. The treasury month-end restriction lived in
``governance_explorer.py`` as two concatenated string literals. The reason it
joined on BOTH ``chain_id`` and ``snapshot_date`` — chains publish independently
and are months apart, so matching the date alone sums two chains' different dates
into one bucket — was written down nowhere, and the query it fed went on to
exhaust the 20s interactive budget in production.

SCOPE. Enforced for ``tools/visualization`` (the mini-app backends and their
planes). It is deliberately NOT repo-wide: ``semantic/sql_compiler.py``,
``semantic/flow_queries.py`` and ``semantic/graph_profiles.py`` exist to emit SQL
from a schema, and ``workflow/event_store.py`` carries embedded SQLite DDL. A
survey found 289 SQL-bearing literals across 32 modules; asserting on all of them
would be a rule nothing enforces, which this repo already has a lesson about
(``negated-grep-passes-when-tool-absent``).
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

VISUALIZATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "cerebro_mcp" / "tools" / "visualization"
)

#: A SQL STATEMENT keyword. Deliberately not every SQL word: a predicate
#: expression with no FROM (``environment={env:String} AND chain_id IN (…)``) is
#: legitimate Python composition — its shape depends on runtime cardinality, and
#: `sql_loader`'s ``@fragment`` mechanism exists precisely to receive it. What
#: must not be in Python is a statement or a named clause block.
#:
#: CASE-SENSITIVE, and that is a deliberate limit rather than an oversight: SQL
#: keywords are uppercase everywhere in this repo, while ordinary prose in an
#: error message ("loaded from cache", "select a chain") is not. Matching
#: case-insensitively would flag that prose in every module and the allowlist
#: would swallow the rule. The cost is that lowercase inline SQL slips through —
#: acceptable, because nothing here writes lowercase keywords and a reviewer
#: reading `select ... from` in a Python string has a much louder signal anyway.
STATEMENT = re.compile(
    r"(?<![\w`])(SELECT|FROM|INNER JOIN|LEFT JOIN|UNION ALL|GROUP BY)(?![\w`])"
)

#: A pure ARM SEPARATOR — whitespace plus `UNION ALL` and nothing else.
#:
#: The one carve-out, and it is narrow by construction: none of Rule 0's three
#: purposes apply to it. There is nothing to paste into a client, no bound
#: parameter whose braces could be doubled, and no rationale to record. A .sql
#: file containing only `UNION ALL` would make the calling code harder to follow,
#: not easier. The arms it joins each come from their own file, and so does the
#: envelope that wraps them (activity.sql).
#:
#: Anything carrying a relation, a projection or an expression is a STATEMENT and
#: is still flagged — `test_the_separator_carve_out_is_narrow` pins that.
SEPARATOR_ONLY = re.compile(r"\A\s*UNION\s+ALL\s*\Z")


def sql_violations(path: pathlib.Path) -> list[tuple[int, str]]:
    """SQL literals in ``path``, excluding pure arm separators."""
    return [
        (line, text) for line, text in sql_literals(path)
        if not SEPARATOR_ONLY.match(text)
    ]

#: Modules whose job IS emitting SQL, with the reason. Shrink-only: an entry that
#: no longer has a violation fails the staleness test below, so this can only get
#: shorter. A new entry needs a reason that is about the module's purpose, not
#: about the effort of moving the strings.
KNOWN_BUILDERS: dict[str, str] = {
    "mini_apps.py": (
        "the generic result envelope (count() OVER (), ROW_CAP) — the executor "
        "applied to every spec, not a query about anything"
    ),
    "metric_lab.py": (
        "compiles a query from a user-chosen model, dimension and grain at "
        "runtime; there is no fixed statement to put in a file"
    ),
}


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """ids of Constant nodes that are docstrings — prose, not code."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                out.add(id(first.value))
    return out


def sql_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    """Non-docstring string literals in ``path`` that contain SQL statement
    syntax. Docstrings are excluded because describing SQL is not writing it —
    half this repo's module docs quote a query to explain a hazard."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and STATEMENT.search(node.value)
    ]


MODULES = sorted(p for p in VISUALIZATION.rglob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_sql_statement_in_python(module: pathlib.Path):
    found = sql_violations(module)
    if module.name in KNOWN_BUILDERS:
        pytest.skip(f"{module.name}: {KNOWN_BUILDERS[module.name]}")
    assert not found, (
        f"{module.name} builds SQL in Python at line(s) "
        f"{[line for line, _ in found]}. Move it to "
        f"queries/<app>/<name>.sql (or _cte_/_pred_/_join_/_anchor_ for a "
        f"fragment) and render it with sql_loader.load_sql. See "
        f"queries/AGENTS.md Rule 0."
    )


def test_the_builder_allowlist_is_shrink_only():
    """An entry that no longer corresponds to a real violation must be deleted.
    Without this the list becomes a permanent exemption nobody revisits, and the
    rule quietly stops meaning anything."""
    stale = [
        name
        for name in KNOWN_BUILDERS
        if not any(p.name == name and sql_violations(p) for p in MODULES)
    ]
    assert stale == [], (
        f"{stale} are allowlisted but clean — delete the entries so the rule "
        f"covers them"
    )


@pytest.mark.parametrize("name", ["governance_explorer.py", "cow_explorer.py"])
def test_the_mini_app_backends_are_clean_and_stay_clean(name):
    """The two planes the rule was written for. Named explicitly rather than left
    to the parametrized sweep so that adding either to KNOWN_BUILDERS cannot
    silently re-open it.

    cow_explorer.py was the original debt: 32 SQL-bearing literals, cleared into
    18 .sql files. Its one remaining literal is the pure `UNION ALL` arm
    separator, which `SEPARATOR_ONLY` excludes."""
    module = VISUALIZATION / name
    assert module.name not in KNOWN_BUILDERS
    assert sql_violations(module) == []


def test_the_separator_carve_out_is_narrow():
    """The carve-out must admit ONLY a bare separator. If it ever admitted a
    statement, the rule would be unenforceable and nothing would say so."""
    # Uppercase only, matching STATEMENT: a lowercase `union all` is never
    # flagged upstream, so the carve-out must not pretend to handle it.
    for glue in ["\nUNION ALL\n", "UNION ALL", "\n UNION  ALL \n", "\t UNION ALL "]:
        assert SEPARATOR_ONLY.match(glue), glue
    for statement in [
        "\nSELECT * FROM (\n",
        "UNION ALL SELECT x FROM t",
        "SELECT 1 UNION ALL SELECT 2",
        "\nUNION ALL\nSELECT chain_id FROM cow_db.trades",
        "  LEFT JOIN np ON np.token=t.sell_token\n",
    ]:
        assert not SEPARATOR_ONLY.match(statement), statement


def test_the_separator_lives_in_exactly_one_place():
    """A separator repeated inline in six call sites is how the UNION-ALL glue
    hid among 32 real violations. One named constant, joined by one helper."""
    text = (VISUALIZATION / "cow_explorer.py").read_text(encoding="utf-8")
    assert text.count('"\\nUNION ALL\\n"') == 1, (
        "the arm separator should appear once, as UNION_ALL_ARM"
    )
    assert "UNION_ALL_ARM" in text and "_union_arms" in text


def test_the_detector_actually_detects():
    """A guard that stopped guarding is this repo's most-repeated mistake class,
    and this one is a regex over an AST — two things that fail silently. So it is
    checked against known-positive and known-negative inputs."""
    positives = [
        "SELECT max(x) FROM t",
        "INNER JOIN months AS m ON t.chain_id = m.chain_id",
        "  UNION ALL ",
        "SELECT * FROM (",
    ]
    for text in positives:
        assert STATEMENT.search(text), text
    negatives = [
        # A predicate with no FROM is legal Python composition.
        "environment={env:String} AND chain_id={chain_id:UInt64}",
        "t.balance_raw != 0",
        "quantity DESC, token_address",
        # Must not match inside an identifier or a backticked column.
        "`from_address`",
        "FROM_UNIXTIME(x)",
        "SELECTED",
        # Prose in an error message — the reason the match is case-sensitive.
        "loaded from cache",
        "select a chain first",
    ]
    for text in negatives:
        assert not STATEMENT.search(text), text
