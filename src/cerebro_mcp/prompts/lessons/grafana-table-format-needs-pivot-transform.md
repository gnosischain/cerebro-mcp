---
id: grafana-table-format-needs-pivot-transform
title: >-
  Table-format Grafana targets are never pivoted into series by the panel —
  long-format results render broken while every SQL gate reports OK
status: observed
layer: mcp-tool
scope: >-
  src/cerebro_mcp/grafana/ (models, styles, compiler), the publish tools in
  src/cerebro_mcp/tools/visualization/grafana.py, and every GrafanaDashboardDef
  an analyst session authors
symptom: >-
  Multi-series Grafana panels (stacked bars, multi-line trends) render as one
  garbled series or plot the label column as nonsense, and a categorical
  heatmap panel crashes with "TypeError: Cannot read properties of undefined
  (reading 'config')" — while validate_grafana_dashboard and
  verify_grafana_dashboard report every panel OK with healthy row counts
last_verified: 2026-08-17
evidence:
  - "src/cerebro_mcp/grafana/compiler.py: _FORMAT_TABLE — every target ships as the plugin's table format regardless of viz"
  - >-
    verified 2026-08-17: gpay_overview_daily / gpay_gnosis_deep_dive_daily /
    gpay_celo_minipay_daily published at v1 with all 67 panels live-verified
    (non-zero row counts); the user reported every labeled plot completely
    broken. v2 with per-spec partitionByValues fixed all 18 multi-series
    trends; v2's groupingToMatrix into NATIVE heatmap panels then crashed with
    the reading-'config' TypeError; v3 rendering the retention grids as tables
    works.
  - "fix in tree, pending deploy: compiler.auto_transformations + _renders_as_grid_table, styles.AUTO_TRANSFORM_COLUMNS, models.py canonical-alias parse gate"
  - "tests/test_grafana_compiler.py::test_auto_pivot_added_for_time_series_multi, ::test_distribution_2d_compiles_to_color_grid_table, ::test_user_transformations_win_over_auto_pivot, ::test_time_series_multi_without_label_alias_rejected"
---
## Symptom

Dashboards publish cleanly — local SQL lint passes, the live `/api/ds/query`
check returns healthy row counts for every panel — but in the browser every
multi-series panel is garbage: a stacked "by token" bar chart draws one
zigzag series, series names are missing or wrong, and a cohort-retention
heatmap throws `TypeError: Cannot read properties of undefined (reading
'config')` and renders nothing.

## Root cause

The ClickHouse datasource plugin rejects the `time_series` target format, so
the compiler ships EVERY target as table format (`_FORMAT_TABLE = 1`). In
table format the panel does not pivot long-format rows into series: a
`(time, label, value)` result is three columns, not N series, unless a
Grafana TRANSFORMATION (`partitionByValues` / `groupingToMatrix`) splits it.
The compiler set stacking options for `time_series_multi` but never emitted
the transformation — stacking one garbled series changes nothing. Separately,
the native heatmap panel only understands time x numeric buckets; feeding it
a categorical x/y grid (even a well-formed matrix frame) crashes its field
lookup. Both failures live in the RENDER layer, which no SQL-level gate
observes: row counts are healthy precisely because the query is fine.

## Forbidden action

Do not ship a long-format (`time_series_multi`, `category_value_multi`,
`distribution_2d`) panel without its pivot transformation, and do not emit a
native heatmap panel for a categorical x/y grid. Do not treat green
`validate_grafana_dashboard` / `verify_grafana_dashboard` output as evidence
that a panel RENDERS — they prove the SQL runs and returns rows, nothing
about what the panel does with the frame.

## Detection

Render-layer breakage is invisible to the SQL gates by construction; the only
runtime detection is a human looking at the dashboard. Statically: any panel
spec whose data_shape is long-format and whose compiled JSON carries an empty
`transformations` list is broken by default. The parse-time alias gate (see
Enforcement) turns the silent version — SQL whose series column is not named
what the pivot looks up — into an immediate, actionable error.

## Safe remediation

For `time_series_multi` (timeseries_*): SQL returns `(time, label, value)`
and the panel carries `{"id": "partitionByValues", "options": {"fields":
["label"], "keepFields": false, "naming": {"asLabels": true}}}`. For
`category_value_multi` (barchart_*): `(category, series, value)` plus
`groupingToMatrix` (columnField=series, rowField=category, valueField=value)
— and the category column must be a String (`leftPad(toString(toHour(t)), 2,
'0')` for hours), numeric categories are rejected by the barchart panel. For
`distribution_2d`: never a native heatmap — render a table fed by
`groupingToMatrix` (rowField=y, columnField=x) with color-background cells.
The in-tree compiler does all of this automatically when the spec supplies no
transformations of its own.

## Enforcement

In tree, pending deploy (status stays `observed` until merged):
`compile_grafana_dashboard` auto-appends the pivot for the (viz, shape) pairs
in `styles.AUTO_TRANSFORM_COLUMNS` and compiles heatmap+distribution_2d as a
color-graded table grid; `models.GrafanaPanelDef` rejects long-format SQL at
parse time when it lacks the canonical aliases the pivot references (`label`
/ `category`,`series`,`value` / `x`,`y`,`value`) and no explicit
transformations are supplied. Pinned by
tests/test_grafana_compiler.py (auto-pivot content, grid-table compilation,
user-transformations-win, and both rejection directions).
