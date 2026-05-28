"""Unit tests for cerebro_mcp.tools.analytics.sql_heuristics."""

from __future__ import annotations

import pytest

from cerebro_mcp.tools.analytics.sql_heuristics import (
    DEFAULT_STOCK_COLUMNS,
    aggregates_stock_measure_over_time,
    aggregator_volume_double_count_risk,
    evaluate_all,
    pearson_correlation_on_time_series,
    uses_dimension_without_residual,
)


# ---------------------------------------------------------------------------
# Stock-vs-flow heuristic
# ---------------------------------------------------------------------------

class TestStockFlow:
    def test_naive_sum_tvl_flagged(self):
        sql = """
        SELECT SUM(tvl_usd) AS total_tvl
        FROM dbt.fct_execution_pools_daily
        WHERE date >= '2025-01-01'
        """
        bad, msg = aggregates_stock_measure_over_time(sql)
        assert bad
        assert "tvl_usd" in msg

    def test_sum_tvl_with_group_by_date_passes(self):
        sql = """
        SELECT date, sum(tvl_usd) AS tvl
        FROM dbt.fct_execution_pools_daily
        GROUP BY date
        ORDER BY date
        """
        bad, _ = aggregates_stock_measure_over_time(sql)
        assert not bad

    def test_sum_tvl_with_single_date_filter_passes(self):
        sql = """
        SELECT sum(tvl_usd) AS tvl
        FROM dbt.fct_execution_pools_daily
        WHERE date = (SELECT max(date) FROM dbt.fct_execution_pools_daily)
        """
        bad, _ = aggregates_stock_measure_over_time(sql)
        assert not bad

    def test_argmax_pattern_passes(self):
        sql = """
        SELECT date, sum(argMax(tvl_usd, date)) AS tvl
        FROM dbt.fct_execution_pools_daily
        GROUP BY date
        """
        bad, _ = aggregates_stock_measure_over_time(sql)
        assert not bad

    def test_other_stock_columns_flagged(self):
        sql = "SELECT SUM(balance_usd) FROM x WHERE date >= '2025-01-01'"
        bad, msg = aggregates_stock_measure_over_time(sql)
        assert bad
        assert "balance_usd" in msg

    def test_flow_columns_not_flagged(self):
        sql = "SELECT SUM(volume_usd) FROM x WHERE date >= '2025-01-01'"
        bad, _ = aggregates_stock_measure_over_time(sql)
        assert not bad

    def test_comments_dont_trigger(self):
        sql = """
        -- SELECT SUM(tvl_usd) FROM x
        SELECT count() FROM x
        """
        bad, _ = aggregates_stock_measure_over_time(sql)
        assert not bad

    def test_empty_sql_passes(self):
        bad, _ = aggregates_stock_measure_over_time("")
        assert not bad


# ---------------------------------------------------------------------------
# Residual-bucket heuristic
# ---------------------------------------------------------------------------

class TestResidualBucket:
    def test_excluding_unlabelled_without_disclosure_flagged(self):
        sql = """
        SELECT label, sum(value)
        FROM dbt.api_execution_transactions_by_sector_daily
        WHERE label != ''
        GROUP BY label
        """
        bad, msg = uses_dimension_without_residual(sql, chart_metadata=None)
        assert bad
        assert "label" in msg

    def test_is_not_null_filter_flagged(self):
        sql = """
        SELECT category, count() FROM x WHERE category IS NOT NULL GROUP BY category
        """
        bad, _ = uses_dimension_without_residual(sql, chart_metadata=None)
        assert bad

    def test_disclosed_in_subtitle_passes(self):
        sql = """
        SELECT label, sum(value) FROM x WHERE label != '' GROUP BY label
        """
        meta = {"subtitle": "share of labelled transactions only"}
        bad, _ = uses_dimension_without_residual(sql, chart_metadata=meta)
        assert not bad

    def test_disclosed_in_description_passes(self):
        sql = "SELECT label, count() FROM x WHERE label IS NOT NULL GROUP BY label"
        meta = {"description": "Excluding unlabelled rows; labelled fraction = 31% of total."}
        bad, _ = uses_dimension_without_residual(sql, chart_metadata=meta)
        assert not bad

    def test_no_residual_filter_passes(self):
        sql = "SELECT label, sum(value) FROM x GROUP BY label ORDER BY 2 DESC"
        bad, _ = uses_dimension_without_residual(sql)
        assert not bad


# ---------------------------------------------------------------------------
# Time-series correlation heuristic
# ---------------------------------------------------------------------------

class TestTimeSeriesCorrelation:
    def test_corr_on_date_series_flagged(self):
        sql = """
        SELECT corr(tvl_usd, volume_usd) AS r
        FROM (SELECT date, sum(tvl_usd) AS tvl_usd, sum(volume_usd) AS volume_usd
              FROM x GROUP BY date)
        """
        bad, msg = pearson_correlation_on_time_series(sql)
        assert bad
        assert "stationarity" in msg.lower() or "spearman" in msg.lower()

    def test_corr_with_first_difference_passes(self):
        sql = """
        WITH d AS (
          SELECT date, x, x - lagInFrame(x) OVER (ORDER BY date) AS dx,
                 y, y - lagInFrame(y) OVER (ORDER BY date) AS dy
          FROM source
        )
        SELECT corr(dx, dy) AS diff_corr FROM d WHERE dx IS NOT NULL
        """
        bad, _ = pearson_correlation_on_time_series(sql)
        assert not bad

    def test_corr_with_spearman_passes(self):
        sql = """
        SELECT corr(rank_x, rank_y) AS spearman
        FROM (SELECT rank() OVER (ORDER BY x) AS rank_x,
                     rank() OVER (ORDER BY y) AS rank_y FROM source)
        """
        bad, _ = pearson_correlation_on_time_series(sql)
        assert not bad

    def test_corr_without_time_column_passes(self):
        sql = "SELECT corr(x, y) FROM source"
        bad, _ = pearson_correlation_on_time_series(sql)
        assert not bad

    def test_disclosure_in_metadata_passes(self):
        sql = "SELECT corr(x, y), date FROM source"
        meta = {"override_reason": "Stationarity verified out-of-band; ADF p<0.01 for both series."}
        bad, _ = pearson_correlation_on_time_series(sql, chart_metadata=meta)
        assert not bad


# ---------------------------------------------------------------------------
# Aggregator volume dedup heuristic
# ---------------------------------------------------------------------------

class TestAggregatorVolume:
    def test_naive_volume_sum_on_pools_daily_flagged(self):
        sql = """
        SELECT date, sum(volume_usd) AS vol
        FROM dbt.fct_execution_pools_daily
        GROUP BY date
        """
        bad, msg = aggregator_volume_double_count_risk(sql)
        assert bad
        assert "volume" in msg.lower()

    def test_dedup_cte_passes(self):
        sql = """
        WITH dedup AS (
          SELECT date, transaction_hash, any(volume_usd) AS volume_usd
          FROM dbt.fct_execution_pools_daily
          GROUP BY date, transaction_hash
        )
        SELECT date, sum(volume_usd) FROM dedup GROUP BY date
        """
        bad, _ = aggregator_volume_double_count_risk(sql)
        assert not bad

    def test_first_hop_disclosure_passes(self):
        sql = """
        SELECT date, sum(volume_usd) FROM dbt.fct_execution_pools_daily GROUP BY date
        """
        meta = {"description": "first-hop only; multi-hop double-counting deliberately excluded"}
        bad, _ = aggregator_volume_double_count_risk(sql, chart_metadata=meta)
        assert not bad

    def test_volume_sum_outside_pools_passes(self):
        sql = "SELECT sum(volume_usd) FROM dbt.fct_execution_gpay_kpi_monthly"
        bad, _ = aggregator_volume_double_count_risk(sql)
        assert not bad


# ---------------------------------------------------------------------------
# Aggregate evaluator
# ---------------------------------------------------------------------------

class TestEvaluateAll:
    def test_clean_sql_returns_no_violations(self):
        sql = "SELECT date, count() FROM dbt.fct_execution_pools_daily GROUP BY date"
        violations = evaluate_all(
            chart_id="chart_1",
            sql=sql,
            chart_metadata=None,
            enabled={"stock_flow": True, "residual_bucket": True,
                     "stationarity": True, "aggregator_dedup": True},
        )
        assert violations == []

    def test_multiple_violations_flagged(self):
        sql = """
        SELECT label, corr(tvl_usd, volume_usd), sum(volume_usd)
        FROM dbt.fct_execution_pools_daily
        WHERE label != '' AND date >= '2025-01-01'
        GROUP BY label, date
        """
        violations = evaluate_all(
            chart_id="chart_2",
            sql=sql,
            chart_metadata=None,
            enabled={"stock_flow": True, "residual_bucket": True,
                     "stationarity": True, "aggregator_dedup": True},
        )
        rules = {v.rule for v in violations}
        assert "residual_bucket_disclosure" in rules
        assert "stationarity_on_correlations" in rules
        assert "aggregator_volume_dedup" in rules

    def test_disabled_heuristic_skipped(self):
        sql = "SELECT SUM(tvl_usd) FROM x WHERE date >= '2025-01-01'"
        violations = evaluate_all(
            chart_id="chart_3",
            sql=sql,
            chart_metadata=None,
            enabled={"stock_flow": False, "residual_bucket": True,
                     "stationarity": True, "aggregator_dedup": True},
        )
        assert violations == []

    def test_default_stock_columns_includes_common_set(self):
        assert "tvl_usd" in DEFAULT_STOCK_COLUMNS
        assert "balance" in DEFAULT_STOCK_COLUMNS
        assert "supply" in DEFAULT_STOCK_COLUMNS
        assert "cumulative_accounts" in DEFAULT_STOCK_COLUMNS
        # Flow measures should NOT be in there
        assert "volume_usd" not in DEFAULT_STOCK_COLUMNS
        assert "fees_usd" not in DEFAULT_STOCK_COLUMNS
