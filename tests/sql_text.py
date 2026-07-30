"""One comment-stripper for every textual assertion over rendered SQL.

Shared deliberately. This repo has now been bitten TWICE by the same mistake
class (``sql-guard-counts-comments-as-code``), in two different suites:

* ``test_governance_explorer.py`` asserted ``"FINAL" not in spec.sql`` and failed
  on a comment explaining "the FINAL SELECT"; its CTE-reference counter scored
  four prose mentions of ``months`` as four extra table scans, which is why the
  reference ceiling had been set to 2 instead of 1.
* ``test_cow_explorer.py`` asserted no glued set-operator keyword and failed on a
  comment ending "…already joined with UNION ALL." — the full stop after ``ALL``
  looked like ``UNION ALL`` glued to the next token.

Both are false positives, but the same flaw fails the other way too, and that
direction is worse: a guard asserting a construct is PRESENT can be satisfied by a
comment mentioning it, so documenting a fix passes the test for having made it.

Now that fragments carry their rationale in the ``.sql`` file (see
``sql_loader._strip_comment_lines``), rendered SQL contains prose by design, and
every textual guard has to go through here.
"""

from __future__ import annotations


def sql_code(sql: str) -> str:
    """``sql`` with ``--`` comments removed.

    Only line comments are stripped: this repo's ``.sql`` files use ``--``
    exclusively, so block-comment handling would be untested code in a test
    helper. Callers that also need to be sure no ``--`` sits inside a string
    literal assert the per-line quote count is even.
    """
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def sql_code_lines(sql: str) -> str:
    """Like :func:`sql_code`, but DROPS comment-only lines instead of blanking
    them. Use when the assertion counts lines or cares about adjacency; use
    :func:`sql_code` when it cares about character offsets."""
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
