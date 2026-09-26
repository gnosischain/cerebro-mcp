"""Live ClickHouse smoke tests for the Pool Liquidity Explorer plane.

Skipped unless ``CEREBRO_LIVE_CH_SMOKE`` is set, so ``make test`` and CI stay
hermetic. These exist because a stub ClickHouse structurally cannot catch the
failures this plane actually has: an output alias that shadows a source column
and empties a result with no error, a scan that only OOMs at real cardinality,
a window form ClickHouse rejects at plan time, and a projection whose type
changes depending on whether the server folded it to a constant.

The hermetic suite (tests/test_pools_explorer.py) pins the shapes. This one
pins that the shapes actually run, and that the numbers reconcile against the
plane's own independently-derived view.
"""

from __future__ import annotations

import os
import time

import pytest

from cerebro_mcp.clients.clickhouse import INTERACTIVE_QUERY_BUDGET, ClickHouseManager
from cerebro_mcp.tools.visualization import pools_explorer as px


pytestmark = pytest.mark.skipif(
    not os.environ.get("CEREBRO_LIVE_CH_SMOKE"),
    reason="live ClickHouse smoke tests require CEREBRO_LIVE_CH_SMOKE=1",
)

#: Every spec must answer inside this, well under the 20s interactive budget.
#: A dataset that creeps past it is a panel that will time out for a user
#: before it is a test failure here.
SLOW_SECONDS = 12.0

EXPECTED_RELATIONS = {
    "config_registry": {"chain_id", "job_name", "target_kind", "target_address",
                        "enabled", "canonical_config_json"},
    "census_publications": {"chain_id", "job_name", "target_kind", "target_address",
                            "snapshot_date", "checks_passed", "anchor_block",
                            "anchor_hash", "published_at", "publication_id",
                            "attempt_id", "integrity_mode", "block_reference_kind",
                            "executor_kind", "observations_total"},
    # What the v_pool_* views actually serve (lesson: published-is-not-served).
    "v_publications_current": {"chain_id", "job_name", "target_kind",
                               "target_address", "snapshot_date", "attempt_id"},
    "v_pool_cl_state_published": {"chain_id", "job_name", "pool_address",
                                  "snapshot_date", "sqrt_price_x96", "current_tick",
                                  "liquidity", "tick_spacing", "fee", "tick_count",
                                  "anchor_block", "fee_growth_global_0_x128",
                                  "fee_growth_global_1_x128"},
    "v_pool_tick_liquidity_published": {"chain_id", "job_name", "pool_address",
                                        "snapshot_date", "tick", "liquidity_gross",
                                        "liquidity_net", "fee_growth_outside_0_x128",
                                        "fee_growth_outside_1_x128"},
    "v_pool_token_balances_published": {"chain_id", "job_name", "pool_address",
                                        "token_address", "snapshot_date",
                                        "balance_raw", "anchor_block"},
    "v_token_metadata_current": {"chain_id", "token_address", "symbol", "name",
                                 "decimals", "resolution_status"},
    "v_day_anchors_canonical": {"chain_id", "snapshot_date", "block_number",
                                "block_timestamp"},
    "v_pool_liquidity_profile": {"chain_id", "pool_address", "snapshot_date",
                                 "tick_lower", "tick_upper", "active_liquidity"},
}


@pytest.fixture(scope="module")
def ch():
    manager = ClickHouseManager()
    try:
        manager.run_query(
            f"SELECT 1 FROM {px.POOLS_DB}.{px.PUB_TABLE} LIMIT 1",
            database=px.POOLS_DB, requested_max_rows=1,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"{px.POOLS_DB} unreachable: {exc}")
    return manager


def _run(ch, spec, max_rows=200):
    return ch.run_query(
        spec.sql, database=px.POOLS_DB, requested_max_rows=max_rows,
        parameters=spec.parameters or None, query_budget=INTERACTIVE_QUERY_BUDGET,
    )


def _rows(ch, sql, params=None, max_rows=200):
    return ch.run_query(
        sql, database=px.POOLS_DB, requested_max_rows=max_rows,
        parameters=params, query_budget=INTERACTIVE_QUERY_BUDGET,
    ).rows


@pytest.fixture(scope="module")
def live_pools(ch):
    """Pools picked from the data at run time, not pinned: a hardcoded address
    rots the day the indexer's universe changes, and the properties under test
    (probed / state-only / reserves-only) are what matter, not the identity.
    The as-of is the app's own SERVED resolution: the raw max(snapshot_date) is a
    day still being written for hours every morning."""
    as_of = _rows(ch, f"{px._asof_cte('')}\nSELECT toString((SELECT as_of FROM asof))")[0][0]
    probed = _rows(ch, f"""
        SELECT s.pool_address FROM {px.POOLS_DB}.{px.STATE_VIEW} AS s
        WHERE s.chain_id = {px.CHAIN_ID} AND s.job_name = '{px.CL_JOB}'
          AND s.snapshot_date = toDate('{as_of}') AND s.tick_count > 1
        ORDER BY s.liquidity DESC, s.pool_address LIMIT 1
    """)[0][0]
    state_only = _rows(ch, f"""
        SELECT s.pool_address FROM {px.POOLS_DB}.{px.STATE_VIEW} AS s
        WHERE s.chain_id = {px.CHAIN_ID} AND s.job_name = '{px.CL_JOB}'
          AND s.snapshot_date = toDate('{as_of}') AND s.tick_count = 0
          AND s.liquidity > 0
        ORDER BY s.liquidity DESC, s.pool_address LIMIT 1
    """)[0][0]
    reserves_only = _rows(ch, f"""
        SELECT c.target_address FROM {px.POOLS_DB}.config_registry AS c FINAL
        WHERE c.chain_id = {px.CHAIN_ID} AND c.job_name = '{px.RESERVES_JOB}'
          AND c.target_kind = 'pool' AND c.enabled = 1
          AND JSONExtractString(c.canonical_config_json, 'target', 'pool_class')
              LIKE 'balancer%'
        ORDER BY c.target_address LIMIT 1
    """)[0][0]
    token = _rows(ch, f"""
        SELECT m.token_address FROM {px.POOLS_DB}.{px.METADATA_VIEW} AS m
        WHERE m.chain_id = {px.CHAIN_ID} AND m.decimals IS NOT NULL
        ORDER BY m.token_address LIMIT 1
    """)[0][0]
    return {"as_of": as_of, "probed": probed, "state_only": state_only,
            "reserves_only": reserves_only, "token": token}


def test_every_expected_relation_exists_with_its_key_columns(ch):
    # An empty SELECT rather than DESCRIBE: the query validator appends a LIMIT,
    # which DESCRIBE will not take, and this also proves the relation is
    # actually selectable rather than merely present in the catalog.
    for relation, columns in EXPECTED_RELATIONS.items():
        result = ch.run_query(
            f"SELECT * FROM {px.POOLS_DB}.{relation} WHERE 1 = 0",
            database=px.POOLS_DB, requested_max_rows=1,
        )
        missing = sorted(columns - set(result.columns))
        assert missing == [], f"{relation} is missing {missing}"


def test_every_spec_executes_against_live_clickhouse(ch, live_pools):
    """The whole spec surface, deduplicated, with every filter variant the UI
    can produce. Failures are collected rather than raised one at a time: one
    run should report every broken dataset, not the first."""
    defaults = px._default_filters()
    batches: list[tuple[str, list[px.QuerySpec]]] = []
    for as_of, window in (("", "1y"), (live_pools["as_of"], "90d"), ("", "all")):
        batches += [
            (f"overview[{as_of or 'latest'}/{window}]",
             px._overview_specs(as_of, window)),
            (f"pools[{as_of or 'latest'}]", px._pools_specs(as_of, defaults)),
            (f"tokens[{as_of or 'latest'}]", px._tokens_specs(as_of, defaults)),
            (f"coverage[{as_of or 'latest'}/{window}]",
             px._coverage_specs(as_of, window)),
            (f"pool:probed[{window}]",
             px._pool_entity_specs(live_pools["probed"], as_of, window, "1y", True)),
            (f"pool:state_only[{window}]",
             px._pool_entity_specs(live_pools["state_only"], as_of, window, "90d", True)),
            (f"pool:reserves_only[{window}]",
             px._pool_entity_specs(live_pools["reserves_only"], as_of, window, "1y", False)),
            (f"token[{as_of or 'latest'}]",
             px._token_entity_specs(live_pools["token"], as_of)),
        ]
    batches.append(("pools:filtered", px._pools_specs("", {
        **defaults, "pool_class": "uniswap_v3", "fee_band": "b3000",
        "live_only": True, "probed_only": True, "sort_by": "tick_count_desc",
    })))
    batches.append(("pools:balancer", px._pools_specs("", {
        **defaults, "pool_family": "reserves_only", "sort_by": "first_published_asc",
    })))
    batches.append(("pools:by_token", px._pools_specs("", {
        **defaults, "token": live_pools["token"], "sort_by": "liquidity_asc",
    })))
    batches.append(("tokens:query", px._tokens_specs("", {
        **defaults, "query": "0x", "sort_by": "symbol_asc",
    })))

    seen: set[tuple] = set()
    failures: list[str] = []
    slow: list[str] = []
    for label, specs in batches:
        for spec in specs:
            fingerprint = (spec.sql, tuple(sorted(spec.parameters.items())))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            started = time.time()
            try:
                _run(ch, spec)
            except Exception as exc:
                failures.append(f"{label}/{spec.key}: {str(exc).splitlines()[0][:220]}")
                continue
            elapsed = time.time() - started
            if elapsed > SLOW_SECONDS:
                slow.append(f"{label}/{spec.key}: {elapsed:.1f}s")
    assert len(seen) > 40, f"only {len(seen)} specs swept — the builders moved"
    assert failures == [], "\n".join(failures)
    assert slow == [], "\n".join(slow)


def test_search_arms_execute_and_classify(ch, live_pools):
    exact = px._search_candidates(ch, live_pools["probed"])
    assert any(c["entity_type"] == "pool" and c["identifier"] == live_pools["probed"]
               for c in exact)
    prefix = px._search_candidates(ch, live_pools["probed"][:10])
    assert any(c["identifier"] == live_pools["probed"] for c in prefix)
    assert len(prefix) <= px.SEARCH_CANDIDATE_CAP
    symbol = _rows(ch, f"""
        SELECT m.symbol FROM {px.POOLS_DB}.{px.METADATA_VIEW} AS m
        WHERE m.chain_id = {px.CHAIN_ID} AND m.token_address = {{t:String}}
    """, {"t": live_pools["token"]})
    if symbol and symbol[0][0]:
        by_symbol = px._search_candidates(ch, symbol[0][0])
        assert all(c["entity_type"] == "token" for c in by_symbol)


def test_the_recomputed_profile_matches_the_indexers_own_derived_view(ch, live_pools):
    """The reason this app recomputes ranges from ticks instead of reading
    v_pool_liquidity_profile is that the derived view only starts 2025-09-01.
    That is only safe if the two agree where they overlap, so they are compared
    on a date both cover — as a set, not a count."""
    spec = next(s for s in px._pool_entity_specs(
        live_pools["probed"], "", "1y", "1y", True) if s.key == "pool_profile_at")
    result = _run(ch, spec, max_rows=500)
    index = {name: i for i, name in enumerate(result.columns)}
    recomputed = {
        (row[index["tick_lower"]], row[index["tick_upper"]],
         str(row[index["active_liquidity_raw"]]))
        for row in result.rows
    }
    oracle = {
        (row[0], row[1], str(row[2]))
        for row in _rows(ch, f"""
            SELECT tick_lower, tick_upper, toString(active_liquidity)
            FROM {px.POOLS_DB}.v_pool_liquidity_profile
            WHERE chain_id = {px.CHAIN_ID} AND pool_address = {{p:String}}
              AND snapshot_date = (
                SELECT max(snapshot_date) FROM {px.POOLS_DB}.{px.PUB_TABLE}
                WHERE job_name = '{px.CL_JOB}' AND target_kind = 'pool'
                  AND chain_id = {px.CHAIN_ID})
            ORDER BY tick_lower
        """, {"p": live_pools["probed"]}, max_rows=500)
    }
    assert recomputed, "the recompute returned no ranges for a probed pool"
    assert recomputed == oracle


def test_the_active_range_reconciles_with_the_pools_reported_liquidity(ch, live_pools):
    """The range containing the current tick must carry exactly the liquidity
    the pool itself reports. A mismatch means the recompute and the chain
    disagree, which is the one failure this plane could not otherwise notice."""
    spec = next(s for s in px._pool_entity_specs(
        live_pools["probed"], "", "1y", "1y", True) if s.key == "pool_profile_at")
    result = _run(ch, spec, max_rows=500)
    index = {name: i for i, name in enumerate(result.columns)}
    current = [r for r in result.rows if r[index["contains_current_tick"]]]
    assert len(current) == 1, f"expected one active range, got {len(current)}"
    assert current[0][index["matches_state_liquidity"]] == 1


def test_the_heatmap_stays_inside_its_row_budget(ch, live_pools):
    """Both bounds at once: the widest window on the most active pool."""
    spec = next(s for s in px._pool_entity_specs(
        live_pools["probed"], "", "all", "all", True)
        if s.key == "pool_profile_heatmap")
    counted = _rows(ch, f"SELECT count(), uniqExact(bucket_date) FROM ({spec.sql})",
                    spec.parameters)[0]
    rows, dates = int(counted[0]), int(counted[1])
    assert dates <= px.HEATMAP_MAX_DATES, dates
    assert rows <= px.HEATMAP_MAX_DATES * px.HEATMAP_TICK_BUCKETS, rows
    assert rows < px.ROW_CAP, rows


def test_the_directory_returns_the_whole_configured_universe(ch):
    """The directory is built on the configured set, not on what published, so
    a pool the indexer skipped today still appears with NULL state instead of
    disappearing from the table."""
    summary = _run(ch, next(
        s for s in px._overview_specs("", "1y") if s.key == "pools_summary"))
    index = {name: i for i, name in enumerate(summary.columns)}
    configured = int(summary.rows[0][index["pools_configured"]])
    directory = px._pools_specs("", px._default_filters())[0]
    counted = int(_rows(ch, f"SELECT count() FROM ({directory.sql})",
                        directory.parameters)[0][0])
    assert counted == configured


def test_reserves_only_pools_carry_no_fabricated_liquidity_columns(ch, live_pools):
    """ClickHouse fills an unmatched LEFT JOIN with zeros, and a zero
    current_tick reads as 'price 1'. Every concentrated-liquidity column must
    come back NULL for a Balancer pool."""
    spec = px._pools_specs("", {**px._default_filters(),
                                "pool_family": "reserves_only"})[0]
    result = _run(ch, spec, max_rows=50)
    index = {name: i for i, name in enumerate(result.columns)}
    assert result.rows
    for row in result.rows:
        assert row[index["pool_family"]] == "reserves_only"
        for column in ("current_tick", "price_raw", "price_adjusted",
                       "liquidity_raw", "tick_count", "fee"):
            assert row[index[column]] is None, (column, row[index["pool_address"]])


def test_an_unresolved_pool_reports_a_raw_price_and_no_adjusted_one(ch):
    """Most pools here pair a resolved token with an unresolved one. They must
    still get a price — the raw one, which needs no decimals — and must never
    get an adjusted one inferred from a guess."""
    spec = px._pools_specs("", px._default_filters())[0]
    result = _run(ch, spec, max_rows=500)
    index = {name: i for i, name in enumerate(result.columns)}
    unresolved = [
        r for r in result.rows
        if r[index["pool_family"]] == "cl"
        and r[index["price_raw"]] is not None
        and (r[index["token0_decimals"]] is None or r[index["token1_decimals"]] is None)
    ]
    assert unresolved, "expected at least one pool with unresolved decimals"
    for row in unresolved:
        assert row[index["price_adjusted"]] is None


def test_a_requested_date_the_indexer_skipped_resolves_backwards(ch):
    """Never an equality: a day with no publication must resolve to the newest
    day before it, and the dataset must say which day it actually used."""
    spec = next(s for s in px._overview_specs("2026-03-02", "90d")
                if s.key == "pools_summary")
    result = _run(ch, spec)
    index = {name: i for i, name in enumerate(result.columns)}
    resolved = str(result.rows[0][index["as_of"]])
    assert resolved <= "2026-03-02"
    assert resolved >= px.FIRST_DATE.isoformat()


def _complete_day(days: list[tuple[str, int, int]]) -> str:
    """The complete-day rule, re-derived in Python from (date, published,
    served) rows ascending — independently of the SQL window functions."""
    complete = []
    for i, (day, published, served) in enumerate(days):
        prior = [s for _, _, s in days[max(0, i - px.ASOF_PEAK_DAYS):i]]
        if served >= px.ASOF_COMPLETENESS_RATIO * max(published, max(prior, default=0)):
            complete.append(day)
    if complete:
        return max(complete)
    return max((d for d, _, s in days if s > 0), default="1970-01-01")


def test_the_as_of_is_the_newest_complete_served_day(ch):
    """Lesson: published-is-not-served. The resolver's answers and the raw
    ingredients they are judged on come from ONE query (lesson:
    live-table-invalidates-cross-query-diff); the rule is then re-derived here.
    The dates cover the latest (the CL job publishes over 1-4.5 hours every
    morning, so the raw max is often half-written), a day the indexer stopped
    part-way through (2026-08-23: 1,082 of 2,519 pools when this was written),
    a re-censused day, and one from an older era of the universe. Nothing is
    pinned to today's data: a repaired day simply becomes complete here too."""
    pub = f"{px.POOLS_DB}.{px.PUB_TABLE}"
    served = f"{px.POOLS_DB}.{px.SERVED_VIEW}"
    for requested in ("", "2026-08-23", "2026-09-10", "2025-06-15"):
        bound = "snapshot_date <= {as_of:Date}" if requested else "1"
        params = {"as_of": requested} if requested else None
        sql = f"""
{px._asof_cte(requested)},
{px._reserves_asof_cte()},
t_cand AS (
  SELECT job_name AS t_job, snapshot_date AS t_date, uniqExact(target_address) AS t_pub
  FROM {pub}
  WHERE job_name IN ('{px.CL_JOB}', '{px.RESERVES_JOB}') AND target_kind = 'pool'
    AND chain_id = {px.CHAIN_ID} AND {bound}
  GROUP BY t_job, t_date ORDER BY t_date DESC LIMIT {px.ASOF_CANDIDATE_DAYS * 3} BY t_job
),
t_srv AS (
  SELECT job_name AS u_job, snapshot_date AS u_date, uniqExact(target_address) AS u_srv
  FROM {served}
  WHERE job_name IN ('{px.CL_JOB}', '{px.RESERVES_JOB}') AND target_kind = 'pool'
    AND chain_id = {px.CHAIN_ID} AND snapshot_date IN (SELECT t_date FROM t_cand)
  GROUP BY u_job, u_date
)
SELECT toString((SELECT as_of FROM asof)) AS resolved,
       toString((SELECT ras_of FROM rasof)) AS reserves_resolved,
       -- Arrays of strings, not tuples: the driver hands a named tuple back as
       -- a dict, and unpacking a dict yields its KEYS.
       groupArray([toString(t_job), toString(t_date), toString(t_pub), toString(u_srv)])
         AS days
FROM t_cand LEFT JOIN t_srv ON t_srv.u_job = t_cand.t_job AND t_srv.u_date = t_cand.t_date
"""
        resolved, reserves_resolved, days = _rows(ch, sql, params)[0]
        cl = sorted((d, int(p), int(s)) for job, d, p, s in days if job == px.CL_JOB)
        expected = _complete_day(cl[-px.ASOF_CANDIDATE_DAYS:])
        assert resolved == expected, (requested, resolved, expected, cl[-10:])
        # Reserves: the same rule over the reserves candidates on or before it.
        rs = sorted((d, int(p), int(s)) for job, d, p, s in days
                    if job == px.RESERVES_JOB and d <= resolved)
        if len(rs) >= px.ASOF_CANDIDATE_DAYS:
            assert reserves_resolved == _complete_day(rs[-px.ASOF_CANDIDATE_DAYS:]), requested
        assert reserves_resolved <= resolved


def test_a_re_censused_day_fans_no_pool_out(ch):
    """A re-census writes a second raw publication for a pool-day. Before the
    served pin, every probe-joined dataset counted such a pool twice:
    pools_summary reported 4,215 configured pools on 2026-09-10 against a
    4,022-pool registry, the directory listed 193 pools twice, and a pool's
    history drew the day twice. Checked on the most recent CL day that has
    duplicate publications, whichever that is when this runs."""
    pub = f"{px.POOLS_DB}.{px.PUB_TABLE}"
    rows = _rows(ch, f"""
        SELECT toString(snapshot_date) AS d, count() - uniqExact(target_address) AS extra
        FROM {pub}
        WHERE job_name = '{px.CL_JOB}' AND target_kind = 'pool' AND chain_id = {px.CHAIN_ID}
        GROUP BY snapshot_date HAVING extra > 0
        ORDER BY snapshot_date DESC LIMIT 1
    """)
    if not rows:
        pytest.skip("no CL day carries duplicate publications")
    day = rows[0][0]
    summary = next(s for s in px._overview_specs(day, "90d") if s.key == "pools_summary")
    configured, configured_cl, published_cl, registry = _rows(ch, f"""
        SELECT s.pools_configured, s.pools_configured_cl, s.pools_published_cl,
               (SELECT count() FROM {px.POOLS_DB}.config_registry AS c FINAL
                WHERE c.chain_id = {px.CHAIN_ID} AND c.job_name = '{px.RESERVES_JOB}'
                  AND c.target_kind = 'pool' AND c.enabled = 1) AS registry
        FROM ({summary.sql}) AS s
    """, summary.parameters)[0]
    assert int(configured) == int(registry), (day, configured, registry)
    assert int(published_cl) <= int(configured_cl), day
    directory = px._pools_specs(day, px._default_filters())[0]
    n, unique = _rows(ch, f"SELECT count(), uniqExact(pool_address) FROM ({directory.sql})",
                      directory.parameters)[0]
    assert int(n) == int(unique) == int(registry), (day, n, unique, registry)

    pool = _rows(ch, f"""
        SELECT target_address FROM {pub}
        WHERE job_name = '{px.CL_JOB}' AND target_kind = 'pool' AND chain_id = {px.CHAIN_ID}
          AND snapshot_date = toDate('{day}')
        GROUP BY target_address HAVING count() > 1 ORDER BY target_address LIMIT 1
    """)[0][0]
    for spec in px._pool_entity_specs(pool, day, "90d", "90d", True):
        if spec.key not in ("pool_detail", "pool_publication_facts",
                            "pool_state_history", "pool_fee_growth"):
            continue
        result = _run(ch, spec, max_rows=1000)
        index = {name: i for i, name in enumerate(result.columns)}
        if spec.key == "pool_detail":
            assert len(result.rows) == 1, (spec.key, pool, day)
            continue
        keys = [tuple(row[index[c]] for c in ("job_name", "snapshot_date") if c in index)
                for row in result.rows]
        assert len(keys) == len(set(keys)), (spec.key, pool, day)


def test_every_date_column_arrives_as_a_string(ch, live_pools):
    """The bug this contract exists for: with an unbounded as-of predicate
    ClickHouse folds the projection to a constant and the driver returns the
    raw day number, so the same column was a date on one path and 20712 on
    another. Both as-of branches are checked because only one folds."""
    date_columns = {
        "as_of", "reserves_as_of", "snapshot_date", "prev_snapshot_date",
        "bucket_date", "bucket", "first_published", "last_published",
        "profile_available_from", "latest_snapshot_date", "first_snapshot_date",
        "last_snapshot_date",
    }
    offenders = []
    for as_of in ("", live_pools["as_of"]):
        specs = [
            *px._overview_specs(as_of, "90d"),
            *px._pools_specs(as_of, px._default_filters()),
            *px._tokens_specs(as_of, px._default_filters()),
            *px._coverage_specs(as_of, "90d"),
            *px._pool_entity_specs(live_pools["probed"], as_of, "90d", "90d", True),
            *px._token_entity_specs(live_pools["token"], as_of),
        ]
        for spec in specs:
            result = _run(ch, spec, max_rows=5)
            if not result.rows:
                continue
            for name, value in zip(result.columns, result.rows[0]):
                if name in date_columns and value is not None and not isinstance(value, str):
                    offenders.append(f"{spec.key}.{name} = {value!r}")
    assert offenders == [], offenders


def test_the_indexer_really_has_no_metadata_for_most_pool_tokens(ch):
    """The premise of the RPC overlay, checked against the data rather than
    assumed: if the indexer ever starts cataloguing these, the overlay becomes
    redundant and this test says so."""
    row = _rows(ch, f"""
        WITH toks AS (
          SELECT DISTINCT JSONExtractString(a, 'token') AS t
          FROM {px.POOLS_DB}.config_registry FINAL
          ARRAY JOIN JSONExtractArrayRaw(canonical_config_json, 'target', 'assets') AS a
          WHERE job_name = '{px.RESERVES_JOB}' AND chain_id = {px.CHAIN_ID}
            AND enabled = 1)
        SELECT count(), countIf(m.decimals IS NULL) FROM toks
        LEFT JOIN {px.POOLS_DB}.{px.METADATA_VIEW} AS m
          ON m.chain_id = {px.CHAIN_ID} AND m.token_address = toks.t
    """)[0]
    total, unresolved = int(row[0]), int(row[1])
    assert total > 1000
    assert unresolved > total * 0.5, (
        f"only {unresolved}/{total} pool tokens lack indexer metadata — if the "
        f"indexer now catalogues them, the RPC overlay may be unnecessary"
    )


def test_the_overlay_labels_tokens_the_indexer_never_catalogued(ch, live_pools):
    """The end the user asked for: a token with no indexer metadata comes back
    with a real symbol and decimals read off the chain."""
    from cerebro_mcp.tools.visualization import token_rpc

    if not token_rpc.is_configured(px.CHAIN_ID):
        pytest.skip("no Gnosis RPC endpoint configured")
    token_rpc.reset_cache_for_tests()
    unresolved = [
        r[0] for r in _rows(ch, f"""
            WITH toks AS (
              SELECT DISTINCT JSONExtractString(a, 'token') AS t
              FROM {px.POOLS_DB}.config_registry FINAL
              ARRAY JOIN JSONExtractArrayRaw(canonical_config_json, 'target', 'assets') AS a
              WHERE job_name = '{px.RESERVES_JOB}' AND chain_id = {px.CHAIN_ID}
                AND enabled = 1)
            SELECT toks.t FROM toks
            LEFT JOIN {px.POOLS_DB}.{px.METADATA_VIEW} AS m
              ON m.chain_id = {px.CHAIN_ID} AND m.token_address = toks.t
            WHERE m.decimals IS NULL ORDER BY toks.t LIMIT 50
        """, max_rows=50)
    ]
    assert len(unresolved) >= 20
    resolved, stats = token_rpc.resolve_tokens(px.CHAIN_ID, unresolved)
    assert stats.error == ""
    # These are real deployed ERC-20s the indexer simply never swept; a low
    # hit rate means the batching or the decoding regressed, not the chain.
    assert len(resolved) >= len(unresolved) * 0.8, stats
    for meta in resolved.values():
        assert meta.symbol is None or meta.symbol.isprintable()
        assert meta.decimals is None or 0 <= meta.decimals <= 77
        assert meta.block_number > 0


def test_one_multicall_round_trip_carries_the_whole_batch(ch, live_pools):
    """Batching is the reason this is viable at all: 3,300 tokens one call at a
    time would be 3,300 round trips."""
    from cerebro_mcp.tools.visualization import token_rpc

    if not token_rpc.is_configured(px.CHAIN_ID):
        pytest.skip("no Gnosis RPC endpoint configured")
    token_rpc.reset_cache_for_tests()
    tokens = [
        r[0] for r in _rows(ch, f"""
            SELECT DISTINCT JSONExtractString(a, 'token') AS t
            FROM {px.POOLS_DB}.config_registry FINAL
            ARRAY JOIN JSONExtractArrayRaw(canonical_config_json, 'target', 'assets') AS a
            WHERE job_name = '{px.RESERVES_JOB}' AND chain_id = {px.CHAIN_ID}
            ORDER BY t LIMIT {token_rpc.TOKENS_PER_BATCH}
        """, max_rows=token_rpc.TOKENS_PER_BATCH)
    ]
    started = time.time()
    resolved, stats = token_rpc.resolve_tokens(px.CHAIN_ID, tokens)
    elapsed = time.time() - started
    assert stats.fetched + stats.unreadable == len(set(tokens))
    assert elapsed < 10, f"{len(tokens)} tokens took {elapsed:.1f}s"
    # Warm path must not touch the chain at all.
    started = time.time()
    _, warm = token_rpc.resolve_tokens(px.CHAIN_ID, tokens)
    assert warm.fetched == 0
    assert time.time() - started < 0.5
