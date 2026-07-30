---
id: orphaned-sql-template-never-wired
title: An extraction can write the .sql file and never repoint the call site, leaving both
status: enforced
layer: sql
scope: >-
  every .sql under tools/visualization/queries/ and the spec builders that load
  them — cow_explorer.py, governance_explorer.py, metric_lab.py
symptom: >-
  editing a .sql file changes nothing at runtime, and the query that actually runs
  is still the one built in Python
last_verified: 2026-07-30
evidence:
  - 'queries/cow/activity.sql was added in 77927cf ("sql isolated + miniapps update") and was still unreferenced three commits later, while three call sites kept assembling the same envelope from Python literals'
  - 'found only by writing tests/test_sql_loader.py::test_no_shipped_template_is_orphaned — grepping tests/ for `orphan`, `available(` and `glob("*.sql")` previously found nothing'
  - 'sql_loader.available() has claimed in its docstring since it was written that it is "used by the registry test that asserts no .sql file is orphaned"; no such test existed'
  - 'the same sweep also caught kpi_select.sql, orphaned during this session by moving its content into _expr_kpi_body.sql'
---
## Symptom

You edit a `.sql` file, restart the server, and nothing changes — because the
statement that actually runs is still built in Python. Or the reverse: you fix a
query in Python and a reviewer points at a `.sql` file with the old shape, and
neither of you can say which one is live.

## Root cause

Moving SQL out of Python is a **two-part** change: write the file, and repoint the
caller. Only the first part shows up in a diff as something new, and the second is
easy to defer and forget. Nothing fails, because an orphaned file is simply never
read.

`queries/cow/activity.sql` is the worked example. It was created as part of an "sql
isolated" commit and holds exactly the UNION-arm envelope that `_overview_specs`,
`_trade_specs` and `_traders_specs` needed. All three carried on building that
envelope as `f"WITH {shared_ctes}\n" + "SELECT * FROM (\n" + …`. The file and the
literals coexisted for three commits.

Why nobody noticed compounds it: `sql_loader.available()` carries a docstring saying
it is "used by the registry test that asserts no `.sql` file is orphaned". That test
did not exist. A comment claiming a guard exists is worse than no comment, because it
stops the next person looking for one.

## Forbidden action

Adding a `.sql` file without, in the same change, a `load_sql` call that names it.
Believing a docstring that says a guard exists — grep for the test.

## Detection

```
.venv/bin/python -m pytest tests/test_sql_loader.py::test_no_shipped_template_is_orphaned
```

By hand: for each `sql_loader.available(app)` name, grep the Python tree for that
name as a string literal.

The complementary direction was already covered — `load_sql` raises
`SqlTemplateError` for a missing file — so the two together stop the file set and the
call sites drifting apart either way.

## Safe remediation

Wire it or delete it. Before deleting, confirm the content survives somewhere:
`kpi_select.sql` was removed only after asserting its text equalled
`_expr_kpi_body.sql`'s minus the leading `SELECT ` it existed to have stripped.

When you find an orphan that does what you were about to write, **adopt it** — keep
its name and its token names and repoint the callers. That resolves the duplication
instead of adding a third copy, and it keeps the shape the earlier author already got
right.

## Enforcement

`tests/test_sql_loader.py::test_no_shipped_template_is_orphaned` matches every
shipped template name against string literals in the source tree, with a documented
`DYNAMIC` escape for names computed at runtime (empty today). Its false-positive mode
is loud, and the fix is to add the call site to `DYNAMIC` rather than weaken the
assertion.
