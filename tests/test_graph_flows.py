"""Flows mode tests: flow SQL locked semantics, edge-id round-trip, the pure
``flows_trace`` walker (ranks, budgets, terminals), the ``load_graph_flows``
tool (replace/merge, aggregates, GP flags), evidence plumbing, patch schema,
and security classification."""

from __future__ import annotations

from typing import Any

import pytest
from unittest.mock import patch

from cerebro_mcp.clients.clickhouse import ExecutedQuery
from cerebro_mcp.semantic import graph_profiles
from cerebro_mcp.semantic.address_semantics import DEAD_ADDRESS, ZERO_ADDRESS
from cerebro_mcp.semantic.flow_queries import (
    BRIDGES_RELATION,
    FLOWS_RELATION,
    TOKENS_META_RELATION,
    build_active_token_universe_sql,
    build_bridge_flows_sql,
    build_bridge_safety_gate_sql,
    build_flow_evidence_sql,
    build_flow_labels_sql,
    build_flows_coverage_sql,
    build_flows_sql,
    build_gp_flags_sqls,
    flow_edge_id,
    parse_flow_edge_id,
)
from cerebro_mcp.tools.semantic.graph_explorer.flows import (
    FlowWalk,
    flows_trace,
    validate_bridge_relation_safety,
)
from cerebro_mcp.tools.visualization import mini_apps

# Shared fixtures/helpers (tests is a package).
from tests.test_graph_explorer import (  # noqa: F401  (fixtures by name)
    AVATAR,
    TRUSTEE,
    FakeSnapshot,
    StubCH,
    _call_tool,
    _graph_model,
    _server,
    reset_state,
)

SEED = "0x1111000000000000000000000000000000000001"
EXPLOITER = "0x2222000000000000000000000000000000000002"
HOP2 = "0x3333000000000000000000000000000000000003"
FUNDER = "0x4444000000000000000000000000000000000004"
DEXADDR = "0x5555000000000000000000000000000000000005"
BRIDGE = "0x6666000000000000000000000000000000000006"
TOKEN = "0xt000000000000000000000000000000000000001"
TOKEN_CONTRACT = "0x7777000000000000000000000000000000000007"

T0 = "2026-06-01 00:00:00"
T1 = "2026-07-01 00:00:00"


# ---------------------------------------------------------------------------
# build_flows_sql — locked semantics
# ---------------------------------------------------------------------------


def test_flows_sql_out_leg_semantics():
    sql, params = build_flows_sql(
        frontier_ids=["0xAbC", "0xdef"],
        direction="out",
        t0=T0,
        t1_exclusive=T1,
        min_usd=10.0,
        tokens=None,
        limit=100,
    )
    assert "d.`from` IN {ids:Array(String)}" in sql
    assert "GROUP BY source_id, target_id, token_address" in sql
    # Daily grain: the window is compared as DATEs against `date`, while the
    # caller still passes datetime strings.
    assert "d.date >= toDate({t0:DateTime}) AND d.date < toDate({t1:DateTime})" in sql
    assert "d.`from` != d.`to`" in sql  # self-loops excluded
    assert "HAVING amount_usd >= {min_usd:Float64}" in sql  # aggregated edge
    assert "amount_usd >= {min_usd:Float64} OR isNull(amount_usd)" in sql
    assert "ORDER BY isNull(amount_usd), amount_usd DESC" in sql
    assert "price_found" in sql
    assert "countIf(coalesce(p.price_found, 0) = 1) = 0" in sql
    assert "CAST(NULL AS Nullable(Float64))" in sql
    assert "coalesce(p.price, 0)" not in sql
    assert params["ids"] == ["0xabc", "0xdef"]  # lowercased as PARAMS
    assert params["lim"] == 101  # limit + 1 truncation detection
    assert "tokens" not in params and "{tokens" not in sql


def test_flows_sql_in_leg_and_token_clause():
    sql, params = build_flows_sql(
        frontier_ids=[EXPLOITER],
        direction="in",
        t0=T0,
        t1_exclusive=T1,
        min_usd=0.0,
        tokens=["0xTOK"],
        limit=10,
    )
    assert "d.`to` IN {ids:Array(String)}" in sql
    assert "token_address IN {tokens:Array(String)}" in sql
    assert params["tokens"] == ["0xtok"]


def test_flows_sql_rejects_both_direction():
    # "both" is the CALLER's job (two queries with per-leg budgets).
    with pytest.raises(ValueError):
        build_flows_sql(
            frontier_ids=[SEED], direction="both", t0=T0, t1_exclusive=T1,
            min_usd=0, tokens=None, limit=10,
        )


def test_flows_coverage_sql_is_exact_pre_budget_and_null_safe():
    sql, params = build_flows_coverage_sql(
        frontier_ids=[SEED],
        direction="out",
        t0=T0,
        t1_exclusive=T1,
        min_usd=10,
        tokens=["0xABC"],
    )
    assert "WITH candidate_edges AS" in sql
    assert "eligible_edges AS" in sql
    assert "uniqExactIf(" in sql
    assert "structural_terminals:Array(String)" in sql
    assert "AS supply_event_edges" in sql
    assert "LIMIT" not in sql
    assert "countIf(unknown_usd_rows > 0)" in sql
    assert "excluded_unknown_usd_edges" in sql
    assert "CAST(NULL AS Nullable(Float64))" in sql
    assert params["tokens"] == ["0xabc"]

    in_sql, _ = build_flows_coverage_sql(
        frontier_ids=[SEED], direction="in", t0=T0, t1_exclusive=T1,
        min_usd=0, tokens=None,
    )
    assert "source_id" in in_sql and "AS total_counterparties" in in_sql


def test_bridge_sql_out_only_never_reversed_no_usd():
    sql, params = build_bridge_flows_sql(
        frontier_ids=[SEED], t0=T0, t1_exclusive=T1, limit=50
    )
    assert "direction = 'out'" in sql
    assert "user_address AS source_id" in sql  # user -> bridge, never reversed
    assert "bridge_contract AS target_id" in sql
    assert "user_address IN {ids:Array(String)}" in sql
    assert "notEmpty(user_address)" in sql
    assert "notEmpty(bridge_contract)" in sql
    assert "user_address != bridge_contract" in sql
    assert "volume_usd" not in sql  # always NULL in the model
    assert "any(bridge_name)" in sql
    assert "ORDER BY transfer_count DESC" in sql
    assert params["t0"] == "2026-06-01" and params["t1"] == "2026-07-01"  # Date
    assert params["lim"] == 51


def test_bridge_safety_gate_scans_full_relation_without_guard_collision():
    sql, params = build_bridge_safety_gate_sql()

    assert params == {}
    assert "graph_explorer_bridge_safety_gate" in sql
    assert "blank_bridge_contract_rows" in sql
    assert "blank_bridge_name_rows" in sql
    assert "blank_user_address_rows" in sql
    assert "invalid_direction_rows" in sql
    assert "endpoint_ambiguity_rows" in sql
    assert "user_address IN" in sql
    assert "SELECT bridge_contract FROM bridge_addresses" in sql
    # No scoped date predicate: stale polluted partitions must fail the gate.
    assert "{t0:" not in sql and "{t1:" not in sql
    # Resource guards are supplied through the typed QueryBudget. Embedding a
    # SETTINGS clause here collides with the shared guarded query wrapper.
    assert "SETTINGS" not in sql.upper()


def test_flow_evidence_sql_transfer_is_transaction_level():
    sql, params = build_flow_evidence_sql(
        edge_class="transfer", source_id=SEED.upper(), target_id=EXPLOITER,
        token_address=TOKEN, t0=T0, t1_exclusive=T1, limit=25,
    )
    # Evidence is TRANSACTION-level and therefore comes from the chain, not
    # from the daily aggregate (which has no transaction_hash at all).
    assert "transaction_hash" in sql
    assert "execution.logs" in sql
    assert FLOWS_RELATION not in sql
    assert "block_timestamp >= {t0:DateTime}" in sql
    assert params["src"] == SEED  # lowercased
    assert params["t0"] == T0 and params["t1"] == T1


def test_flow_evidence_sql_bridge_is_per_day():
    sql, params = build_flow_evidence_sql(
        edge_class="bridge", source_id=SEED, target_id=BRIDGE,
        token_address=TOKEN, t0=T0, t1_exclusive=T1, limit=25,
    )
    assert BRIDGES_RELATION in sql and "GROUP BY date" in sql
    assert "direction = 'out'" in sql
    assert params["t0"] == "2026-06-01"  # Date-truncated


def test_gp_flags_sql_aliases_and_refund_column():
    sqls = build_gp_flags_sqls([SEED, EXPLOITER.upper()])
    kinds = [k for k, _, _ in sqls]
    assert kinds == ["canonical", "refunded"]
    canonical_sql = sqls[0][1]
    assert "address AS old_safe" in canonical_sql  # live columns aliased
    assert "canonical_address AS new_safe" in canonical_sql
    assert "new_safe" in sqls[1][1]
    assert sqls[0][2]["ids"] == [SEED, EXPLOITER]  # lowercased


def test_flow_labels_sql_latest_by_introduced_at():
    sql, params = build_flow_labels_sql([DEXADDR])
    assert "argMax(project, introduced_at)" in sql
    assert "argMax(sector, introduced_at)" in sql
    assert params["ids"] == [DEXADDR]


def test_active_token_universe_uses_effective_interval_overlap():
    sql, params = build_active_token_universe_sql(t0=T0, t1_exclusive=T1)
    assert "date_start < toDate({t1:DateTime})" in sql
    assert "date_end IS NULL OR date_end > toDate({t0:DateTime})" in sql
    assert "GROUP BY token_address" in sql
    assert params["lim"] == 1001


def test_flow_edge_id_round_trip():
    eid = flow_edge_id(SEED, EXPLOITER, TOKEN, "transfer")
    assert eid == f"flow:{SEED}->{EXPLOITER}:{TOKEN}"
    assert parse_flow_edge_id(eid) == ("transfer", SEED, EXPLOITER, TOKEN)
    bid = flow_edge_id(SEED, BRIDGE, TOKEN, "bridge")
    assert parse_flow_edge_id(bid) == ("bridge", SEED, BRIDGE, TOKEN)
    assert parse_flow_edge_id("circles_trust:0xa->0xb") is None  # profile id
    assert parse_flow_edge_id("") is None
    assert parse_flow_edge_id("flow:garbage") is None


# ---------------------------------------------------------------------------
# flows_trace — pure walker with injected fetchers
# ---------------------------------------------------------------------------


def _edge_row(src, tgt, usd, symbol="GNO", token=TOKEN, n=1):
    return [src, tgt, token, symbol, usd / 100.0, usd, n,
            "2026-06-02 10:00:00", "2026-06-20 10:00:00"]


def _adjacency_fetchers(edges: list[list[Any]], labels: dict[str, tuple[str, str]] | None = None):
    """fetch_edges emulating the SQL: out = rows whose src in ids, in = rows
    whose tgt in ids, USD desc."""
    labels = labels or {}

    def fetch_edges(ids, leg):
        col = 0 if leg == "out" else 1
        rows = [r for r in edges if r[col] in ids]
        return sorted(rows, key=lambda r: -r[5])

    def fetch_labels(ids):
        return [[i, labels[i][0], labels[i][1]] for i in ids if i in labels]

    return fetch_edges, fetch_labels


def _trace(**kw):
    defaults = dict(
        ch=None,
        seeds=[SEED],
        direction="out",
        hops=2,
        t0=T0,
        t1=T1,
        min_usd=0.0,
        tokens=None,
        include_bridges=False,
        per_hop_budget=400,
        node_cap=3000,
        edge_cap=8000,
        per_query_limit=2000,
        fetch_bridges=lambda ids: [],
        fetch_token_contracts=lambda ids: [],
        fetch_labels=lambda ids: [],
    )
    defaults.update(kw)
    return flows_trace(**defaults)


def test_trace_out_ranks_positive():
    fetch_edges, _ = _adjacency_fetchers([
        _edge_row(SEED, EXPLOITER, 1000),
        _edge_row(EXPLOITER, HOP2, 500),
    ])
    walk = _trace(fetch_edges=fetch_edges)
    assert walk.nodes[SEED]["rank"] == 0
    assert walk.nodes[EXPLOITER]["rank"] == 1
    assert walk.nodes[HOP2]["rank"] == 2
    assert len(walk.edges) == 2 and not walk.truncated


def test_trace_in_ranks_negative():
    fetch_edges, _ = _adjacency_fetchers([
        _edge_row(FUNDER, SEED, 50),
        _edge_row(HOP2, FUNDER, 25),
    ])
    walk = _trace(direction="in", fetch_edges=fetch_edges)
    assert walk.nodes[FUNDER]["rank"] == -1
    assert walk.nodes[HOP2]["rank"] == -2


def test_trace_both_min_abs_rank_with_out_bias():
    # EXPLOITER reachable at +1 (out) and -1 (in). Out leg runs FIRST each
    # hop, so the node keeps +1; the rank is assigned once, never revised.
    fetch_edges, _ = _adjacency_fetchers([
        _edge_row(SEED, EXPLOITER, 1000),
        _edge_row(EXPLOITER, SEED, 900),
    ])
    walk = _trace(direction="both", hops=1, fetch_edges=fetch_edges)
    assert walk.nodes[EXPLOITER]["rank"] == 1


def test_trace_budget_usd_desc_admission_drops_overflow_edges():
    fetch_edges, _ = _adjacency_fetchers([
        _edge_row(SEED, EXPLOITER, 1000),
        _edge_row(SEED, HOP2, 900),
        _edge_row(SEED, FUNDER, 10),  # over budget — node AND edge dropped
    ])
    walk = _trace(hops=1, per_hop_budget=2, fetch_edges=fetch_edges)
    assert set(walk.nodes) == {SEED, EXPLOITER, HOP2}  # USD-desc admission
    targets = {e["target"] for e in walk.edges.values()}
    assert FUNDER not in targets  # no dangling nodes
    assert walk.truncated and walk.truncated_hops == ["out hop 1"]


def test_trace_terminal_sector_attributed_but_not_enqueued():
    edges = [
        _edge_row(SEED, DEXADDR, 1000),
        _edge_row(DEXADDR, HOP2, 999),  # must NOT be followed
        _edge_row(SEED, EXPLOITER, 500),
        _edge_row(EXPLOITER, HOP2, 400),  # normal expansion continues
    ]
    fetch_edges, fetch_labels = _adjacency_fetchers(
        edges, labels={DEXADDR: ("CowSwap", "DEX")}
    )
    walk = _trace(fetch_edges=fetch_edges, fetch_labels=fetch_labels)
    assert walk.nodes[DEXADDR]["sector"] == "DEX"  # attributed
    assert walk.nodes[HOP2]["rank"] == 2  # reached via EXPLOITER only
    assert f"flow:{DEXADDR}->{HOP2}:{TOKEN}" not in walk.edges


def test_token_contracts_are_marked_and_never_traversed():
    """A transfer INTO an ERC-20 contract is a deposit/burn, not a payment to a
    counterparty. It must be flagged AND terminal — walking through it would
    drag in every holder of that token (the 405-node explosion)."""
    edges = [
        _edge_row(SEED, TOKEN_CONTRACT, 750),   # seed -> ERC20 contract (deposit)
        _edge_row(TOKEN_CONTRACT, HOP2, 900),   # must NOT be followed
        _edge_row(SEED, EXPLOITER, 500),
        _edge_row(EXPLOITER, HOP2, 400),        # a normal address DOES expand
    ]
    fetch_edges, fetch_labels = _adjacency_fetchers(edges)
    walk = _trace(
        fetch_edges=fetch_edges,
        fetch_labels=fetch_labels,
        fetch_token_contracts=lambda ids: [[TOKEN_CONTRACT]] if TOKEN_CONTRACT in ids else [],
    )
    # Flagged as a contract…
    assert walk.nodes[TOKEN_CONTRACT]["is_token_contract"] is True
    # …reached at hop 1, but never expanded THROUGH.
    assert walk.nodes[TOKEN_CONTRACT]["rank"] == 1
    assert f"flow:{TOKEN_CONTRACT}->{HOP2}:{TOKEN}" not in walk.edges
    assert walk.hop_coverage[0]["shown_counterparties"] == 1
    assert walk.hop_coverage[0]["shown_contract_endpoint_edges"] == 1
    assert walk.edges[f"flow:{SEED}->{TOKEN_CONTRACT}:{TOKEN}"][
        "edge_class"
    ] == "contract_endpoint"
    # A normal address at the same hop still expands.
    assert walk.nodes[HOP2]["rank"] == 2


def test_trace_seed_with_terminal_sector_still_expands():
    fetch_edges, fetch_labels = _adjacency_fetchers(
        [_edge_row(DEXADDR, HOP2, 100)],
        labels={DEXADDR: ("CowSwap", "DEX")},
    )
    walk = _trace(seeds=[DEXADDR], hops=1, fetch_edges=fetch_edges,
                  fetch_labels=fetch_labels)
    assert walk.nodes[HOP2]["rank"] == 1  # seeds are always expandable


def test_trace_payments_sector_is_traversable():
    fetch_edges, fetch_labels = _adjacency_fetchers(
        [
            _edge_row(SEED, EXPLOITER, 1000),
            _edge_row(EXPLOITER, HOP2, 500),
        ],
        labels={EXPLOITER: ("Gnosis Pay", "Payments")},
    )
    walk = _trace(fetch_edges=fetch_edges, fetch_labels=fetch_labels)
    assert walk.nodes[HOP2]["rank"] == 2  # walked THROUGH the Payments node


def test_trace_bridge_edges_out_leg_terminal():
    def fetch_bridges(ids):
        if SEED in ids:
            return [[SEED, BRIDGE, TOKEN, "GNO", "gnosis-omnibridge",
                     123456, 4, "2026-06-03", "2026-06-10"]]
        return []

    fetch_edges, _ = _adjacency_fetchers([
        _edge_row(SEED, BRIDGE, 100),
        _edge_row(SEED, EXPLOITER, 50),
    ])
    walk = _trace(include_bridges=True, fetch_edges=fetch_edges,
                  fetch_bridges=fetch_bridges)
    assert walk.nodes[BRIDGE]["rank"] == 1
    assert walk.nodes[BRIDGE]["sector"] == "Bridges"  # terminal by class
    eid = f"flow:{SEED}->{BRIDGE}:{TOKEN}"
    assert walk.edges[eid]["amount_usd"] == 100
    assert walk.edges[eid]["edge_class"] == "bridge_attributed"
    assert not any(edge_id.startswith("bridge:") for edge_id in walk.edges)
    # Bridge endpoint never expanded (no fetch beyond it possible anyway).
    assert all(e["source"] != BRIDGE for e in walk.edges.values())


def test_structural_supply_endpoint_is_visible_terminal_not_counterparty():
    fetch_edges, _ = _adjacency_fetchers([
        _edge_row(SEED, ZERO_ADDRESS, 1_000),
        _edge_row(ZERO_ADDRESS, HOP2, 900),
        _edge_row(SEED, EXPLOITER, 500),
        _edge_row(EXPLOITER, HOP2, 400),
    ])
    walk = _trace(fetch_edges=fetch_edges, hops=2)

    assert walk.nodes[ZERO_ADDRESS]["structural_terminal"] is True
    assert walk.nodes[ZERO_ADDRESS]["sector"] == "Supply"
    assert walk.edges[f"flow:{SEED}->{ZERO_ADDRESS}:{TOKEN}"]["edge_class"] == "burn"
    assert f"flow:{ZERO_ADDRESS}->{HOP2}:{TOKEN}" not in walk.edges
    assert walk.hop_coverage[0]["shown_counterparties"] == 1
    assert walk.hop_coverage[0]["shown_supply_event_edges"] == 1


@pytest.mark.parametrize("address", [ZERO_ADDRESS, DEAD_ADDRESS])
def test_structural_supply_endpoint_cannot_be_seed(address):
    with pytest.raises(ValueError, match="mint/burn endpoint"):
        _trace(seeds=[address], hops=1, fetch_edges=lambda ids, leg: [])


def test_trace_merge_preserves_existing_ranks():
    existing = FlowWalk()
    existing.nodes = {SEED: {"rank": 0, "sector": "", "project": ""},
                      EXPLOITER: {"rank": 1, "sector": "", "project": ""}}
    fetch_edges, _ = _adjacency_fetchers([
        _edge_row(EXPLOITER, HOP2, 700),
        _edge_row(EXPLOITER, SEED, 600),  # existing node: rank untouched
    ])
    walk = _trace(seeds=[EXPLOITER], hops=1, fetch_edges=fetch_edges,
                  existing=existing)
    assert walk.nodes[SEED]["rank"] == 0  # NOT revised
    assert walk.nodes[EXPLOITER]["rank"] == 1  # merge seed keeps its rank
    assert walk.nodes[HOP2]["rank"] == 2  # parent rank + 1


# ---------------------------------------------------------------------------
# load_graph_flows tool
# ---------------------------------------------------------------------------


class FlowsStubCH(StubCH):
    """Answers flow/bridge/label/GP queries from adjacency lists; records
    evidence-query params so the t0/t1 plumbing is assertable."""

    def __init__(self, flow_edges=None, bridge_edges=None, labels=None,
                 canonical_pairs=None, refunded=None, evidence_rows=None,
                 token_contracts=None, bridge_quality=None, **kw):
        super().__init__(**kw)
        self.flow_edges = flow_edges or []
        self.bridge_edges = bridge_edges or []
        self.labels = labels or {}
        self.canonical_pairs = canonical_pairs or []
        self.refunded = refunded or []
        self.evidence_rows = evidence_rows or []
        self.token_contracts = token_contracts or []
        self.bridge_quality = {
            "rows_checked": 100,
            "blank_bridge_contract_rows": 0,
            "blank_bridge_name_rows": 0,
            "blank_user_address_rows": 0,
            "invalid_direction_rows": 0,
            "endpoint_ambiguity_rows": 0,
            "first_date": "2024-01-01",
            "last_date": "2026-07-18",
            **(bridge_quality or {}),
        }
        self.evidence_params: list[dict[str, Any]] = []
        self.flow_calls: list[dict[str, Any]] = []

    def run_query(self, sql, database="dbt", requested_max_rows=100,
                  audience="tool", fetch_mode="auto", parameters=None):
        params = parameters or {}

        def _result(cols, rows):
            return ExecutedQuery(sql, sql, database, cols, rows,
                                 len(rows), 0.0, "rows", [])

        if "AS source_horizon" in sql:
            return super().run_query(
                sql,
                database,
                requested_max_rows,
                audience,
                fetch_mode,
                parameters=parameters,
            )

        if "graph_explorer_bridge_safety_gate" in sql:
            columns = [
                "rows_checked",
                "blank_bridge_contract_rows",
                "blank_bridge_name_rows",
                "blank_user_address_rows",
                "invalid_direction_rows",
                "endpoint_ambiguity_rows",
                "first_date",
                "last_date",
            ]
            return _result(
                columns,
                [[self.bridge_quality[column] for column in columns]],
            )

        if TOKENS_META_RELATION in sql and "date_start <" in sql:
            tokens = sorted({str(row[2]) for row in self.flow_edges if row[2]})
            return _result(
                ["token_address", "symbol"],
                [[token, "GNO"] for token in tokens],
            )

        if "transaction_hash" in sql and "execution" in sql:
            # Per-edge evidence now reads the CHAIN — the daily aggregate has
            # no transaction_hash. Matched before the FLOWS_RELATION branches
            # because it no longer mentions that relation at all.
            self.evidence_params.append(dict(params))
            return _result(
                ["transaction_hash", "block_timestamp", "symbol", "amount",
                 "amount_usd"],
                self.evidence_rows,
            )
        if FLOWS_RELATION in sql and "token_contract" in sql:
            # Token-contract detection probe — which traced addresses are
            # themselves ERC-20 contracts. Not a flow-edge query.
            ids = params.get("ids", [])
            rows = [[a] for a in self.token_contracts if a in ids]
            return _result(["address"], rows)
        if FLOWS_RELATION in sql and "transaction_hash" in sql:
            self.evidence_params.append(dict(params))
            return _result(
                ["transaction_hash", "block_timestamp", "symbol", "amount",
                 "amount_usd"],
                self.evidence_rows,
            )
        if FLOWS_RELATION in sql and "total_counterparties" in sql:
            ids = params.get("ids", [])
            leg = "out" if "d.`from` IN" in sql else "in"
            col = 0 if leg == "out" else 1
            candidate_col = 1 if leg == "out" else 0
            candidate_rows = [r for r in self.flow_edges if r[col] in ids]
            minimum = float(params.get("min_usd", 0))
            rows = [
                r
                for r in candidate_rows
                if (r[5] is not None and float(r[5]) >= minimum)
                or r[5] is None
            ]
            candidates = {
                str(r[candidate_col])
                for r in rows
                if str(r[candidate_col]) not in {ZERO_ADDRESS, DEAD_ADDRESS}
                and str(r[candidate_col]) not in set(self.token_contracts)
            }
            supply_events = sum(
                1
                for r in rows
                if str(r[candidate_col]) in {ZERO_ADDRESS, DEAD_ADDRESS}
            )
            contract_endpoints = sum(
                1
                for r in rows
                if str(r[candidate_col]) in set(self.token_contracts)
            )
            known_usd = sum(float(r[5]) for r in rows if r[5] is not None)
            unknown = sum(
                1
                for r in rows
                if r[5] is None
                or (len(r) > 9 and int(r[9] or 0) > 0)
            )
            excluded_unknown = 0
            return _result(
                ["total_counterparties", "total_edges", "known_usd",
                 "total_usd", "unknown_usd_edges",
                 "excluded_unknown_usd_edges", "supply_event_edges",
                 "contract_endpoint_edges"],
                [[len(candidates), len(rows), known_usd,
                  known_usd if unknown == 0 and excluded_unknown == 0 else None,
                  unknown, excluded_unknown, supply_events,
                  contract_endpoints]],
            )
        if FLOWS_RELATION in sql:
            ids = params.get("ids", [])
            leg = "out" if "`from` IN" in sql else "in"
            self.flow_calls.append({"ids": sorted(ids), "leg": leg,
                                    "params": dict(params)})
            col = 0 if leg == "out" else 1
            minimum = float(params.get("min_usd", 0))
            rows = sorted(
                (
                    r
                    for r in self.flow_edges
                    if r[col] in ids
                    and (
                        (r[5] is not None and float(r[5]) >= minimum)
                        or r[5] is None
                    )
                ),
                key=lambda r: (
                    r[5] is None,
                    -(float(r[5]) if r[5] is not None else 0.0),
                ),
            )
            cols = ["source_id", "target_id", "token_address", "symbol",
                    "amount", "amount_usd", "transfer_count", "first_seen",
                    "last_seen", "unknown_usd_rows"]
            return _result(cols, rows)
        if BRIDGES_RELATION in sql:
            ids = params.get("ids", [])
            rows = [r for r in self.bridge_edges if r[0] in ids]
            return _result(
                ["source_id", "target_id", "token_address", "symbol",
                 "bridge_name", "amount_raw", "transfer_count", "first_seen",
                 "last_seen"],
                rows,
            )
        if "int_crawlers_data_labels" in sql:
            ids = params.get("ids", [])
            rows = [[i, self.labels[i][0], self.labels[i][1]]
                    for i in ids if i in self.labels]
            return _result(["address", "project", "sector"], rows)
        if "int_execution_gpay_safe_canonical" in sql:
            return _result(["old_safe", "new_safe"], self.canonical_pairs)
        if "int_execution_gpay_refunds" in sql:
            return _result(["new_safe"], [[a] for a in self.refunded])
        return super().run_query(sql, database, requested_max_rows, audience,
                                 fetch_mode, parameters=parameters)


@pytest.fixture
def flows_snapshot():
    snap = FakeSnapshot(
        models={
            "api_execution_circles_v2_trust_relations_current": _graph_model(
                "api_execution_circles_v2_trust_relations_current",
                profile="circles_trust",
                module="Circles",
                source_column="truster",
                target_column="trustee",
                source_kind="circles_avatar",
                target_kind="circles_avatar",
            ),
        },
    )
    from cerebro_mcp.semantic.graph_extraction import synthesize_search_documents

    profiles = tuple(graph_profiles.discover_profiles(models=snap.models))
    snap.graph_profiles = profiles
    snap.profiles_by_id = {p.profile: p for p in profiles}
    snap.kind_to_profiles = graph_profiles.build_kind_index(profiles)
    snap.graph_search_documents = tuple(synthesize_search_documents(profiles))
    with patch.object(graph_profiles, "semantic_runtime") as rt:
        rt.snapshot = snap
        yield snap


def _open(server) -> str:
    opened = _call_tool(server, "open_graph_explorer", {})
    return opened.structuredContent["view_id"]


def _default_ch() -> FlowsStubCH:
    return FlowsStubCH(
        flow_edges=[
            _edge_row(SEED, EXPLOITER, 1000),
            _edge_row(EXPLOITER, HOP2, 400),
            _edge_row(FUNDER, SEED, 30),
        ],
        labels={EXPLOITER: ("", "")},
        canonical_pairs=[[SEED, HOP2]],
        refunded=[HOP2],
    )


@pytest.mark.parametrize(
    "violation",
    [
        "blank_bridge_contract_rows",
        "blank_bridge_name_rows",
        "blank_user_address_rows",
        "invalid_direction_rows",
        "endpoint_ambiguity_rows",
    ],
)
def test_bridge_relation_safety_gate_rejects_each_quality_violation(violation):
    gate = validate_bridge_relation_safety(
        FlowsStubCH(bridge_quality={violation: 1})
    )

    assert gate["ok"] is False
    assert gate["status"] == "failed"
    assert gate[violation] == 1
    assert f"{violation}=1" in gate["error"]


def test_dirty_bridge_relation_disables_enrichment_but_keeps_transfers(
    flows_snapshot,
):
    ch = FlowsStubCH(
        flow_edges=[_edge_row(SEED, BRIDGE, 250)],
        bridge_edges=[
            [
                SEED,
                BRIDGE,
                TOKEN,
                "GNO",
                "gnosis-omnibridge",
                123456,
                4,
                "2026-06-03",
                "2026-06-10",
            ]
        ],
        bridge_quality={"blank_bridge_name_rows": 7},
    )
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {
            "view_id": view_id,
            "seed_node_ids": [SEED],
            "hops": 1,
            "t0": T0,
            "t1": T1,
            "include_bridges": True,
        },
    )

    state = result.structuredContent["view_state"]["flows"]
    scope = state["scope"]
    edges = result.structuredContent["datasets"]["flow_edges"]["preview_rows"]
    bridge_source = next(
        source
        for source in scope["sources"]
        if source["name"] == f"dbt.{BRIDGES_RELATION}"
    )

    assert len(edges) == 1
    assert edges[0][3] == "transfer"
    assert scope["status"] == "partial"
    assert scope["sources"][0]["status"] == "ok"
    assert bridge_source["status"] == "error"
    assert "blank_bridge_name_rows=7" in bridge_source["error"]
    assert scope["bridge_enrichment"]["requested"] is True
    assert scope["bridge_enrichment"]["enabled"] is False
    assert scope["bridge_enrichment"]["quality_gate"][
        "validation_basis"
    ] == "bounded_full_relation_scan"
    assert any(
        "primary transfer evidence remains usable" in warning
        for warning in scope["warnings"]
    )


def test_unprovable_bridge_gate_is_partial_not_verified_empty(flows_snapshot):
    class BridgeGateTimeoutCH(FlowsStubCH):
        def run_query(self, sql, database="dbt", requested_max_rows=100,
                      audience="tool", fetch_mode="auto", parameters=None):
            if "graph_explorer_bridge_safety_gate" in sql:
                raise RuntimeError("bridge quality probe timed out")
            return super().run_query(
                sql,
                database,
                requested_max_rows,
                audience,
                fetch_mode,
                parameters,
            )

    server = _server(BridgeGateTimeoutCH())
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {
            "view_id": view_id,
            "seed_node_ids": [SEED],
            "hops": 1,
            "t0": T0,
            "t1": T1,
            "include_bridges": True,
        },
    )
    scope = result.structuredContent["view_state"]["flows"]["scope"]

    assert scope["status"] == "partial"
    assert scope["coverage"]["rows"] == {"shown": 0, "total": 0}
    assert scope["verification"]["status"] == "verified"
    assert scope["bridge_enrichment"]["enabled"] is False
    assert "could not be proven" in scope["bridge_enrichment"]["error"]
    assert result.structuredContent["datasets"]["flow_edges"][
        "preview_rows"
    ] == []


def test_clean_bridge_gate_allows_matching_annotation(flows_snapshot):
    ch = FlowsStubCH(
        flow_edges=[_edge_row(SEED, BRIDGE, 250)],
        bridge_edges=[
            [
                SEED,
                BRIDGE,
                TOKEN,
                "GNO",
                "gnosis-omnibridge",
                123456,
                4,
                "2026-06-03",
                "2026-06-10",
            ]
        ],
    )
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {
            "view_id": view_id,
            "seed_node_ids": [SEED],
            "hops": 1,
            "t0": T0,
            "t1": T1,
            "include_bridges": True,
        },
    )

    scope = result.structuredContent["view_state"]["flows"]["scope"]
    edges = result.structuredContent["datasets"]["flow_edges"]["preview_rows"]
    assert edges[0][3] == "bridge_attributed"
    assert scope["bridge_enrichment"]["enabled"] is True
    assert scope["bridge_enrichment"]["quality_gate"]["status"] == "verified"


def test_load_graph_flows_replace(flows_snapshot):
    ch = _default_ch()
    server = _server(ch)
    view_id = _open(server)
    before = dict(mini_apps.get_view(view_id).dataset_revisions)

    result = _call_tool(
        server,
        "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": ["0x" + SEED[2:].upper()],  # checksum-style, normalized
         "direction": "out", "hops": 2, "t0": T0, "t1": T1},
    )
    assert result.isError is not True
    sc = result.structuredContent
    vs = sc["view_state"]
    # The data loader must NOT touch mode/selection/mode_revision — those are
    # owned by explicit mode commands. The view was opened in atlas; it stays
    # atlas (the client flips the tab locally + via the focus tool).
    assert vs["mode"] == "atlas"
    assert vs.get("mode_revision", 0) == 0
    fl = vs["flows"]
    assert fl["seeds"] == [SEED]
    assert fl["t0"] == T0 and fl["t1"] == T1
    assert fl["node_count"] == 3 and fl["edge_count"] == 2
    assert fl["expanded"] == {}
    assert fl["token_catalog"][0]["symbol"] == "GNO"
    assert fl["scope"]["sources"][0]["name"] == f"dbt.{FLOWS_RELATION}"
    assert fl["scope"]["sources"][0]["horizon"] == "2026-07-18T00:00:00Z"
    metadata_source = next(
        source
        for source in fl["scope"]["sources"]
        if source["name"] == f"dbt.{TOKENS_META_RELATION}"
    )
    assert metadata_source["horizon_basis"] == (
        "system.tables.metadata_modification_time"
    )
    assert "row-level event freshness is unavailable" in metadata_source[
        "freshness_note"
    ]
    assert fl["scope"]["data_horizon"] == "2026-07-18T00:00:00Z"
    assert fl["scope"]["result_observed_through"] == "2026-06-20 10:00:00"
    assert fl["scope"]["query_kind"] == "money_trail"
    assert fl["scope"]["evidence_class"] == "aggregate_transfer_adjacency"
    assert fl["scope"]["predicate"]["subjects"] == [SEED]
    assert fl["scope"]["token_universe"]["addresses"] == [TOKEN]
    assert fl["scope"]["token_universe"]["count"] == 1
    assert len(fl["scope"]["token_universe"]["sha256"]) == 64
    assert fl["scope"]["coverage"]["rows"]["total"] == 2
    assert fl["scope"]["coverage"]["usd"]["unknown_rows"] == 0
    trace_coverage = fl["scope"]["truncation_coverage"]
    assert trace_coverage["exact"] is True
    assert trace_coverage["shown_counterparties"] == 2
    assert trace_coverage["total_counterparties"] == 2
    assert trace_coverage["retained_usd_fraction"] == 1.0
    assert len(trace_coverage["by_hop"]) == 2
    assert vs["selection"] == {
        "node_id": "", "edge_id": "", "request_id": 0,
    }  # preserved, not set

    nodes = {r[0]: r for r in sc["datasets"]["flow_nodes"]["preview_rows"]}
    assert nodes[SEED][4] == 0 and nodes[EXPLOITER][4] == 1 and nodes[HOP2][4] == 2
    # Aggregates over the LOADED edge set.
    assert nodes[EXPLOITER][5] == 1000.0  # in_usd
    assert nodes[EXPLOITER][6] == 400.0   # out_usd
    # GP flags: SEED is an old safe, HOP2 its canonical + refunded.
    assert nodes[SEED][9] == ["old_safe"]
    assert sorted(nodes[HOP2][9]) == ["new_safe", "refunded_safe"]
    # FUNDER not present (out-only trace).
    assert FUNDER not in nodes

    # Per-key attach: investigate/atlas revisions untouched.
    after = mini_apps.get_view(view_id).dataset_revisions
    for key in ("nodes", "edges", "atlas_nodes", "atlas_edges"):
        assert after.get(key, 0) == before.get(key, 0), key
    assert after["flow_nodes"] > before.get("flow_nodes", 0)
    assert after["flow_edges"] > before.get("flow_edges", 0)


def test_load_graph_flows_direction_in(flows_snapshot):
    ch = _default_ch()
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "direction": "in",
         "hops": 1, "t0": T0, "t1": T1},
    )
    nodes = {r[0]: r for r in
             result.structuredContent["datasets"]["flow_nodes"]["preview_rows"]}
    assert nodes[FUNDER][4] == -1  # upstream = negative rank
    assert EXPLOITER not in nodes
    # Bridges are out-leg only — no bridge query on a pure in trace.
    assert all(c["leg"] == "in" for c in ch.flow_calls)


def test_zero_minimum_keeps_wholly_unpriced_flow_group(flows_snapshot):
    unpriced = [
        SEED, FUNDER, TOKEN, "UNK", 3.0, None, 2,
        "2026-06-02", "2026-06-20",
    ]
    ch = FlowsStubCH(flow_edges=[unpriced])
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {
            "view_id": view_id,
            "seed_node_ids": [SEED],
            "hops": 1,
            "t0": T0,
            "t1": T1,
            "min_usd": 0,
            "include_bridges": False,
        },
    )

    edges = result.structuredContent["datasets"]["flow_edges"]["preview_rows"]
    assert len(edges) == 1
    assert edges[0][7] is None
    scope = result.structuredContent["view_state"]["flows"]["scope"]
    assert scope["coverage"]["usd"]["total"] is None
    assert scope["coverage"]["usd"]["unknown_rows"] == 1
    assert scope["usd_filter_coverage"] == {
        "min_usd": 0.0,
        "eligibility": "priced_at_or_above_minimum_or_unpriced",
        "excluded_unknown_usd_edges": 0,
        "eligible_unknown_usd_edges": 1,
        "counting_basis": "sum_of_per_hop_aggregated_edges",
    }


def test_positive_minimum_retains_unpriced_flow_group_in_separate_lane(flows_snapshot):
    unpriced = [
        SEED, FUNDER, TOKEN, "UNK", 3.0, None, 2,
        "2026-06-02", "2026-06-20",
    ]
    ch = FlowsStubCH(flow_edges=[_edge_row(SEED, EXPLOITER, 100), unpriced])
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {
            "view_id": view_id,
            "seed_node_ids": [SEED],
            "hops": 1,
            "t0": T0,
            "t1": T1,
            "min_usd": 10,
            "include_bridges": False,
        },
    )

    scope = result.structuredContent["view_state"]["flows"]["scope"]
    edges = result.structuredContent["datasets"]["flow_edges"]["preview_rows"]
    assert len(edges) == 2
    assert any(edge[7] is None for edge in edges)
    assert scope["status"] == "partial"
    assert scope["truncation"]["truncated"] is False
    assert scope["coverage"]["usd"]["total"] is None
    assert scope["usd_filter_coverage"]["excluded_unknown_usd_edges"] == 0
    assert scope["truncation_coverage"]["excluded_unknown_usd_edges"] == 0
    assert any(
        "remain available in the unpriced lane" in warning
        for warning in scope["warnings"]
    )


def test_mixed_price_flow_publishes_known_subtotal_and_unknown_coverage(
    flows_snapshot,
):
    mixed = [
        SEED, FUNDER, TOKEN, "MIX", 3.0, 125.0, 2,
        "2026-06-02", "2026-06-20", 2,
    ]
    ch = FlowsStubCH(flow_edges=[mixed])
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {
            "view_id": view_id,
            "seed_node_ids": [SEED],
            "hops": 1,
            "t0": T0,
            "t1": T1,
            "min_usd": 0,
            "include_bridges": False,
        },
    )

    edge = result.structuredContent["datasets"]["flow_edges"]["preview_rows"][0]
    assert edge[7] == 125.0
    assert edge[11] == 2
    scope = result.structuredContent["view_state"]["flows"]["scope"]
    assert scope["status"] == "partial"
    # Price incompleteness does not erase independently known structural
    # coverage for the rows/nodes/edges that were actually admitted.
    assert scope["coverage"]["rows"] == {"shown": 1, "total": 1}
    assert scope["coverage"]["nodes"] == {"shown": 2, "total": 2}
    assert scope["coverage"]["edges"] == {"shown": 1, "total": 1}
    assert scope["verification"]["status"] == "verified"
    assert scope["coverage"]["usd"] == {
        "known": 125.0,
        "total": None,
        "unknown_rows": 2,
    }
    assert scope["usd_filter_coverage"]["eligible_unknown_usd_edges"] == 1
    nodes = {
        row[0]: row
        for row in result.structuredContent["datasets"]["flow_nodes"]["preview_rows"]
    }
    assert nodes[SEED][6] is None
    assert nodes[FUNDER][5] is None


def test_load_graph_flows_merge_expands_with_view_filters(flows_snapshot):
    ch = _default_ch()
    server = _server(ch)
    view_id = _open(server)
    _call_tool(
        server,
        "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "direction": "out",
         "hops": 1, "t0": T0, "t1": T1, "min_usd": 5.0},
    )
    ch.flow_calls.clear()
    result = _call_tool(
        server,
        "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [EXPLOITER], "direction": "out",
         "hops": 1, "merge": True, "min_usd": 999.0},  # conflicting arg
    )
    assert result.isError is not True
    vs = result.structuredContent["view_state"]
    fl = vs["flows"]
    # Filters came from the VIEW, not the conflicting arg…
    assert fl["min_usd"] == 5.0
    assert any("ignored explicit" in w for w in vs["warnings"])
    assert all(c["params"]["min_usd"] == 5.0 for c in ch.flow_calls)
    # …ranks preserved, new neighbor extends from the expanded node's rank…
    nodes = {r[0]: r for r in
             result.structuredContent["datasets"]["flow_nodes"]["preview_rows"]}
    assert nodes[SEED][4] == 0 and nodes[EXPLOITER][4] == 1
    assert nodes[HOP2][4] == 2
    # …and the trace is book-kept.
    assert fl["expanded"] == {EXPLOITER: ["out"]}
    assert fl["seeds"] == [SEED, EXPLOITER]
    # Merge restores prior companion-query coverage before appending the new
    # expansion. The totals therefore describe the merged graph, not only the
    # most recent delta.
    coverage = fl["scope"]["truncation_coverage"]
    assert len(coverage["by_hop"]) == 2
    assert coverage["shown_counterparties"] == 2
    assert coverage["total_counterparties"] == 2
    assert coverage["shown_usd"] == 1400.0
    assert coverage["total_usd"] == 1400.0


def test_load_graph_flows_merge_requires_node_on_graph(flows_snapshot):
    ch = _default_ch()
    server = _server(ch)
    view_id = _open(server)
    _call_tool(
        server,
        "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "direction": "out",
         "hops": 1, "t0": T0, "t1": T1},
    )
    result = _call_tool(
        server,
        "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [FUNDER], "merge": True},
    )
    assert result.isError is True
    assert "not on the flow graph" in result.content[0].text


def test_load_graph_flows_errors_and_clamps(flows_snapshot, monkeypatch):
    import cerebro_mcp.tools.semantic.graph_explorer as ge

    ch = _default_ch()
    server = _server(ch)
    view_id = _open(server)

    empty = _call_tool(server, "load_graph_flows",
                       {"view_id": view_id, "seed_node_ids": []})
    assert empty.isError is True and "seed" in empty.content[0].text

    bad_dir = _call_tool(
        server, "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "direction": "sideways"},
    )
    assert bad_dir.isError is True and "direction" in bad_dir.content[0].text

    monkeypatch.setattr(ge.constants, "FLOWS_MAX_SEEDS", 1)
    too_many = _call_tool(
        server, "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED, EXPLOITER]},
    )
    assert too_many.isError is True and "Too many seeds" in too_many.content[0].text

    clamped = _call_tool(
        server, "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "hops": 99,
         "t0": T0, "t1": T1},
    )
    fl = clamped.structuredContent["view_state"]["flows"]
    assert fl["hops"] == ge.constants.FLOWS_MAX_HOPS

    missing = _call_tool(server, "load_graph_flows",
                         {"view_id": "nope", "seed_node_ids": [SEED]})
    assert missing.isError is True


def test_load_graph_flows_truncation_warns(flows_snapshot, monkeypatch):
    import cerebro_mcp.tools.semantic.graph_explorer as ge

    monkeypatch.setattr(ge.constants, "FLOWS_PER_HOP_NODE_BUDGET", 1)
    ch = _default_ch()
    ch.flow_edges.append(_edge_row(SEED, FUNDER, 900))
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server, "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "hops": 1,
         "t0": T0, "t1": T1},
    )
    vs = result.structuredContent["view_state"]
    assert vs["flows"]["truncated"] is True
    assert vs["flows"]["truncated_hops"] == ["out hop 1"]
    assert any("truncated" in w.lower() for w in vs["warnings"])
    nodes = {r[0] for r in
             result.structuredContent["datasets"]["flow_nodes"]["preview_rows"]}
    assert EXPLOITER in nodes and FUNDER not in nodes  # USD-desc admission
    coverage = vs["flows"]["scope"]["truncation_coverage"]
    assert coverage["budget_per_hop"] == 1
    assert coverage["counting_basis"] == "sum_of_per_hop_unique_counterparties"
    assert coverage["shown_counterparties"] == 1
    assert coverage["total_counterparties"] == 2
    assert coverage["dropped_counterparties"] == 1
    assert coverage["shown_usd"] == 1000.0
    assert coverage["total_usd"] == 1900.0
    assert coverage["retained_usd_fraction"] == pytest.approx(1000 / 1900)
    assert coverage["by_hop"][0]["direction"] == "out"
    assert coverage["by_hop"][0]["exact"] is True


def test_unmatched_bridge_row_does_not_create_a_second_movement(flows_snapshot):
    ch = FlowsStubCH(
        bridge_edges=[
            [SEED, BRIDGE, TOKEN, "GNO", "gnosis-omnibridge",
             123456, 4, "2026-06-03", "2026-06-10"],
        ]
    )
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "hops": 1,
         "t0": T0, "t1": T1, "include_bridges": True},
    )
    rows = {
        row[0]: row
        for row in result.structuredContent["datasets"]["flow_nodes"]["preview_rows"]
    }
    assert set(rows) == {SEED}
    assert rows[SEED][6] == 0.0
    assert result.structuredContent["datasets"]["flow_edges"]["preview_rows"] == []
    assert any(
        "no matching admitted transfer" in warning
        for warning in result.structuredContent["view_state"]["flows"]["scope"]["warnings"]
    )
    assert rows[SEED][5] == 0.0  # no incoming leg is a verified zero


def test_missing_flow_relation_publishes_failed_scope(flows_snapshot):
    class MissingFlowCH(FlowsStubCH):
        def run_query(self, sql, database="dbt", requested_max_rows=100,
                      audience="tool", fetch_mode="auto", parameters=None):
            params = parameters or {}
            if "FROM system.columns" in sql and params.get("table") == FLOWS_RELATION:
                return ExecutedQuery(
                    sql, sql, database, ["name", "type"], [], 0, 0.0, "rows", []
                )
            return super().run_query(
                sql, database, requested_max_rows, audience, fetch_mode, parameters
            )

    ch = MissingFlowCH()
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "t0": T0, "t1": T1},
    )
    scope = result.structuredContent["view_state"]["flows"]["scope"]
    assert scope["status"] == "failed"
    assert scope["coverage"]["rows"]["total"] is None
    assert scope["sources"][0]["status"] == "error"
    assert result.structuredContent["datasets"]["flow_edges"]["preview_rows"] == []


def test_missing_flow_enrichment_relation_is_not_reported_ok(flows_snapshot):
    class MissingPricesCH(FlowsStubCH):
        def run_query(self, sql, database="dbt", requested_max_rows=100,
                      audience="tool", fetch_mode="auto", parameters=None):
            params = parameters or {}
            if (
                "FROM system.columns" in sql
                and params.get("table") == "int_execution_token_prices_daily"
            ):
                return ExecutedQuery(
                    sql, sql, database, ["name", "type"], [], 0, 0.0, "rows", []
                )
            return super().run_query(
                sql, database, requested_max_rows, audience, fetch_mode, parameters
            )

    ch = MissingPricesCH()
    server = _server(ch)
    view_id = _open(server)
    result = _call_tool(
        server,
        "load_graph_flows",
        {
            "view_id": view_id,
            "seed_node_ids": [SEED],
            "t0": T0,
            "t1": T1,
        },
    )
    scope = result.structuredContent["view_state"]["flows"]["scope"]
    price_source = next(
        source
        for source in scope["sources"]
        if source["name"].endswith("int_execution_token_prices_daily")
    )
    # Prices are optional enrichment. Their absence must downgrade the scope
    # without erasing primary transfer evidence or claiming a ready result.
    assert scope["status"] == "partial"
    assert price_source["status"] == "error"


def test_flow_evidence_uses_view_range(flows_snapshot):
    ch = _default_ch()
    ch.evidence_rows = [
        ["0xhash1", "2026-06-05 12:00:00", "GNO", 2.5, 330.26],
    ]
    server = _server(ch)
    view_id = _open(server)
    _call_tool(
        server, "load_graph_flows",
        {"view_id": view_id, "seed_node_ids": [SEED], "hops": 1,
         "t0": T0, "t1": T1},
    )
    eid = f"flow:{SEED}->{EXPLOITER}:{TOKEN}"
    result = _call_tool(
        server, "update_graph_explorer_focus",
        {"view_id": view_id, "selected_edge_id": eid},
    )
    assert result.isError is not True
    # The evidence query carried the VIEW's traced range.
    assert ch.evidence_params
    assert ch.evidence_params[-1]["t0"] == T0
    assert ch.evidence_params[-1]["t1"] == T1
    # Focus returns a PATCH payload; refreshed evidence rides under patch.datasets.
    patch_block = result.structuredContent["patch"]
    rows = patch_block["datasets"]["edge_evidence"]["preview_rows"]
    assert any(r[1] == "transaction_hash" and r[2] == "0xhash1" for r in rows)
    focus_request_id = patch_block["view_state"]["selection"]["request_id"]
    assert focus_request_id > 0
    assert all(r[3] == "edge" and r[4] == focus_request_id for r in rows)
    ts = [r for r in rows if r[1] == "block_timestamp"]
    assert ts and ts[0][2] == "2026-06-05 12:00:00"  # tx-level, not daily


def test_patch_schema_accepts_flow_knobs_rejects_server_owned(flows_snapshot):
    ch = _default_ch()
    server = _server(ch)
    view_id = _open(server)
    ok = _call_tool(
        server, "set_graph_explorer_view",
        {"view_id": view_id,
         "patch": {"flows": {"direction": "both", "min_usd": 100.0,
                             "tokens": [TOKEN], "hops": 3}}},
    )
    assert ok.isError is not True
    rejected = _call_tool(
        server, "set_graph_explorer_view",
        {"view_id": view_id, "patch": {"flows": {"seeds": [SEED]}}},
    )
    assert rejected.isError is True
    mode_ok = _call_tool(
        server, "update_graph_explorer_focus",
        {"view_id": view_id, "mode": "flows"},
    )
    assert mode_ok.isError is not True


def test_open_publishes_flow_limits_and_empty_datasets(flows_snapshot):
    ch = _default_ch()
    server = _server(ch)
    opened = _call_tool(server, "open_graph_explorer", {})
    sc = opened.structuredContent
    limits = sc["view_state"]["limits"]
    for key in ("flows_default_hops", "flows_max_hops", "flows_default_min_usd",
                "flows_default_range_days", "flows_max_edges"):
        assert key in limits, key
    assert sc["view_state"]["flows"]["direction"] == "out"
    ds = sc["datasets"]
    assert [c["name"] for c in ds["flow_nodes"]["columns"]][:5] == [
        "id", "label", "sector", "project", "hop_rank",
    ]
    assert ds["flow_edges"]["preview_rows"] == []


def test_security_classification():
    from cerebro_mcp.security import RiskClass, TOOL_RISK_REGISTRY

    assert TOOL_RISK_REGISTRY["load_graph_flows"] == frozenset(
        {RiskClass.APP_ONLY}
    )
