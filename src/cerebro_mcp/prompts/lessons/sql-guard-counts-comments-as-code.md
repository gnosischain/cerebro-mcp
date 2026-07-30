---
id: sql-guard-counts-comments-as-code
title: A guard that greps rendered SQL reads the comment block as code, in both directions
status: enforced
layer: sql
scope: >-
  every test that asserts on the TEXT of a rendered query — the QuerySpec guards in
  tests/test_governance_explorer.py and tests/test_cow_explorer.py, and any new
  assertion over sql_loader output
symptom: >-
  a textual SQL guard fails on a query that is correct, or passes on one that is
  not, and the difference is a comment
last_verified: 2026-07-30
evidence:
  - 'tests/test_governance_explorer.py::test_treasury_specs_always_pin_the_job_and_never_use_final failed on the prose "and in the FINAL SELECT" inside treasury_token_history.sql'
  - 'the CTE-reference counter scored `months` at 6 uses when the code referenced it once — the other five were in the cost-history comment'
  - 'comments stripped, all 7 treasury specs reference every CTE exactly once, so the uses <= 2 ceiling had never been a considered allowance'
  - tests/test_governance_explorer.py::test_sql_code_strips_comments_and_keeps_code
  - 'SECOND INSTANCE, different suite: test_cow_explorer.py::test_generated_spec_sql_never_glues_tokens_to_set_operators went red on a comment ending "...already joined with UNION ALL." — the full stop after ALL matched its glued-keyword regex'
  - 'both suites now route every textual assertion through tests/sql_text.py::sql_code'
---
## Symptom

A guard over rendered SQL text disagrees with the query. Two shapes:

- **False positive.** `assert "FINAL" not in spec.sql.upper()` failed because the
  file's cost-history note explained that a CTE was referenced "in the final
  SELECT". The query contains no `FINAL`.
- **False negative — the dangerous one.** A guard of the form
  `assert "<construct>" in sql` is satisfied by a comment *mentioning* the
  construct. Documenting a fix passes the test for having made it.

## Root cause

`spec.sql` is the whole rendered template, comments included, and these guards are
substring or regex matches. A `.sql` file in this repo carries a large explanatory
header by design — that is where the reasoning for a query's shape lives — so the
prose is often longer than the code and names every identifier in it.

The CTE-reference counter shows how far this goes: it counted `\bmonths\b`
occurrences and subtracted one for the definition, so five prose mentions read as
five extra table scans. That inflation is also why the threshold sat at `<= 2` —
the number was chosen against inflated counts, so it looked like real specs needed
the slack. They did not.

## Forbidden action

Asserting on `spec.sql` (or any `sql_loader.load_sql` output) directly. Counting
identifier occurrences in text that includes comments.

## Detection

Strip line comments first, then assert:

```python
def sql_code(sql: str) -> str:
    return "\n".join(line.split("--")[0] for line in sql.splitlines())
```

Check the splitter's own assumption too — a `--` inside a string literal would be
mangled. `tests/test_governance_explorer.py` asserts every line's quote count is
even, so the simple form stays correct.

## Safe remediation

Route every textual assertion through one shared `sql_code()` helper, and test the
helper — an assertion helper that quietly stops working takes down every guard
built on it. Then re-derive any threshold that was set against uncomparable
counts; the honest number is usually stricter than the one in the file.

## Why this keeps happening

Rendered SQL now contains prose BY DESIGN: a fragment carries its rationale in the
file (`sql_loader._strip_comment_lines` keeps it out of the substituted text, but a
whole-query header stays at the top of the statement). So the surface area for this
mistake grows every time the SQL-in-files rule is applied to another plane. Two
suites have hit it, on two different regexes, three weeks apart in repo time.

## Enforcement

`tests/test_governance_explorer.py::test_sql_code_strips_comments_and_keeps_code`
pins the helper, and the FINAL / job-pin / CTE-count guards all consume it. The
CTE ceiling is now `<= 1`, with a `seen >= 10` assertion so the sweep cannot pass
vacuously if the `WITH` spelling drifts.

The helper now lives in `tests/sql_text.py` and BOTH suites import it — the
governance guards and `test_cow_explorer.py`'s glued-set-operator sweep. One
implementation, so a fix reaches every caller. Remaining exposure: any NEW textual
assertion written against `spec.sql` directly. There is no test that forces a guard
to use the stripper, because a test cannot tell an assertion about SQL from one about
prose.
