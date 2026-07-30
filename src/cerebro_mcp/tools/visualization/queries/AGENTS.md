# SQL query planes — scoped guide

Hand-written `.sql` loaded by `sql_loader.load_sql(app, name, **fragments)`.
Run `get_cerebro_change_context(paths="src/cerebro_mcp/tools/visualization/queries")`
for the live hazard list; this guide is the prose companion.

## Rule 0 — SQL lives in a `.sql` file, never in Python

**Every query and every reusable query fragment is its own file here.** Python
composes named fragments and passes parameters; it does not contain SQL. That means
no query in an f-string, no `"SELECT ..."` string literal, no clause assembled by
concatenation in a spec builder.

A fragment is a file too. Name it with a leading `_` and a kind prefix so it sorts
apart from whole queries: `_cte_*` (a CTE block), `_pred_*` (a predicate),
`_join_*` (a join clause), `_anchor_*` (a scalar sub-select), `_expr_*` (a scalar
expression or projection list).

**Write the rationale in the file — none of it is sent to ClickHouse.**
`sql_loader` drops every comment-only line from every template before substitution.
That is what makes this rule workable: the header is for whoever opens the file, and
stripping it does not take it away from them.

An earlier version stripped fragments only and kept whole-query headers, on the
theory that they would show up usefully in `system.query_log`. Measured, that was a
bad trade. `settings.MAX_QUERY_LENGTH` is 10,000 and `clients/clickhouse.py` rejects
anything longer; inlining the headers took the longest cow statement from 9,523 to
9,852 characters. One more chain in `COW_CHAINS` would have failed `trader_activity`
at all-networks scope only, as a validation error pointing at nothing. Stripping
universally recovered the budget — the longest is now **9,114** — and made rendered
output byte-identical to the Python literals it replaced, so a behaviour-preserving
refactor is verifiable by comparing bytes instead of arguing about which differences
are "only" comments. `test_no_rendered_template_exceeds_the_query_length_cap` guards
the cap.

One consequence: **never write a fragment name with a leading `@` in prose.**
`_tokens()` reads the stripped text, so a name that appears only in a comment is
invisible to the loader while the caller still passes it — and `load_sql` raises
"was given fragments it does not use". `pair_depth_heatmap.sql` documented its floor
as "Floored at @min_step_label" and that raised on every depth-heatmap load the
moment stripping became universal. Name the Python constant instead;
`test_no_template_names_a_fragment_only_in_a_comment` enforces it.

Why this is a rule and not a preference:

- **A `.sql` file pastes into a client.** Reproducing a production failure means
  filling in a handful of `@tokens`. Reproducing an f-string means importing the
  module and calling the spec builder to see what actually ran.
- **`{name:Type}` stops fighting `{}`.** In an f-string every bound parameter has to
  be doubled to `{{name:Type}}`, and a forgotten pair is a runtime error that waits
  for that code path.
- **A clause in Python has no home for its rationale.** The month-end join in the
  treasury specs sat in Python as two concatenated strings for three revisions; the
  reason it joined on *both* columns was written down nowhere, and the query it fed
  went on to time out in production.

Enforced by `tests/test_sql_lives_in_files.py`. The one carve-out is the generic
result envelope in `mini_apps.py` (`count() OVER ()` / `ROW_CAP`), which is the
executor applied to every spec rather than a query about anything — it is named
explicitly in that test.

## Two substitution mechanisms, do not confuse them

- `@name` — a **Python-side fragment** substituted by `sql_loader` before the query
  leaves the process. Use for composing predicates and table names.
- `{name:Type}` — a **ClickHouse bound parameter**. Leave it alone; the driver binds
  it. No brace doubling.

An unused fragment or an unsubstituted `@token` **raises** — deliberately, because
the alternative is a filter that silently stops applying
(`tests/test_sql_loader.py::test_an_unused_fragment_raises_rather_than_silently_dropping_a_filter`).

## `sql_loader` is `lru_cache`d

Editing a `.sql` requires a **server restart**. A rebuilt UI bundle does not help.
This is the single most common "my fix did nothing" in this layer.

## ClickHouse rules that bite here

- **`FINAL`**: three branches — see the `ch-final-three-way-rule` record. And the
  alias precedes it: `FROM t AS x FINAL`, never `FROM t FINAL AS x`.
- **A CTE is inlined per reference.** Two references = two scans. A 4-reference CTE
  has already exhausted the 2 GiB cap here.
- **Never alias an expression to the name of a real column** in the same query — the
  `WHERE` binds to the alias and the query returns nothing, with no error.
- **An aggregate alias beside a same-level `WHERE`** raises code 184. Put the column
  in the `GROUP BY` instead (which usually bounds the scan too).
- **`LIMIT` needs a total `ORDER BY`.** Otherwise repeat calls return different rows.
- **Never index an upstream-versioned array by position.** Resolve the slot by name
  from the schema that same row was produced under, matching names as substrings.
- Emit `NULL`, never `0`, where nothing was measured.

## Before you finish

`.venv/bin/python -m pytest tests/test_sql_loader.py tests/test_governance_explorer.py tests/test_cow_explorer.py -q`
