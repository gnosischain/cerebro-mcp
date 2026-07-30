"""Load mini-app ClickHouse queries from ``.sql`` files.

Why these live on disk instead of in Python f-strings:

* **They are debuggable.** A ``.sql`` file pastes into a ClickHouse client with
  only the handful of ``@fragment`` tokens to fill in. An f-string requires
  importing the module and calling a spec builder just to see the query.
* **ClickHouse's own parameter syntax stops fighting Python's.** Bound params
  are written natively as ``{env:String}``. Inside an f-string every one of
  them had to be doubled to ``{{env:String}}``, and a forgotten pair of braces
  is a runtime error nobody sees until that code path runs.

Two substitution namespaces, deliberately disjoint:

``@name``
    Python-side **composition** — a predicate fragment, a shared CTE block, a
    whitelisted ORDER BY. Substituted here, before the query ever leaves the
    process. These are trusted internal fragments, NEVER user input.

``{name:Type}``
    ClickHouse **bound parameters**. Passed through untouched and bound by the
    driver. This is where user input goes, always.

The sigil is ``@`` because ClickHouse SQL has no other use for it — verified
across every query in this repo at extraction time.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

#: A composition token. Deliberately narrow — lowercase, digits, underscore —
#: so an ``@`` appearing in a string literal or comment cannot be mistaken for
#: one. Anchored with a word boundary at the end so ``@chain`` does not match
#: inside ``@chain_sql``.
_TOKEN = re.compile(r"@([a-z_][a-z0-9_]*)")

QUERIES_DIR = Path(__file__).resolve().parent / "queries"


class SqlTemplateError(ValueError):
    """Raised when a template and its fragments disagree.

    Both directions are errors, and both are the same bug wearing different
    hats: the ``.sql`` file and its Python caller have drifted apart. Failing
    loudly at render time is the whole point — a silently unsubstituted
    ``@chain_sql`` would reach ClickHouse as a syntax error at the worst
    possible moment, and a silently ignored kwarg would drop a filter.
    """


def is_fragment(name: str) -> bool:
    """A leading underscore marks a FRAGMENT — a file substituted into another
    query rather than executed on its own (``_cte_``, ``_pred_``, ``_join_``,
    ``_anchor_``, ``_expr_``)."""
    return name.startswith("_")


def _strip_comment_lines(text: str) -> str:
    """Drop lines that are entirely a ``--`` comment.

    Applied to EVERY template. This is what makes Rule 0 workable: a ``.sql`` file
    can carry its full rationale — the whole point of moving the SQL out of Python
    — while still rendering exactly the string the old literal rendered.

    For a FRAGMENT the argument is obvious: it is substituted into the middle of
    another statement, so a header would land mid-expression. ``... AND @pred``
    becomes ``... AND -- why this predicate exists`` and the ``--`` swallows the
    rest of that line. It survives only because the fragment's SQL is on a later
    line, and it is one edit away from silently commenting out a filter.

    For a WHOLE QUERY the header lands harmlessly at the top, and an earlier
    version of this function kept it there on the theory that it would show up
    usefully in ``system.query_log``. That was wrong, and measurably so:

    * ``settings.MAX_QUERY_LENGTH`` is 10,000 characters and
      ``clients/clickhouse.py`` rejects anything longer. Inlining the headers took
      the longest rendered cow statement from 9,523 to 9,852 characters — 148 to
      spare. One more chain in ``COW_CHAINS`` would have failed ``trader_activity``
      outright, at all-networks scope only, as a validation error rather than
      anything that points at a comment.
    * the ``query_log`` benefit is speculative; the length budget is not. A header
      is written for whoever opens the file, and stripping it does not take it away
      from them.
    * with nothing inlined, a behaviour-preserving refactor renders byte-identical
      output, so it can be verified by comparing bytes rather than by arguing about
      which differences are "only" comments.

    Only comment-ONLY lines go. A trailing comment after code (``x = 1 -- why``)
    is left alone, because removing it would require knowing whether the ``--`` is
    inside a string literal.
    """
    kept = [line for line in text.split("\n") if not line.lstrip().startswith("--")]
    return "\n".join(kept)


@lru_cache(maxsize=None)
def _read(app: str, name: str) -> str:
    """Raw template text, cached. Comment-only lines are dropped, then exactly one
    trailing newline — the one every editor adds — so a file ending
    ``...ORDER BY x\\n`` renders the same string the f-string did. See
    ``_strip_comment_lines`` for why the comments go."""
    path = QUERIES_DIR / app / f"{name}.sql"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # pragma: no cover - exercised by the registry test
        raise SqlTemplateError(f"no such query template: {app}/{name}.sql") from None
    # Comments first, THEN the trailing-newline strip: dropping the header must not
    # change whether the file ends in a newline.
    text = _strip_comment_lines(text)
    return text[:-1] if text.endswith("\n") else text


@lru_cache(maxsize=None)
def _tokens(app: str, name: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(_read(app, name)))


def load_sql(app: str, name: str, /, **fragments: object) -> str:
    """Render ``queries/<app>/<name>.sql``.

    Every ``@token`` in the file must have a matching keyword, and every
    keyword must appear in the file. Values are stringified; ``None`` is
    rejected rather than rendered as the literal ``"None"``, which would be a
    valid-looking column name.
    """
    template = _read(app, name)
    wanted = _tokens(app, name)
    given = frozenset(fragments)

    missing = wanted - given
    if missing:
        raise SqlTemplateError(
            f"{app}/{name}.sql needs fragments not supplied: {sorted(missing)}"
        )
    extra = given - wanted
    if extra:
        raise SqlTemplateError(
            f"{app}/{name}.sql was given fragments it does not use: {sorted(extra)}"
        )
    for key, value in fragments.items():
        if value is None:
            raise SqlTemplateError(f"{app}/{name}.sql fragment '{key}' is None")

    # Longest name first: without it, `@chain` would substitute inside
    # `@chain_sql` and leave a trailing `_sql` in the query. The regex already
    # matches greedily, but sorting makes the guarantee independent of it.
    def replace(match: re.Match[str]) -> str:
        return str(fragments[match.group(1)])

    return _TOKEN.sub(replace, template)


def available(app: str) -> list[str]:
    """Every template name shipped for an app, sorted.

    Used by ``tests/test_sql_loader.py::test_no_shipped_template_is_orphaned``,
    which asserts every shipped ``.sql`` is loaded by name somewhere. This
    docstring claimed that test existed for a long time before it did — and while
    it did not, ``queries/cow/activity.sql`` sat unreferenced for three commits
    with the Python it was meant to replace still in place. See
    ``orphaned-sql-template-never-wired``.
    """
    directory = QUERIES_DIR / app
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.sql"))


def apps() -> list[str]:
    if not QUERIES_DIR.is_dir():
        return []
    return sorted(p.name for p in QUERIES_DIR.iterdir() if p.is_dir())


def reset_cache_for_tests() -> None:
    _read.cache_clear()
    _tokens.cache_clear()
