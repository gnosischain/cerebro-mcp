# Mini-app backends — scoped guide

`QuerySpec` builders, dataset delivery and the MCP-UI plumbing behind the mini-apps.
Run `get_cerebro_change_context(paths="src/cerebro_mcp/tools/visualization")` for the
live hazard list.

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
