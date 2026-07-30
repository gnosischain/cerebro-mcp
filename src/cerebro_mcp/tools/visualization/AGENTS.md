# Mini-app backends — scoped guide

`QuerySpec` builders, dataset delivery and the MCP-UI plumbing behind the mini-apps.
Run `get_cerebro_change_context(paths="src/cerebro_mcp/tools/visualization")` for the
live hazard list.

## No SQL in this file — a spec builder composes, it does not write SQL

Every query and every reusable fragment lives in `queries/<app>/*.sql` and is
rendered with `sql_loader.load_sql`. A builder here supplies **parameters and named
fragments**; it must not contain a statement or a clause block. Fragment files carry
a kind prefix — `_cte_`, `_pred_`, `_join_`, `_anchor_`, `_expr_`. Full rationale in
[`queries/AGENTS.md`](queries/AGENTS.md) Rule 0; enforced by
`tests/test_sql_lives_in_files.py`.

`governance_explorer.py` and `cow_explorer.py` are both clean and test-pinned as
such. `metric_lab.py` (a runtime query compiler) and `mini_apps.py` (the generic
`count() OVER ()` result envelope) are exempt, with reasons recorded in the test.

`cow_explorer.py` was the debt this rule was written against: 32 SQL-bearing
literals — anchor sub-selects, three copies of a UNION-arm envelope, two search
probes, and a projection list that was post-processed with
`.replace("SELECT ", "", 1)`. All 32 are now in 18 `.sql` files. The one literal
left is a single named `UNION_ALL_ARM` separator; the test's `SEPARATOR_ONLY`
carve-out admits whitespace plus `UNION ALL` and nothing else.

The concrete cost of the Python-side version: the treasury month-end restriction sat
here as two concatenated string literals, so the reason it joined on **both**
`chain_id` and `snapshot_date` — chains publish independently and are months apart,
so matching the date alone sums two chains' different dates into one bucket — was
recorded nowhere. It is now `queries/governance/_join_treasury_months.sql`, with
that paragraph in it.

## Dataset contract

- **A dataset that fails must render a visible stub**, never vanish. A missing panel
  reads as "there is no data", which converts a load failure into an apparent
  finding. Three sites state this rule and none test it.
- **A deliberate exclusion must be COUNTED**, and the counts must partition every
  omitted row. Getting this half-right is easy: the first `gip_pipeline` attempt left
  one class in neither bucket — excluded and undisclosed, the exact failure the
  counts exist to prevent.
- **An empty region needs words.** An empty box with no explanation reads as "still
  loading" or "broken".
- **Any spec feeding a paged table needs a deterministic `ORDER BY`.**

## Column order is a contract

Where a consumer reads rows positionally (`r[5]`), reordering the SELECT list
re-labels every field downstream with no error. Prefer `rowsToObjects(dataset)` on
the UI side, which zips columns to names and removes the contract.

## The dev fixture must mirror the SQL

`devFixture.ts` rows that the query can never return produce a dev loop that
validates nothing — and worse, makes a wrong UI look correct. When you change a
spec's filter, change the fixture with it.

## Before you finish

`.venv/bin/python -m pytest tests/test_visualization.py tests/test_governance_explorer.py -q`
