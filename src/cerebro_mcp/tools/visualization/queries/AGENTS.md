# SQL query planes — scoped guide

Hand-written `.sql` loaded by `sql_loader.load_sql(app, name, **fragments)`.
Run `get_cerebro_change_context(paths="src/cerebro_mcp/tools/visualization/queries")`
for the live hazard list; this guide is the prose companion.

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
