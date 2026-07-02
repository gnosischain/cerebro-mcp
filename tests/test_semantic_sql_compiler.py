"""Unit tests for the semantic SQL compiler helpers.

Covers:
  * _agg_call — translates dbt MetricFlow agg names to ClickHouse
    function names (notably `count_distinct` → `uniqExact`).
  * _compile_filters — splits filters into WHERE / HAVING clauses,
    accepts the public-API `column`/`operator` keys as well as the
    internal `field`/`op` keys, and raises on unknown field names
    instead of emitting malformed `WHERE = 'val'` SQL.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.semantic.sql_compiler import (
    _AGG_TO_CLICKHOUSE,
    _agg_call,
    _compile_filters,
)


# ─── _agg_call ──────────────────────────────────────────────────────


class TestAggCall:
    def test_count_distinct_translates_to_uniqExact(self):
        # The bug this patch fixes: ClickHouse has no `count_distinct`
        # function — the equivalent is `uniqExact`.
        assert _agg_call("count_distinct", "user_pseudonym") == "uniqExact(user_pseudonym)"

    def test_average_translates_to_avg(self):
        assert _agg_call("average", "x") == "avg(x)"

    def test_avg_alias_passes_through(self):
        assert _agg_call("avg", "x") == "avg(x)"

    @pytest.mark.parametrize(
        "agg,expected_fn",
        [("sum", "sum"), ("min", "min"), ("max", "max"), ("count", "count"), ("median", "median")],
    )
    def test_pass_through_aggs(self, agg, expected_fn):
        assert _agg_call(agg, "value") == f"{expected_fn}(value)"

    def test_unknown_agg_raises_with_supported_list(self):
        with pytest.raises(ValueError) as exc:
            _agg_call("count_distinct_approx", "x")
        message = str(exc.value)
        assert "Unsupported aggregation type: 'count_distinct_approx'" in message
        # Surface the supported set so authors can self-serve.
        assert "count_distinct" in message
        assert "sum" in message

    def test_expr_passed_through_verbatim(self):
        # Compound expressions (CASE, arithmetic, qualified columns) are
        # preserved unchanged in the function-call body.
        assert (
            _agg_call("sum", "CASE WHEN x > 0 THEN x ELSE 0 END")
            == "sum(CASE WHEN x > 0 THEN x ELSE 0 END)"
        )

    def test_agg_to_clickhouse_map_is_sorted_and_canonical(self):
        # Regression guard: every agg name maps to a real ClickHouse fn.
        for agg, fn in _AGG_TO_CLICKHOUSE.items():
            assert isinstance(agg, str) and agg
            assert isinstance(fn, str) and fn


# ─── _compile_filters ───────────────────────────────────────────────


class TestCompileFilters:
    def test_known_dimension_filter_goes_to_where(self):
        # The bug this patch fixes: with the previous code, when `field`
        # didn't match either lookup the fallback emitted `WHERE = 'DEX'`
        # (missing left-hand identifier). Now we either find a match or
        # raise.
        where, having = _compile_filters(
            filters=[{"column": "sector", "operator": "=", "value": "DEX"}],
            branch_dimensions={"sector": "b1_root.label"},
            metric_aliases={},
        )
        assert where == ["b1_root.label = 'DEX'"]
        assert having == []

    def test_known_metric_filter_goes_to_having(self):
        where, having = _compile_filters(
            filters=[{"field": "transaction_count", "op": ">", "value": 1000}],
            branch_dimensions={},
            metric_aliases={"transaction_count": "transaction_count"},
        )
        assert where == []
        assert having == ["transaction_count > 1000"]

    def test_accepts_internal_field_op_keys(self):
        # Backwards compat with code that constructs filters using the
        # planner-internal naming convention.
        where, _ = _compile_filters(
            filters=[{"field": "day", "op": ">=", "value": "2026-01-01"}],
            branch_dimensions={"day": "b1_root.date"},
            metric_aliases={},
        )
        assert where == ["b1_root.date >= '2026-01-01'"]

    def test_accepts_public_column_operator_keys(self):
        # Public MCP tool callers use `column` / `operator` — same filter
        # shape should work without translation in the tool layer.
        where, _ = _compile_filters(
            filters=[{"column": "day", "operator": "<=", "value": "2026-12-31"}],
            branch_dimensions={"day": "b1_root.date"},
            metric_aliases={},
        )
        assert where == ["b1_root.date <= '2026-12-31'"]

    def test_unknown_field_raises_with_valid_fields_listed(self):
        with pytest.raises(ValueError) as exc:
            _compile_filters(
                filters=[{"field": "nonsense", "op": "=", "value": 1}],
                branch_dimensions={"day": "b1_root.date", "sector": "b1_root.label"},
                metric_aliases={"transaction_count": "transaction_count"},
            )
        message = str(exc.value)
        assert "Filter field 'nonsense' is not a dimension or metric" in message
        # Valid fields should be surfaced for debugging.
        assert "day" in message and "sector" in message and "transaction_count" in message

    def test_missing_field_key_raises(self):
        with pytest.raises(ValueError, match="missing 'field' / 'column' key"):
            _compile_filters(
                filters=[{"op": "=", "value": "x"}],
                branch_dimensions={},
                metric_aliases={},
            )

    def test_default_operator_is_equals(self):
        where, _ = _compile_filters(
            filters=[{"column": "sector", "value": "DEX"}],
            branch_dimensions={"sector": "b1_root.label"},
            metric_aliases={},
        )
        assert where == ["b1_root.label = 'DEX'"]

    def test_null_value_literal(self):
        # Pseudo-NULL filter shape (`column IS NULL` analogue via `=`)
        # still produces a valid SQL literal.
        where, _ = _compile_filters(
            filters=[{"column": "day", "operator": "IS", "value": None}],
            branch_dimensions={"day": "b1_root.date"},
            metric_aliases={},
        )
        assert where == ["b1_root.date IS NULL"]

    def test_numeric_value_not_quoted(self):
        _, having = _compile_filters(
            filters=[{"field": "transaction_count", "op": ">=", "value": 5000}],
            branch_dimensions={},
            metric_aliases={"transaction_count": "transaction_count"},
        )
        assert having == ["transaction_count >= 5000"]

    def test_string_value_with_single_quote_escapes(self):
        # SQL injection guard on string values.
        where, _ = _compile_filters(
            filters=[{"column": "sector", "operator": "=", "value": "O'Brien"}],
            branch_dimensions={"sector": "b1_root.label"},
            metric_aliases={},
        )
        assert where == ["b1_root.label = 'O''Brien'"]


# ─── PR 5: time-spine upcast (cross-grain) ───────────────────────────


class TestTimeSpineUpcastCompiler:
    """The compiler must render a synthesised upcast binding as
    `<template>(<root_alias>.<col>) AS <dim>`. Planner-side coverage
    lives in test_semantic_planner.py."""

    def _build_branch_with_upcast(self):
        # Synthesise the binding shape the planner emits for "asking for
        # week on a metric whose root only has a `date` column".
        return {
            "root_model": "api_consensus_validators_active_daily",
            "metrics": ["validators_active"],
            "dimension_bindings": {
                "week": {
                    "name": "week",
                    "provider_model": "api_consensus_validators_active_daily",
                    "local": True,
                    "path": ["api_consensus_validators_active_daily"],
                    "edges": [],
                    "dimension": {
                        "name": "week",
                        "type": "time",
                        "expr": "toMonday(date)",
                        "type_params": {"time_granularity": "week"},
                        "_upcast_template": "toMonday({col})",
                        "_upcast_from_col": "date",
                        "_upcast_source_grain": "day",
                    },
                    "_synthesised": "time_spine_upcast",
                },
            },
        }

    def _build_snapshot(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            models={
                "api_consensus_validators_active_daily": {
                    "name": "api_consensus_validators_active_daily",
                    "relation_name": "`dbt`.`api_consensus_validators_active_daily`",
                    "dimensions": [
                        {"name": "day", "type": "time", "expr": "date",
                         "type_params": {"time_granularity": "day"}}
                    ],
                    "measures": [
                        {"name": "validators_active_value", "agg": "sum", "expr": "cnt"}
                    ],
                },
            },
            metrics={
                "validators_active": {
                    "name": "validators_active",
                    "root_model": "api_consensus_validators_active_daily",
                    "measure": "validators_active_value",
                    "default_filters": [],
                },
            },
        )

    def test_upcast_renders_template_with_root_alias(self):
        from cerebro_mcp.semantic.sql_compiler import _compile_branch_cte
        snapshot = self._build_snapshot()
        branch = self._build_branch_with_upcast()

        sql, _, _, _ = _compile_branch_cte(
            snapshot, branch, branch_index=1, request_filters=[]
        )

        assert "toMonday(b1_root.date) AS week" in sql
        assert "sum(b1_root.cnt) AS validators_active" not in sql  # no force_qualified
        assert "sum(cnt) AS validators_active" in sql
        # The branch must GROUP BY the upcast expression, not the raw col.
        assert "GROUP BY toMonday(b1_root.date)" in sql

    def test_upcast_branch_index_propagates(self):
        # When the planner emits multiple branches, each gets its own
        # alias and the upcast template must use it.
        from cerebro_mcp.semantic.sql_compiler import _compile_branch_cte
        snapshot = self._build_snapshot()
        branch = self._build_branch_with_upcast()

        sql, _, _, _ = _compile_branch_cte(
            snapshot, branch, branch_index=2, request_filters=[]
        )

        assert "toMonday(b2_root.date) AS week" in sql
        assert "b1_root" not in sql


# ─── Cross-root multi-metric: zero-dimension CROSS JOIN ──────────────


def _two_root_snapshot():
    """Two-root snapshot (transactions + validators) mirroring the
    fixture shape in test_semantic_tools.py. The compiler only reads
    `models` and `metrics`."""
    from types import SimpleNamespace
    return SimpleNamespace(
        models={
            "api_execution_transactions_by_sector_daily": {
                "name": "api_execution_transactions_by_sector_daily",
                "relation_name": "dbt.api_execution_transactions_by_sector_daily",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "day"},
                    {"name": "sector", "type": "categorical", "expr": "sector"},
                ],
                "measures": [
                    {"name": "transaction_count_value", "agg": "sum", "expr": "txs"},
                ],
            },
            "api_consensus_validators_active_daily": {
                "name": "api_consensus_validators_active_daily",
                "relation_name": "dbt.api_consensus_validators_active_daily",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "day"},
                ],
                "measures": [
                    {"name": "validators_active_value", "agg": "sum", "expr": "cnt"},
                ],
            },
        },
        metrics={
            "transaction_count": {
                "name": "transaction_count",
                "root_model": "api_execution_transactions_by_sector_daily",
                "measure": "transaction_count_value",
                "default_filters": [],
            },
            "validators_active": {
                "name": "validators_active",
                "root_model": "api_consensus_validators_active_daily",
                "measure": "validators_active_value",
                "default_filters": [],
            },
        },
    )


def _local_day_binding(provider_model: str) -> dict:
    return {
        "name": "day",
        "provider_model": provider_model,
        "local": True,
        "path": [provider_model],
        "edges": [],
        "dimension": {"name": "day", "type": "time", "expr": "day"},
    }


def _two_root_plan(dimensions: list[str]) -> dict:
    branches = []
    for root_model, metric_name in [
        ("api_execution_transactions_by_sector_daily", "transaction_count"),
        ("api_consensus_validators_active_daily", "validators_active"),
    ]:
        branches.append(
            {
                "root_model": root_model,
                "metrics": [metric_name],
                "dimension_bindings": {
                    dim: _local_day_binding(root_model) for dim in dimensions
                },
            }
        )
    return {
        "requested_metrics": ["transaction_count", "validators_active"],
        "resolved_metrics": ["transaction_count", "validators_active"],
        "requested_dimensions": dimensions,
        "resolved_dimensions": dimensions,
        "planner_mode": "multi_branch_aggregate_join",
        "branches": branches,
        "filters": [],
        "limit": 100,
    }


class TestZeroDimMultiRootCrossJoin:
    """Multi-root plans with NO shared dimensions must CROSS JOIN the
    single-row branch CTEs. The previous scaffolding
    (`keys AS (SELECT 1 AS join_key ...)` + `LEFT JOIN branch_N ON
    keys.join_key = 1`) is rejected by ClickHouse with Code 403
    INVALID_JOIN_ON_EXPRESSION because the ON clause references only
    one side of the join."""

    def test_zero_dim_plan_compiles_to_cross_join(self):
        from cerebro_mcp.semantic.sql_compiler import compile_metric_plan
        snapshot = _two_root_snapshot()
        plan = _two_root_plan(dimensions=[])

        sql, _warnings = compile_metric_plan(snapshot, plan)

        assert "CROSS JOIN branch_2" in sql
        assert "ON keys.join_key = 1" not in sql
        assert "keys AS (" not in sql
        assert "LEFT JOIN" not in sql
        assert "FROM branch_1" in sql
        # Both metrics survive into the outer SELECT under their aliases.
        assert "branch_1.transaction_count AS transaction_count" in sql
        assert "branch_2.validators_active AS validators_active" in sql

    def test_zero_dim_branches_have_no_group_by(self):
        # Correctness precondition for CROSS JOIN: each branch CTE
        # aggregates without GROUP BY, so it returns exactly one row.
        from cerebro_mcp.semantic.sql_compiler import compile_metric_plan
        snapshot = _two_root_snapshot()
        plan = _two_root_plan(dimensions=[])

        sql, _warnings = compile_metric_plan(snapshot, plan)

        assert "GROUP BY" not in sql
        assert "sum(txs) AS transaction_count" in sql
        assert "sum(cnt) AS validators_active" in sql

    def test_dimensioned_multi_root_plan_keeps_keys_left_join(self):
        # Regression guard: the shared-dimension multi-branch path is
        # untouched — keys CTE + LEFT JOIN per branch, no CROSS JOIN.
        from cerebro_mcp.semantic.sql_compiler import compile_metric_plan
        snapshot = _two_root_snapshot()
        plan = _two_root_plan(dimensions=["day"])

        sql, _warnings = compile_metric_plan(snapshot, plan)

        assert "keys AS (" in sql
        assert "LEFT JOIN branch_1 ON keys.day = branch_1.day" in sql
        assert "LEFT JOIN branch_2 ON keys.day = branch_2.day" in sql
        assert "CROSS JOIN" not in sql
        assert "keys.day AS day" in sql


# ─── relationship join_semantics ─────────────────────────────────────


class TestJoinSemantics:
    """`_compile_join_chain` must honor the relationship's authored
    `join_semantics`: 'inner' compiles to INNER JOIN (approved scoping
    joins that intentionally drop unmatched left rows); anything else
    keeps the historical LEFT JOIN, with `allow_any_join` upgrading
    only the LEFT variant to ANY LEFT JOIN."""

    def _snapshot(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            models={
                "root_model": {
                    "name": "root_model",
                    "relation_name": "dbt.root_model",
                    "dimensions": [],
                    "measures": [],
                },
                "dim_provider": {
                    "name": "dim_provider",
                    "relation_name": "dbt.dim_provider",
                    "dimensions": [
                        {"name": "sector", "type": "categorical", "expr": "sector"}
                    ],
                    "measures": [],
                },
            },
        )

    def _binding(self, relationship: dict) -> dict:
        return {
            "name": "sector",
            "provider_model": "dim_provider",
            "local": False,
            "path": ["root_model", "dim_provider"],
            "edges": [
                {
                    "relationship": relationship,
                    "name": relationship.get("name", ""),
                    "source": "root_model",
                    "target": "dim_provider",
                    "left_keys": ["k"],
                    "right_keys": ["k"],
                    "cardinality": "many_to_one",
                }
            ],
            "dimension": {"name": "sector", "type": "categorical", "expr": "sector"},
        }

    def _compile(self, relationship: dict) -> tuple[list[str], list[str]]:
        from cerebro_mcp.semantic.sql_compiler import _compile_join_chain
        warnings: list[str] = []
        join_sql, _expr = _compile_join_chain(
            self._snapshot(), self._binding(relationship), "b1_root", warnings
        )
        return join_sql, warnings

    def test_inner_semantics_emits_inner_join(self):
        join_sql, warnings = self._compile(
            {"name": "root_to_provider", "join_semantics": "inner"}
        )
        assert join_sql == [
            "INNER JOIN dbt.dim_provider AS b1_root_j1 ON b1_root.k = b1_root_j1.k"
        ]
        assert warnings == []

    def test_left_semantics_keeps_left_join(self):
        join_sql, warnings = self._compile(
            {"name": "root_to_provider", "join_semantics": "left"}
        )
        assert join_sql == [
            "LEFT JOIN dbt.dim_provider AS b1_root_j1 ON b1_root.k = b1_root_j1.k"
        ]
        assert warnings == []

    def test_missing_semantics_defaults_to_left_join(self):
        join_sql, _ = self._compile({"name": "root_to_provider"})
        assert join_sql[0].startswith("LEFT JOIN ")

    def test_allow_any_join_still_upgrades_left_to_any_left(self):
        join_sql, warnings = self._compile(
            {
                "name": "root_to_provider",
                "join_semantics": "left",
                "allow_any_join": True,
            }
        )
        assert join_sql[0].startswith("ANY LEFT JOIN ")
        assert any("ANY LEFT JOIN" in warning for warning in warnings)

    def test_inner_semantics_takes_precedence_over_allow_any_join(self):
        join_sql, warnings = self._compile(
            {
                "name": "root_to_provider",
                "join_semantics": "inner",
                "allow_any_join": True,
            }
        )
        assert join_sql[0].startswith("INNER JOIN ")
        assert warnings == []


# ─── Ratio / derived metrics (same-root post-aggregation MVP) ─────────


def _derived_snapshot():
    """Same-root two-measure snapshot for ratio/derived compilation."""
    from types import SimpleNamespace
    return SimpleNamespace(
        registry_hash="derived-compile-test",
        graph={},
        synonym_index={},
        dimension_index={},
        models={
            "api_gpay_tx_daily": {
                "name": "api_gpay_tx_daily",
                "relation_name": "dbt.api_gpay_tx_daily",
                "semantic_status": "approved",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "day",
                     "type_params": {"time_granularity": "day"}},
                ],
                "measures": [
                    {"name": "tx_success_value", "agg": "sum", "expr": "success_cnt"},
                    {"name": "tx_total_value", "agg": "sum", "expr": "total_cnt"},
                ],
            },
        },
        metrics={
            "tx_success": {
                "name": "tx_success",
                "type": "simple",
                "root_model": "api_gpay_tx_daily",
                "measure": "tx_success_value",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "tx_total": {
                "name": "tx_total",
                "type": "simple",
                "root_model": "api_gpay_tx_daily",
                "measure": "tx_total_value",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "tx_success_rate": {
                "name": "tx_success_rate",
                "type": "ratio",
                "type_params": {"numerator": "tx_success", "denominator": "tx_total"},
                "root_model": "api_gpay_tx_daily",
                "measure": "",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "tx_failed": {
                "name": "tx_failed",
                "type": "derived",
                "type_params": {
                    "expr": "tx_total - tx_success",
                    "metrics": ["tx_total", "tx_success"],
                },
                "root_model": "api_gpay_tx_daily",
                "measure": "",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
        },
    )


class TestRenderDerivedSelect:
    def test_ratio_uses_nullif_denominator(self):
        from cerebro_mcp.semantic.sql_compiler import _render_derived_select
        spec = {"name": "rate", "kind": "ratio", "inputs": ["num_m", "den_m"], "expr": ""}
        assert _render_derived_select(spec, "branch_1") == (
            "branch_1.num_m / nullIf(branch_1.den_m, 0) AS rate"
        )

    def test_derived_expr_resolves_inputs_to_branch_columns(self):
        from cerebro_mcp.semantic.sql_compiler import _render_derived_select
        spec = {
            "name": "net",
            "kind": "derived",
            "inputs": ["a_metric", "b_metric"],
            "expr": "(a_metric - b_metric) / 2",
        }
        assert _render_derived_select(spec, "branch_2") == (
            "((branch_2.a_metric - branch_2.b_metric) / 2) AS net"
        )

    def test_derived_expr_word_boundaries_protect_similar_names(self):
        from cerebro_mcp.semantic.sql_compiler import _render_derived_select
        # `tx` must not be rewritten inside `tx_total`.
        spec = {
            "name": "x",
            "kind": "derived",
            "inputs": ["tx", "tx_total"],
            "expr": "tx / tx_total",
        }
        assert _render_derived_select(spec, "branch_1") == (
            "(branch_1.tx / branch_1.tx_total) AS x"
        )

    def test_derived_expr_rejects_forbidden_characters(self):
        from cerebro_mcp.semantic.sql_compiler import _render_derived_select
        spec = {
            "name": "evil",
            "kind": "derived",
            "inputs": ["a"],
            "expr": "a; DROP TABLE users",
        }
        with pytest.raises(ValueError, match="unsupported expr"):
            _render_derived_select(spec, "branch_1")

    def test_derived_expr_rejects_empty(self):
        from cerebro_mcp.semantic.sql_compiler import _render_derived_select
        spec = {"name": "empty", "kind": "derived", "inputs": ["a"], "expr": ""}
        with pytest.raises(ValueError, match="unsupported expr"):
            _render_derived_select(spec, "branch_1")

    def test_ratio_requires_two_inputs(self):
        from cerebro_mcp.semantic.sql_compiler import _render_derived_select
        spec = {"name": "rate", "kind": "ratio", "inputs": ["only_one"], "expr": ""}
        with pytest.raises(ValueError, match="exactly two inputs"):
            _render_derived_select(spec, "branch_1")


class TestDerivedMetricCompilation:
    """End-to-end: plan_metric_query -> compile_metric_plan for a ratio /
    derived metric over two same-root measures."""

    def _compile(self, requested_metrics, dimensions=("day",)):
        from cerebro_mcp.semantic.planner import plan_metric_query
        from cerebro_mcp.semantic.sql_compiler import compile_metric_plan
        snapshot = _derived_snapshot()
        plan = plan_metric_query(
            snapshot,
            requested_metrics=list(requested_metrics),
            requested_dimensions=list(dimensions),
        )
        sql, warnings = compile_metric_plan(snapshot, plan)
        return sql, warnings

    def test_ratio_sql_shape(self):
        sql, _warnings = self._compile(["tx_success_rate"])
        # CTE path forced (no inline single-branch shortcut).
        assert sql.startswith("WITH\nbranch_1 AS (")
        # Inputs aggregate inside the branch CTE...
        assert "sum(success_cnt) AS tx_success" in sql
        assert "sum(total_cnt) AS tx_total" in sql
        # ...and are ALSO selected in the outer query alongside the ratio.
        assert "FROM branch_1" in sql
        assert "tx_success,\n  tx_total" in sql
        assert (
            "branch_1.tx_success / nullIf(branch_1.tx_total, 0) AS tx_success_rate"
            in sql
        )

    def test_derived_expr_sql_shape(self):
        sql, _warnings = self._compile(["tx_failed"])
        assert "(branch_1.tx_total - branch_1.tx_success) AS tx_failed" in sql

    def test_ratio_with_plain_metric_keeps_single_branch(self):
        sql, _warnings = self._compile(["tx_success", "tx_success_rate"])
        assert "branch_2" not in sql
        assert sql.count("sum(success_cnt) AS tx_success") == 1
        assert (
            "branch_1.tx_success / nullIf(branch_1.tx_total, 0) AS tx_success_rate"
            in sql
        )

    def test_zero_dimension_ratio_compiles(self):
        sql, _warnings = self._compile(["tx_success_rate"], dimensions=())
        assert "GROUP BY" not in sql
        assert (
            "branch_1.tx_success / nullIf(branch_1.tx_total, 0) AS tx_success_rate"
            in sql
        )
