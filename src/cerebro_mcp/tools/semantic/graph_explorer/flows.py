"""Money Trail — bounded aggregate transfer-adjacency evidence (app-only).

``load_graph_flows`` traces value movements hop by hop from seed addresses
over daily-grain whitelisted transfers (USD-weighted, token-level), with
optional bridge attribution, per-hop labels, and GP-case flags. Results land
in ``flow_nodes`` / ``flow_edges``; the frontend renders an evidence table and
a segmented Sankey-style map. Distinct hops are observations of adjacency,
not proof that identical fungible units continued through an intermediary.

Traversal rules (pinned):
  * hop_rank: negative = upstream (funders), 0 = seed, positive = downstream;
    assigned ONCE at first discovery (rank = parent rank ± 1), never revised.
  * "both" runs the two legs interleaved hop-by-hop (out first), so a node
    reachable from both sides gets its minimal |rank| with an out-bias tie.
  * Per-hop node budget with USD-descending admission; edges whose new
    endpoint was refused are DROPPED (no dangling nodes).
  * Zero/dead supply endpoints are retained as mint/burn evidence but excluded
    from counterparties and never enqueued as a frontier.
  * Terminal sectors ({Bridges, DEX, Privacy}) are label-checked PER HOP
    before the next frontier is built — attributed but never enqueued.
    Payments stays traversable; seeds are always expandable; a per-node
    Trace (merge=True) overrides terminal status.
  * merge=True takes every filter from ``view_state.flows`` (explicitly
    passed conflicting values are ignored with a warning) — a merge can
    never mix filter regimes.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.semantic.address_semantics import (
    is_structural_terminal,
    money_event_kind,
    structural_terminal_label,
)
from cerebro_mcp.semantic.flow_queries import (
    BRIDGES_RELATION,
    FLOWS_RELATION,
    PRICES_RELATION,
    TOKENS_META_RELATION,
    build_active_token_universe_sql,
    build_bridge_flows_sql,
    build_bridge_safety_gate_sql,
    build_flows_coverage_sql,
    build_flow_labels_sql,
    build_flows_sql,
    build_gp_flags_sqls,
    build_token_contract_sql,
    flow_edge_id,
)
from cerebro_mcp.tools.visualization import mini_apps

from . import constants
from .forensics import (
    forensic_scope,
    new_scope_id,
    source_record,
    validate_source_contract,
)
from .state import build_payload, dataset_from_rows, short_id
from .ui_tools import _normalize_node_id

logger = logging.getLogger(__name__)

TOKEN_CATALOG_CAP = 40

_BRIDGE_GATE_FIELDS = (
    "blank_bridge_contract_rows",
    "blank_bridge_name_rows",
    "blank_user_address_rows",
    "invalid_direction_rows",
    "endpoint_ambiguity_rows",
)


@dataclass
class FlowWalk:
    """Accumulated trace result."""

    # node_id -> {"rank": int, "sector": str, "project": str}
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    # edge_id -> row dict (source/target/edge_class/token/symbol/amount/...)
    edges: dict[str, dict[str, Any]] = field(default_factory=dict)
    truncated_hops: list[str] = field(default_factory=list)
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    source_failures: list[str] = field(default_factory=list)
    # Exact companion-query coverage for each directional hop.  Counts are
    # per-hop unique counterparties (and may therefore repeat across hops);
    # this explicit basis is safer than presenting their sum as global unique
    # addresses.
    hop_coverage: list[dict[str, Any]] = field(default_factory=list)


def _run_query(ch: ClickHouseManager, sql: str, params: dict[str, Any], limit: int):
    return mini_apps.run_structured_query(
        ch, sql, database="dbt", parameters=params, requested_max_rows=limit + 1
    )


def validate_bridge_relation_safety(ch: ClickHouseManager) -> dict[str, Any]:
    """Prove the deployed bridge relation clean before using enrichment.

    This gate is intentionally fail-closed.  It is an optional-enrichment
    check, not a primary-transfer check: callers disable bridge annotations on
    any malformed result, timeout, or query exception while retaining the
    independently sourced transfer graph.
    """
    sql, params = build_bridge_safety_gate_sql()
    base: dict[str, Any] = {
        "ok": False,
        "status": "failed",
        "validation_basis": "bounded_full_relation_scan",
        "rows_checked": None,
        **{field: None for field in _BRIDGE_GATE_FIELDS},
        "first_date": None,
        "last_date": None,
    }
    try:
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database="dbt",
            parameters=params,
            requested_max_rows=1,
        )
        if len(result.rows) != 1 or len(result.rows[0]) < 8:
            raise RuntimeError(
                "bridge quality query did not return its complete aggregate row"
            )
        row = result.rows[0]
        rows_checked = int(row[0] or 0)
        counts = {
            field: int(row[index] or 0)
            for index, field in enumerate(_BRIDGE_GATE_FIELDS, start=1)
        }
        base.update(
            {
                "rows_checked": rows_checked,
                **counts,
                "first_date": None if row[6] is None else str(row[6]),
                "last_date": None if row[7] is None else str(row[7]),
            }
        )
        failures = [
            f"{field}={value}"
            for field, value in counts.items()
            if value > 0
        ]
        if rows_checked <= 0:
            failures.insert(0, "rows_checked=0")
        if failures:
            base["error"] = (
                "bridge attribution disabled: deployed relation failed the "
                "full-relation safety gate (" + ", ".join(failures) + ")"
            )
            return base
        base.update({"ok": True, "status": "verified"})
        return base
    except Exception as exc:
        base["error"] = (
            "bridge attribution disabled: full-relation safety could not be "
            f"proven ({exc})"
        )
        return base


def flows_trace(
    ch: ClickHouseManager,
    *,
    seeds: list[str],
    direction: str,  # "out" | "in" | "both"
    hops: int,
    t0: str,
    t1: str,
    min_usd: float,
    tokens: list[str] | None,
    include_bridges: bool,
    per_hop_budget: int,
    node_cap: int,
    edge_cap: int,
    per_query_limit: int,
    terminal_sectors: frozenset[str] | None = None,
    existing: FlowWalk | None = None,
    fetch_edges: Callable[..., Any] | None = None,
    fetch_coverage: Callable[..., Any] | None = None,
    fetch_bridges: Callable[..., Any] | None = None,
    fetch_labels: Callable[..., Any] | None = None,
    fetch_token_contracts: Callable[..., Any] | None = None,
) -> FlowWalk:
    """Directional hop-by-hop trace. Injectable fetchers for tests."""
    terminal = (
        constants.FLOWS_TERMINAL_SECTORS
        if terminal_sectors is None
        else terminal_sectors
    )

    custom_fetch_edges = fetch_edges is not None

    def default_fetch_edges(ids: list[str], leg: str):
        sql, params = build_flows_sql(
            frontier_ids=ids,
            direction=leg,
            t0=t0,
            t1_exclusive=t1,
            min_usd=min_usd,
            tokens=tokens,
            limit=per_query_limit,
        )
        return _run_query(ch, sql, params, per_query_limit).rows

    def default_fetch_coverage(ids: list[str], leg: str):
        sql, params = build_flows_coverage_sql(
            frontier_ids=ids,
            direction=leg,
            t0=t0,
            t1_exclusive=t1,
            min_usd=min_usd,
            tokens=tokens,
        )
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database="dbt",
            parameters=params,
            requested_max_rows=2,
        )
        if not result.rows:
            return None
        row = result.rows[0]
        return {
            "total_counterparties": int(row[0] or 0),
            "total_edges": int(row[1] or 0),
            "known_usd": float(row[2] or 0),
            "total_usd": None if row[3] is None else float(row[3]),
            "unknown_usd_edges": int(row[4] or 0),
            "excluded_unknown_usd_edges": (
                int(row[5] or 0) if len(row) > 5 else 0
            ),
            "supply_event_edges": int(row[6] or 0) if len(row) > 6 else 0,
            "contract_endpoint_edges": int(row[7] or 0) if len(row) > 7 else 0,
            "exact": True,
        }

    def default_fetch_bridges(ids: list[str]):
        sql, params = build_bridge_flows_sql(
            frontier_ids=ids, t0=t0, t1_exclusive=t1, limit=per_query_limit
        )
        return _run_query(ch, sql, params, per_query_limit).rows

    def default_fetch_labels(ids: list[str]):
        sql, params = build_flow_labels_sql(ids)
        return _run_query(ch, sql, params, len(ids)).rows

    def default_fetch_token_contracts(ids: list[str]):
        sql, params = build_token_contract_sql(ids)
        return _run_query(ch, sql, params, len(ids)).rows

    fetch_edges = fetch_edges or default_fetch_edges
    fetch_coverage = fetch_coverage or (
        None if custom_fetch_edges else default_fetch_coverage
    )
    fetch_bridges = fetch_bridges or default_fetch_bridges
    fetch_labels = fetch_labels or default_fetch_labels
    fetch_token_contracts = fetch_token_contracts or default_fetch_token_contracts

    walk = existing or FlowWalk()
    token_contract_cache: set[str] = {
        node_id
        for node_id, meta in walk.nodes.items()
        if meta.get("is_token_contract")
    }
    token_contract_checked: set[str] = set(token_contract_cache)

    def detect_token_contracts(ids: list[str]) -> set[str]:
        pending = [
            node_id
            for node_id in ids
            if node_id not in token_contract_checked
            and not is_structural_terminal(node_id)
        ]
        if pending:
            try:
                rows = fetch_token_contracts(pending)
                token_contract_cache.update(str(row[0]) for row in rows)
                token_contract_checked.update(pending)
            except Exception as exc:
                walk.warnings.append(f"token-contract lookup failed: {exc}")
        return {node_id for node_id in ids if node_id in token_contract_cache}

    def label_nodes(ids: list[str]) -> None:
        """Attribute a hop's newly-admitted nodes BEFORE the next frontier is
        built: project/sector labels AND whether the address is itself an
        ERC-20 contract (a deposit/burn target, not a counterparty)."""
        fresh = [
            i
            for i in ids
            if i in walk.nodes
            and "sector" not in walk.nodes[i]
            and not is_structural_terminal(i)
        ]
        if not fresh:
            return
        try:
            rows = fetch_labels(fresh)
        except Exception as exc:  # degrade, never abort the walk
            walk.warnings.append(f"label lookup failed: {exc}")
            rows = []
        by_id = {str(r[0]): (str(r[1] or ""), str(r[2] or "")) for r in rows}
        # Token-contract detection — a transfer INTO one of these is a protocol
        # interaction (deposit / burn / redeem). Tracing through it would drag
        # in every holder of that token, so it is marked and made terminal.
        token_contracts = detect_token_contracts(fresh)
        for nid in fresh:
            project, sector = by_id.get(nid, ("", ""))
            walk.nodes[nid]["project"] = project
            walk.nodes[nid]["sector"] = sector
            if nid in token_contracts:
                walk.nodes[nid]["is_token_contract"] = True

    # Seeds: rank 0 unless already ranked (merge keeps existing ranks — the
    # expanded node's own rank is the base for its new neighbors).
    seed_set = set()
    for s in seeds:
        if is_structural_terminal(s):
            raise ValueError(
                f"{structural_terminal_label(s)} is a mint/burn endpoint, "
                "not an investigative seed."
            )
        seed_set.add(s)
        if s not in walk.nodes:
            walk.nodes[s] = {"rank": 0}
    label_nodes(list(seed_set))

    def run_leg(leg: str, frontier: list[str], hop: int) -> list[str]:
        """One hop of one leg; returns the newly admitted, enqueueable ids."""
        if not frontier:
            return []
        try:
            rows = fetch_edges(frontier, leg)
        except Exception as exc:
            walk.warnings.append(f"{leg} hop {hop}: {exc}")
            walk.source_failures.append(f"{leg} hop {hop}: {exc}")
            return []
        query_truncated = len(rows) > per_query_limit
        rows = rows[:per_query_limit]
        coverage: dict[str, Any] | None = None
        if fetch_coverage is not None:
            try:
                coverage = fetch_coverage(frontier, leg)
            except Exception as exc:
                walk.warnings.append(f"{leg} hop {hop} coverage unavailable: {exc}")
        candidate_index = 1 if leg == "out" else 0
        candidate_ids = [str(row[candidate_index]) for row in rows]
        token_contract_candidates = detect_token_contracts(candidate_ids)

        # An injected fetcher is used by the pure walker tests and is expected
        # to return its full adjacency.  A real LIMIT n+1 query is also exact
        # whenever it did not hit the limit, so the returned rows can safely
        # recover coverage if the companion aggregate was unavailable.
        if coverage is None and (custom_fetch_edges or not query_truncated):
            candidates = {
                str(row[candidate_index])
                for row in rows
                if not is_structural_terminal(row[candidate_index])
                and str(row[candidate_index]) not in token_contract_candidates
            }
            supply_event_edges = sum(
                1 for row in rows if is_structural_terminal(row[candidate_index])
            )
            contract_endpoint_edges = sum(
                1
                for row in rows
                if str(row[candidate_index]) in token_contract_candidates
            )
            known = sum(float(row[5]) for row in rows if row[5] is not None)
            unknown = sum(
                1
                for row in rows
                if row[5] is None
                or (len(row) > 9 and int(row[9] or 0) > 0)
            )
            coverage = {
                "total_counterparties": len(candidates),
                "total_edges": len(rows),
                "known_usd": known,
                "total_usd": known if unknown == 0 else None,
                "unknown_usd_edges": unknown,
                # An injected/full result has no hidden candidate population.
                "excluded_unknown_usd_edges": 0,
                "supply_event_edges": supply_event_edges,
                "contract_endpoint_edges": contract_endpoint_edges,
                "exact": True,
            }

        admitted: list[str] = []
        admitted_counterparty_count = 0
        shown_counterparties: set[str] = set()
        shown_supply_event_edges = 0
        shown_contract_endpoint_edges = 0
        shown_edge_count = 0
        shown_known_usd = 0.0
        shown_unknown_usd_edges = 0
        budget_hit = False
        for row in rows:  # USD-desc — biggest money first degradation
            src, tgt = str(row[0]), str(row[1])
            token_addr = str(row[2])
            parent, candidate = (src, tgt) if leg == "out" else (tgt, src)
            if parent not in walk.nodes:
                continue  # stale frontier row
            event_kind = money_event_kind(src, tgt)
            structural_candidate = is_structural_terminal(candidate)
            contract_candidate = candidate in token_contract_candidates
            terminal_candidate = structural_candidate or contract_candidate
            if event_kind == "transfer" and (
                contract_candidate
                or walk.nodes[parent].get("is_token_contract")
            ):
                event_kind = "contract_endpoint"
            is_new = candidate not in walk.nodes
            if is_new:
                if (
                    (not terminal_candidate
                     and admitted_counterparty_count >= per_hop_budget)
                    or len(walk.nodes) >= node_cap
                ):
                    budget_hit = True
                    continue  # DROP the edge — no dangling nodes
                delta = 1 if leg == "out" else -1
                walk.nodes[candidate] = {
                    "rank": walk.nodes[parent]["rank"] + delta
                }
                if structural_candidate:
                    walk.nodes[candidate].update(
                        {
                            "sector": "Supply",
                            "project": structural_terminal_label(candidate),
                            "structural_terminal": True,
                        }
                    )
                elif contract_candidate:
                    walk.nodes[candidate]["is_token_contract"] = True
                else:
                    admitted_counterparty_count += 1
                admitted.append(candidate)
            if len(walk.edges) >= edge_cap:
                walk.truncated = True
                continue
            eid = flow_edge_id(src, tgt, token_addr, "transfer")
            walk.edges[eid] = {
                "id": eid,
                "source": src,
                "target": tgt,
                "edge_class": event_kind,
                "token_address": token_addr,
                "symbol": str(row[3] or ""),
                "amount": float(row[4] or 0),
                "amount_usd": None if row[5] is None else float(row[5]),
                "transfer_count": int(row[6] or 0),
                "first_seen": str(row[7] or ""),
                "last_seen": str(row[8] or ""),
                "unknown_usd_rows": (
                    int(row[9] or 0)
                    if len(row) > 9
                    else (1 if row[5] is None else 0)
                ),
            }
            shown_edge_count += 1
            if structural_candidate:
                shown_supply_event_edges += 1
            elif contract_candidate:
                shown_contract_endpoint_edges += 1
            else:
                shown_counterparties.add(candidate)
            if row[5] is None or (len(row) > 9 and int(row[9] or 0) > 0):
                shown_unknown_usd_edges += 1
            if row[5] is not None:
                shown_known_usd += float(row[5])

        total_counterparties = (
            int(coverage["total_counterparties"])
            if coverage is not None and coverage.get("exact")
            else None
        )
        total_usd = (
            coverage.get("total_usd")
            if coverage is not None and coverage.get("exact")
            else None
        )
        walk.hop_coverage.append(
            {
                "hop": hop,
                "direction": leg,
                "frontier_size": len(frontier),
                "budget": per_hop_budget,
                "ranking": "usd_desc",
                "shown_counterparties": len(shown_counterparties),
                "total_counterparties": total_counterparties,
                "dropped_counterparties": (
                    max(0, total_counterparties - len(shown_counterparties))
                    if total_counterparties is not None
                    else None
                ),
                "shown_edges": shown_edge_count,
                "shown_supply_event_edges": shown_supply_event_edges,
                "total_supply_event_edges": (
                    int(coverage.get("supply_event_edges") or 0)
                    if coverage is not None and coverage.get("exact")
                    else None
                ),
                "shown_contract_endpoint_edges": shown_contract_endpoint_edges,
                "total_contract_endpoint_edges": (
                    int(coverage.get("contract_endpoint_edges") or 0)
                    if coverage is not None and coverage.get("exact")
                    else None
                ),
                "total_edges": (
                    int(coverage["total_edges"])
                    if coverage is not None and coverage.get("exact")
                    else None
                ),
                "shown_usd": round(shown_known_usd, 6),
                "total_usd": (
                    None if total_usd is None else round(float(total_usd), 6)
                ),
                "retained_usd_fraction": (
                    shown_known_usd / float(total_usd)
                    if total_usd is not None and float(total_usd) > 0
                    else None
                ),
                "shown_unknown_usd_edges": shown_unknown_usd_edges,
                "total_unknown_usd_edges": (
                    int(coverage["unknown_usd_edges"])
                    if coverage is not None and coverage.get("exact")
                    else None
                ),
                "excluded_unknown_usd_edges": (
                    int(coverage["excluded_unknown_usd_edges"])
                    if coverage is not None
                    and coverage.get("exact")
                    and coverage.get("excluded_unknown_usd_edges") is not None
                    else None
                ),
                "exact": bool(coverage is not None and coverage.get("exact")),
            }
        )
        if query_truncated or budget_hit:
            walk.truncated = True
            tag = f"{leg} hop {hop}"
            if tag not in walk.truncated_hops:
                walk.truncated_hops.append(tag)
        # Attribution BEFORE the next frontier: terminal sectors stop here, and
        # so do ERC-20 contracts (a deposit/burn target is not a counterparty —
        # walking through one would pull in every holder of that token).
        label_nodes(admitted)
        return [
            n
            for n in admitted
            if walk.nodes[n].get("sector", "") not in terminal
            and not walk.nodes[n].get("is_token_contract")
            and not walk.nodes[n].get("structural_terminal")
            and not is_structural_terminal(n)
        ]

    def run_bridges(frontier: list[str], hop: int) -> None:
        """Annotate already-admitted transfers with bridge attribution.

        The bridge aggregate is derived from the same transfer population. It
        therefore cannot safely create a second movement edge: doing so would
        double-count value and make the graph look like two observations. A
        bridge row that has no matching admitted transfer is retained only as
        a warning for the source disclosure.
        """
        if not include_bridges or not frontier:
            return
        try:
            rows = fetch_bridges(frontier)
        except Exception as exc:
            walk.warnings.append(f"bridges hop {hop}: {exc}")
            walk.source_failures.append(f"bridges hop {hop}: {exc}")
            return
        unmatched = 0
        for row in rows[:per_query_limit]:
            src, tgt = str(row[0]), str(row[1])
            token_addr = str(row[2])
            if not src or not tgt or src == tgt or src not in walk.nodes:
                continue
            eid = flow_edge_id(src, tgt, token_addr, "transfer")
            edge = walk.edges.get(eid)
            if edge is None:
                unmatched += 1
                continue
            edge["edge_class"] = "bridge_attributed"
            edge["bridge_contract"] = tgt
            edge["bridge_name"] = str(row[4] or "")
            if tgt in walk.nodes:
                walk.nodes[tgt]["sector"] = "Bridges"
                walk.nodes[tgt]["project"] = (
                    str(row[4] or "") or walk.nodes[tgt].get("project", "")
                )
        if unmatched:
            warning = (
                f"bridges hop {hop}: {unmatched} attribution row(s) had no "
                "matching admitted transfer and were not rendered"
            )
            walk.warnings.append(warning)
            walk.source_failures.append(warning)

    # Interleaved hop-by-hop legs (out first — the out-bias tie).
    legs = {"out": ["out"], "in": ["in"], "both": ["out", "in"]}[direction]
    frontiers: dict[str, list[str]] = {leg: list(seed_set) for leg in legs}
    for hop in range(1, max(1, hops) + 1):
        for leg in legs:
            current_frontier = frontiers[leg]
            frontiers[leg] = run_leg(leg, current_frontier, hop)
            if leg == "out":
                run_bridges(current_frontier, hop)

    return walk


def _resolve_range(
    t0: str, t1: str, range_days: int
) -> tuple[str, str, int]:
    """Explicit ISO datetimes win; else derive a half-open window ending
    tomorrow (exclusive) from range_days."""
    if t0 and t1:
        pad = lambda s: s if len(s) > 10 else f"{s} 00:00:00"  # noqa: E731
        return pad(t0), pad(t1), range_days or 0
    days = int(range_days or constants.FLOWS_DEFAULT_RANGE_DAYS)
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=max(1, days))
    return f"{start.isoformat()} 00:00:00", f"{end.isoformat()} 00:00:00", days


def register_flows_tools(mcp, ch: ClickHouseManager) -> dict[str, Any]:
    """Register the flows tools; returns {name: fn} for web-app dispatch."""

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_graph_flows(
        view_id: str,
        seed_node_ids: list[str],
        direction: str = "out",
        hops: int = 0,
        t0: str = "",
        t1: str = "",
        range_days: int = 0,
        min_usd: float = -1.0,
        tokens: list[str] | None = None,
        include_bridges: bool = True,
        merge: bool = False,
        max_edges: int = 0,
        request_id: int = 0,
    ) -> CallToolResult:
        """Trace fund flows from seed addresses (app-only, Flows mode)."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )
        seeds = []
        for s in seed_node_ids or []:
            n = _normalize_node_id(s)
            if n and n not in seeds:
                seeds.append(n)
        if not seeds:
            return mini_apps.error_call_tool_result(
                "Provide at least one seed address."
            )
        structural_seeds = [s for s in seeds if is_structural_terminal(s)]
        if structural_seeds:
            return mini_apps.error_call_tool_result(
                f"{structural_terminal_label(structural_seeds[0])} is a "
                "mint/burn endpoint, not an investigative seed."
            )
        if len(seeds) > constants.FLOWS_MAX_SEEDS:
            return mini_apps.error_call_tool_result(
                f"Too many seeds (max {constants.FLOWS_MAX_SEEDS})."
            )
        if direction not in ("out", "in", "both"):
            return mini_apps.error_call_tool_result(
                "direction must be 'out', 'in', or 'both'"
            )

        state_flows = dict(record.view_state.get("flows") or {})
        warnings: list[str] = []
        request_id = max(0, int(request_id or 0))
        scope_id = new_scope_id("flows", request_id)

        if merge:
            # Filters come from the view — a merge can never mix regimes.
            conflicting = []
            if t0 or t1 or range_days:
                conflicting.append("time range")
            if min_usd >= 0 and min_usd != state_flows.get("min_usd"):
                conflicting.append("min_usd")
            if tokens is not None and tokens != state_flows.get("tokens"):
                conflicting.append("tokens")
            if conflicting:
                warnings.append(
                    "merge=True uses the view's filters — ignored explicit: "
                    + ", ".join(conflicting)
                )
            eff_t0 = state_flows.get("t0") or ""
            eff_t1 = state_flows.get("t1") or ""
            eff_range = int(state_flows.get("range_days") or 0)
            if not (eff_t0 and eff_t1):
                eff_t0, eff_t1, eff_range = _resolve_range("", "", eff_range)
            eff_min_usd = float(state_flows.get("min_usd", constants.FLOWS_DEFAULT_MIN_USD))
            eff_tokens = list(state_flows.get("tokens") or []) or None
            eff_bridges = bool(state_flows.get("include_bridges", True))
            eff_hops = max(1, min(int(hops or 1), constants.FLOWS_MAX_HOPS))
        else:
            eff_t0, eff_t1, eff_range = _resolve_range(t0, t1, range_days)
            eff_min_usd = (
                float(min_usd)
                if min_usd >= 0
                else constants.FLOWS_DEFAULT_MIN_USD
            )
            eff_tokens = [t.lower() for t in tokens] if tokens else None
            eff_bridges = bool(include_bridges)
            eff_hops = max(
                1,
                min(
                    int(hops or constants.FLOWS_DEFAULT_HOPS),
                    constants.FLOWS_MAX_HOPS,
                ),
            )

        edge_cap = int(max_edges or constants.FLOWS_MAX_EDGES)

        # Merge pre-seeds the walk with the existing graph so ranks are
        # preserved and new neighbors extend from the expanded node's rank.
        existing_walk: FlowWalk | None = None
        if merge:
            existing_walk = _walk_from_datasets(record)
            for s in seeds:
                if s not in existing_walk.nodes:
                    return mini_apps.error_call_tool_result(
                        f"Node {short_id(s)} is not on the flow graph — "
                        "load it as a seed first (merge expands existing nodes)."
                    )

        primary_contract = validate_source_contract(
            ch,
            FLOWS_RELATION,
            (
                "date",
                "token_address",
                "from",
                "to",
                "amount_raw",
                "transfer_count",
            ),
            probe_horizon=True,
            horizon_column="date",
        )
        metadata_contract = validate_source_contract(
            ch,
            TOKENS_META_RELATION,
            ("token_address", "token", "decimals", "date_start", "date_end"),
            probe_horizon=True,
        )
        prices_contract = validate_source_contract(
            ch,
            PRICES_RELATION,
            ("symbol", "date", "price"),
            probe_horizon=True,
            horizon_column="date",
        )
        token_universe_error: str | None = None
        token_universe: dict[str, Any] = {
            "addresses": [],
            "count": None,
            "as_of": eff_t1,
            "source": f"dbt.{TOKENS_META_RELATION}",
            "sha256": "",
            "status": "failed",
        }
        if metadata_contract["ok"]:
            try:
                universe_sql, universe_params = build_active_token_universe_sql(
                    t0=eff_t0,
                    t1_exclusive=eff_t1,
                    limit=1000,
                )
                universe_result = mini_apps.run_structured_query(
                    ch,
                    universe_sql,
                    database="dbt",
                    parameters=universe_params,
                    requested_max_rows=1001,
                )
                if len(universe_result.rows) > 1000:
                    raise RuntimeError(
                        "active token universe exceeded the 1,000-address safety cap"
                    )
                active_addresses = sorted(
                    {
                        str(row[0]).strip().lower()
                        for row in universe_result.rows
                        if row and str(row[0] or "").strip()
                    }
                )
                if eff_tokens:
                    selected_tokens = set(eff_tokens)
                    active_addresses = [
                        address
                        for address in active_addresses
                        if address in selected_tokens
                    ]
                token_universe = {
                    "addresses": active_addresses,
                    "count": len(active_addresses),
                    "as_of": eff_t1,
                    "source": f"dbt.{TOKENS_META_RELATION}",
                    "sha256": hashlib.sha256(
                        "\n".join(active_addresses).encode("utf-8")
                    ).hexdigest(),
                    "status": "verified",
                }
            except Exception as exc:
                token_universe_error = f"token universe unavailable: {exc}"
        bridge_contract = None
        bridge_gate: dict[str, Any] | None = None
        bridge_issue: str | None = None
        use_bridges = eff_bridges and direction != "in"
        if use_bridges:
            bridge_contract = validate_source_contract(
                ch,
                BRIDGES_RELATION,
                (
                    "date",
                    "direction",
                    "user_address",
                    "bridge_contract",
                    "bridge_name",
                    "token_address",
                    "symbol",
                    "amount_raw_sum",
                    "transfer_count",
                ),
                probe_horizon=True,
                horizon_column="date",
            )
            if not bridge_contract["ok"]:
                bridge_issue = (
                    "bridge source contract unavailable: "
                    + str(
                        bridge_contract.get("error")
                        or "required bridge columns could not be verified"
                    )
                )
                use_bridges = False
            else:
                bridge_gate = validate_bridge_relation_safety(ch)
                if not bridge_gate["ok"]:
                    bridge_issue = str(
                        bridge_gate.get("error")
                        or "bridge relation safety could not be proven"
                    )
                    use_bridges = False
            if bridge_issue:
                warnings.append(
                    bridge_issue + "; primary transfer evidence remains usable."
                )

        required_contract_failures = [
            contract
            for contract in (primary_contract, metadata_contract, prices_contract)
            if not contract["ok"]
        ]
        if required_contract_failures:
            walk = FlowWalk(
                warnings=[
                    "flow source contract failed: "
                    + "; ".join(
                        f"{contract['relation']}: "
                        + str(contract.get("error") or "unknown source error")
                        for contract in required_contract_failures
                    )
                ],
                source_failures=[
                    f"{contract['relation']}: "
                    + str(contract.get("error") or "flow source unavailable")
                    for contract in required_contract_failures
                ],
            )
        else:
            walk = flows_trace(
                ch,
                seeds=seeds,
                direction=direction,
                hops=eff_hops,
                t0=eff_t0,
                t1=eff_t1,
                min_usd=eff_min_usd,
                tokens=eff_tokens,
                include_bridges=use_bridges,
                per_hop_budget=constants.FLOWS_PER_HOP_NODE_BUDGET,
                node_cap=constants.FLOWS_MAX_NODES,
                edge_cap=edge_cap,
                per_query_limit=constants.FLOWS_EDGES_PER_QUERY,
                existing=existing_walk,
            )
            if token_universe_error:
                walk.warnings.append(token_universe_error)
                walk.source_failures.append(token_universe_error)
        warnings.extend(walk.warnings)
        if walk.truncated_hops:
            warnings.append(
                "Trace truncated at: "
                + ", ".join(walk.truncated_hops)
                + " — raise min USD, narrow tokens, or shorten the range."
            )

        # ---- GP-case flags over the final node set ---------------------------
        flags: dict[str, list[str]] = {}
        node_ids = list(walk.nodes.keys())
        try:
            for kind, sql, params in build_gp_flags_sqls(node_ids):
                result = mini_apps.run_structured_query(
                    ch, sql, database="dbt", parameters=params,
                    requested_max_rows=len(node_ids) + 1,
                )
                for row in result.rows:
                    if kind == "canonical":
                        old, new = str(row[0]), str(row[1])
                        if old in walk.nodes:
                            flags.setdefault(old, []).append("old_safe")
                        if new in walk.nodes:
                            flags.setdefault(new, []).append("new_safe")
                    else:  # refunded
                        rid = str(row[0])
                        if rid in walk.nodes:
                            flags.setdefault(rid, []).append("refunded_safe")
        except Exception as exc:
            warnings.append(f"GP-case flag lookup failed: {exc}")

        # ---- Node aggregates over the FINAL loaded edge set ------------------
        in_usd: dict[str, float] = {}
        out_usd: dict[str, float] = {}
        in_edge_nodes: set[str] = set()
        out_edge_nodes: set[str] = set()
        in_unpriced: set[str] = set()
        out_unpriced: set[str] = set()
        first_seen: dict[str, str] = {}
        last_seen: dict[str, str] = {}
        token_usd: dict[str, dict[str, Any]] = {}
        known_usd_total = 0.0
        unknown_usd_rows = 0
        for e in walk.edges.values():
            raw_usd = e["amount_usd"]
            usd = None if raw_usd is None else float(raw_usd)
            edge_unknown_usd_rows = int(
                e.get("unknown_usd_rows", 1 if usd is None else 0) or 0
            )
            out_edge_nodes.add(e["source"])
            in_edge_nodes.add(e["target"])
            if edge_unknown_usd_rows:
                unknown_usd_rows += edge_unknown_usd_rows
                out_unpriced.add(e["source"])
                in_unpriced.add(e["target"])
            if usd is not None:
                known_usd_total += usd
                out_usd[e["source"]] = out_usd.get(e["source"], 0.0) + usd
                in_usd[e["target"]] = in_usd.get(e["target"], 0.0) + usd
            for nid in (e["source"], e["target"]):
                fs, ls = str(e["first_seen"]), str(e["last_seen"])
                if fs and (nid not in first_seen or fs < first_seen[nid]):
                    first_seen[nid] = fs
                if ls and (nid not in last_seen or ls > last_seen[nid]):
                    last_seen[nid] = ls
            if usd is not None:
                tok = token_usd.setdefault(
                    e["token_address"],
                    {
                        "token_address": e["token_address"],
                        "symbol": e["symbol"],
                        "amount_usd": 0.0,
                    },
                )
                tok["amount_usd"] += usd

        node_rows: list[list[Any]] = []
        for nid, meta in walk.nodes.items():
            project = str(meta.get("project", "") or "")
            # An ERC-20 contract is NOT a counterparty — surface it as its own
            # sector so the canvas colors it distinctly and the analyst reads
            # the edge as a deposit/burn/redeem rather than "money to a person".
            is_token_contract = bool(meta.get("is_token_contract"))
            is_supply_terminal = bool(
                meta.get("structural_terminal") or is_structural_terminal(nid)
            )
            sector = str(meta.get("sector", "") or "")
            node_flags = set(flags.get(nid, []))
            if is_token_contract:
                node_flags.add("token_contract")
                sector = sector or "Token contract"
            if is_supply_terminal:
                node_flags.add("structural_terminal")
                sector = "Supply"
                project = structural_terminal_label(nid)
            node_rows.append([
                nid,
                project or short_id(nid),
                sector,
                project,
                int(meta["rank"]),
                (
                    round(in_usd[nid], 2)
                    if nid in in_usd and nid not in in_unpriced
                    else (None if nid in in_edge_nodes else 0.0)
                ),
                (
                    round(out_usd[nid], 2)
                    if nid in out_usd and nid not in out_unpriced
                    else (None if nid in out_edge_nodes else 0.0)
                ),
                first_seen.get(nid, ""),
                last_seen.get(nid, ""),
                sorted(node_flags),
            ])
        edge_rows = [
            [
                e["id"], e["source"], e["target"], e["edge_class"],
                e["token_address"], e["symbol"], e["amount"], e["amount_usd"],
                e["transfer_count"], e["first_seen"], e["last_seen"],
                int(e.get("unknown_usd_rows", 1 if e["amount_usd"] is None else 0)),
            ]
            for e in walk.edges.values()
        ]
        catalog = sorted(
            token_usd.values(), key=lambda t: -t["amount_usd"]
        )[:TOKEN_CATALOG_CAP]
        for t in catalog:
            t["amount_usd"] = round(t["amount_usd"], 2)

        transfer_failures = [
            failure
            for failure in walk.source_failures
            if "bridge" not in str(failure).lower()
        ]
        bridge_runtime_failures = [
            str(failure)
            for failure in walk.source_failures
            if "bridge" in str(failure).lower()
        ]
        primary_status = (
            "error"
            if not primary_contract["ok"]
            else ("partial" if transfer_failures else "ok")
        )
        sources = [
            source_record(
                kind="dbt_aggregate",
                name=f"dbt.{FLOWS_RELATION}",
                role="primary",
                status=primary_status,
                # A result's latest event is not the relation watermark.
                # Keep the independently probed source horizon intact and
                # publish scoped observations separately below.
                horizon=primary_contract.get("horizon"),
                horizon_basis=primary_contract.get("horizon_basis"),
                fetched_at=primary_contract.get("freshness_checked_at"),
                error=(
                    str(primary_contract.get("error"))
                    if not primary_contract["ok"]
                    else ("; ".join(transfer_failures) or None)
                ),
            ),
            source_record(
                kind="dbt_aggregate",
                name=f"dbt.{TOKENS_META_RELATION}",
                role="enrichment",
                status=(
                    "error"
                    if not metadata_contract["ok"]
                    else ("partial" if token_universe_error else "ok")
                ),
                horizon=metadata_contract.get("horizon"),
                horizon_basis=metadata_contract.get("horizon_basis"),
                fetched_at=metadata_contract.get("freshness_checked_at"),
                error=token_universe_error or metadata_contract.get("error"),
            ),
            source_record(
                kind="dbt_aggregate",
                name=f"dbt.{PRICES_RELATION}",
                role="enrichment",
                status="ok" if prices_contract["ok"] else "error",
                horizon=prices_contract.get("horizon"),
                horizon_basis=prices_contract.get("horizon_basis"),
                fetched_at=prices_contract.get("freshness_checked_at"),
                error=prices_contract.get("error"),
            ),
        ]
        if bridge_contract is not None:
            bridge_source_error = (
                bridge_issue
                or ("; ".join(bridge_runtime_failures) or None)
                or bridge_contract.get("error")
            )
            sources.append(
                source_record(
                    kind="dbt_aggregate",
                    name=f"dbt.{BRIDGES_RELATION}",
                    role="enrichment",
                    status=(
                        "error"
                        if bridge_issue or not bridge_contract["ok"]
                        else (
                            "partial"
                            if bridge_runtime_failures
                            else "ok"
                        )
                    ),
                    contract_status=(
                        "error"
                        if bridge_issue or not bridge_contract["ok"]
                        else "ok"
                    ),
                    horizon=bridge_contract.get("horizon"),
                    horizon_basis=bridge_contract.get("horizon_basis"),
                    fetched_at=bridge_contract.get("freshness_checked_at"),
                    error=bridge_source_error,
                )
            )
        coverage_exact = bool(walk.hop_coverage) and all(
            bool(item.get("exact")) for item in walk.hop_coverage
        )
        excluded_unknown_usd_edges = (
            sum(
                int(item["excluded_unknown_usd_edges"])
                for item in walk.hop_coverage
            )
            if coverage_exact
            and all(
                item.get("excluded_unknown_usd_edges") is not None
                for item in walk.hop_coverage
            )
            else None
        )
        eligible_unknown_usd_edges = (
            sum(
                int(item["total_unknown_usd_edges"])
                for item in walk.hop_coverage
            )
            if coverage_exact
            and all(
                item.get("total_unknown_usd_edges") is not None
                for item in walk.hop_coverage
            )
            else None
        )
        if eff_min_usd > 0 and (eligible_unknown_usd_edges or 0) > 0:
            warnings.append(
                f"USD minimum {eff_min_usd:g} applies only to priced edges; "
                f"{eligible_unknown_usd_edges} wholly or partly unpriced "
                "aggregated edge(s) remain available in the unpriced lane."
            )
        if unknown_usd_rows:
            warnings.append(
                f"{unknown_usd_rows} admitted daily source row(s) lacked price "
                "enrichment; displayed USD values are known subtotals and "
                "complete USD totals remain unknown."
            )
        if required_contract_failures or (transfer_failures and not edge_rows):
            scope_status = "failed"
        elif (
            walk.truncated
            or transfer_failures
            or bridge_issue
            or bridge_runtime_failures
            or unknown_usd_rows > 0
        ):
            scope_status = "partial"
        else:
            scope_status = "ready"
        # Structural coverage and USD completeness are independent. Missing
        # prices make value totals partial but do not erase exact node/edge
        # counts established by the bounded companion queries.
        structural_exact = bool(
            coverage_exact
            and not walk.truncated
            and not transfer_failures
            and not required_contract_failures
        )
        shown_counterparties = sum(
            int(item["shown_counterparties"]) for item in walk.hop_coverage
        )
        shown_supply_event_edges = sum(
            int(item.get("shown_supply_event_edges") or 0)
            for item in walk.hop_coverage
        )
        shown_contract_endpoint_edges = sum(
            int(item.get("shown_contract_endpoint_edges") or 0)
            for item in walk.hop_coverage
        )
        total_counterparties = (
            sum(int(item["total_counterparties"]) for item in walk.hop_coverage)
            if coverage_exact
            and all(item.get("total_counterparties") is not None for item in walk.hop_coverage)
            else None
        )
        total_supply_event_edges = (
            sum(
                int(item.get("total_supply_event_edges") or 0)
                for item in walk.hop_coverage
            )
            if coverage_exact
            and all(
                item.get("total_supply_event_edges") is not None
                for item in walk.hop_coverage
            )
            else None
        )
        total_contract_endpoint_edges = (
            sum(
                int(item.get("total_contract_endpoint_edges") or 0)
                for item in walk.hop_coverage
            )
            if coverage_exact
            and all(
                item.get("total_contract_endpoint_edges") is not None
                for item in walk.hop_coverage
            )
            else None
        )
        shown_measured_usd = round(
            sum(float(item.get("shown_usd") or 0) for item in walk.hop_coverage),
            6,
        )
        total_measured_usd = (
            round(
                sum(float(item["total_usd"]) for item in walk.hop_coverage),
                6,
            )
            if coverage_exact
            and all(item.get("total_usd") is not None for item in walk.hop_coverage)
            else None
        )
        truncation_coverage = {
            "budget_per_hop": constants.FLOWS_PER_HOP_NODE_BUDGET,
            "ranking": "usd_desc",
            # A counterparty can legitimately appear in more than one hop.
            # State the counting basis rather than silently calling this a
            # global unique-address total.
            "counting_basis": "sum_of_per_hop_unique_counterparties",
            "shown_counterparties": shown_counterparties,
            "total_counterparties": total_counterparties,
            "shown_supply_event_edges": shown_supply_event_edges,
            "total_supply_event_edges": total_supply_event_edges,
            "shown_contract_endpoint_edges": shown_contract_endpoint_edges,
            "total_contract_endpoint_edges": total_contract_endpoint_edges,
            "dropped_counterparties": (
                max(0, total_counterparties - shown_counterparties)
                if total_counterparties is not None
                else None
            ),
            "shown_usd": shown_measured_usd,
            "total_usd": total_measured_usd,
            "retained_usd_fraction": (
                shown_measured_usd / total_measured_usd
                if total_measured_usd is not None and total_measured_usd > 0
                else None
            ),
            "excluded_unknown_usd_edges": excluded_unknown_usd_edges,
            "eligible_unknown_usd_edges": eligible_unknown_usd_edges,
            "priced_only_minimum": eff_min_usd if eff_min_usd > 0 else None,
            "exact": coverage_exact,
            "by_hop": walk.hop_coverage,
        }
        scope_truncated = bool(walk.truncated)
        primary_horizons = [
            str(source["horizon"])
            for source in sources
            if source.get("role") == "primary"
            and source.get("status") != "error"
            and source.get("horizon") is not None
        ]
        result_observed_through = max(last_seen.values()) if last_seen else None
        scope = forensic_scope(
            scope_id=scope_id,
            request_id=request_id,
            status=scope_status,
            t0=eff_t0,
            t1=eff_t1,
            window_source=(
                "explicit_t0_t1"
                if t0 and t1 and not merge
                else f"range_days={eff_range}"
            ),
            # Multiple primary relations have independent clocks. The
            # conservative combined bound is their oldest usable watermark;
            # each exact watermark remains visible on its source record.
            data_horizon=(
                min(primary_horizons)
                if primary_horizons
                else primary_contract.get("horizon")
            ),
            result_observed_through=result_observed_through,
            sources=sources,
            rows_returned=len(edge_rows),
            rows_total=len(edge_rows) if structural_exact else None,
            nodes_returned=len(node_rows),
            nodes_total=len(node_rows) if structural_exact else None,
            edges_returned=len(edge_rows),
            edges_total=len(edge_rows) if structural_exact else None,
            known_usd=round(known_usd_total, 6),
            total_usd=(
                round(known_usd_total, 6)
                if structural_exact and unknown_usd_rows == 0
                else None
            ),
            unknown_usd_rows=unknown_usd_rows,
            truncated=scope_truncated,
            truncation_rule=(
                f"per-hop node budget {constants.FLOWS_PER_HOP_NODE_BUDGET}, "
                f"USD-descending admission; node cap {constants.FLOWS_MAX_NODES}; "
                f"edge cap {edge_cap}; "
                + f"min_usd={eff_min_usd:g} filters priced edges only; "
                "unpriced aggregates remain structurally admitted"
            ),
            coverage_note="whitelisted tokens only; daily-grain transfer model",
            residuals=(
                "token coverage is limited to the warehouse whitelist",
                "daily grain cannot establish same-day transaction ordering",
                "bridge rows annotate admitted transfers; they do not prove "
                "a destination-chain receipt",
            ),
            warnings=warnings,
            verification_status="verified" if structural_exact else "unverified",
            verification_method=(
                "structural LIMIT n+1 per hop plus explicit budget accounting; "
                "USD completeness is reported separately"
            ),
            query_kind="money_trail",
            evidence_class="aggregate_transfer_adjacency",
            subjects=(
                list(dict.fromkeys([*(state_flows.get("seeds") or []), *seeds]))
                if merge
                else seeds
            ),
            token_universe=token_universe,
        )
        scope["truncation_coverage"] = truncation_coverage
        scope["bridge_enrichment"] = {
            "requested": bool(eff_bridges and direction != "in"),
            "enabled": bool(use_bridges),
            "quality_gate": bridge_gate,
            "error": bridge_issue,
        }
        scope["usd_filter_coverage"] = {
            "min_usd": eff_min_usd,
            "eligibility": (
                "priced_at_or_above_minimum_or_unpriced"
            ),
            "excluded_unknown_usd_edges": excluded_unknown_usd_edges,
            "eligible_unknown_usd_edges": eligible_unknown_usd_edges,
            "counting_basis": "sum_of_per_hop_aggregated_edges",
        }

        # ---- Persist (per-key attach ONLY) -----------------------------------
        mini_apps.attach_dataset(
            view_id,
            "flow_nodes",
            dataset_from_rows(constants.FLOW_NODES_COLUMNS, node_rows, "flow_nodes"),
        )
        mini_apps.attach_dataset(
            view_id,
            "flow_edges",
            dataset_from_rows(constants.FLOW_EDGES_COLUMNS, edge_rows, "flow_edges"),
        )

        expanded = dict(state_flows.get("expanded") or {}) if merge else {}
        if merge:
            for s in seeds:
                dirs = set(expanded.get(s, []))
                dirs.add(direction)
                expanded[s] = sorted(dirs)
        prior_seeds = list(state_flows.get("seeds") or []) if merge else []
        all_seeds = prior_seeds + [s for s in seeds if s not in prior_seeds] if merge else seeds

        # Data loader: writes ONLY the flows namespace + datasets. mode and
        # selection are owned by explicit mode commands (mode_revision) — a
        # trace must never flip the visible tab or clear the client selection.
        mini_apps.patch_view_state(
            view_id,
            {
                "flows": {
                    "seeds": all_seeds,
                    "direction": state_flows.get("direction", direction) if merge else direction,
                    "hops": int(state_flows.get("hops", eff_hops)) if merge else eff_hops,
                    "range_days": eff_range,
                    "t0": eff_t0,
                    "t1": eff_t1,
                    "min_usd": eff_min_usd,
                    "tokens": list(eff_tokens or []),
                    "include_bridges": eff_bridges,
                    "node_count": len(node_rows),
                    "edge_count": len(edge_rows),
                    "truncated": scope_truncated,
                    "truncated_hops": walk.truncated_hops,
                    "expanded": expanded,
                    "token_catalog": catalog,
                    "scope": scope,
                    "truncation_coverage": truncation_coverage,
                },
                "dataset_scopes": {
                    "flow_nodes": scope_id,
                    "flow_edges": scope_id,
                },
                "warnings": warnings,
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        return mini_apps.payload_to_call_tool_result(
            build_payload(updated),
            summary_text=(
                f"Flows traced from {len(seeds)} seed(s) ({direction}, "
                f"{eff_hops} hop(s)): {len(node_rows)} node(s), "
                f"{len(edge_rows)} edge(s)."
            ),
        )

    mini_apps.mark_app_only("load_graph_flows")
    return {"load_graph_flows": load_graph_flows}


def _walk_from_datasets(record) -> FlowWalk:
    """Rebuild a FlowWalk from the view's flow datasets (merge pre-seed)."""
    walk = FlowWalk()
    flow_state = dict(record.view_state.get("flows") or {})
    prior_scope = flow_state.get("scope") or {}
    prior_coverage = (
        prior_scope.get("truncation_coverage")
        if isinstance(prior_scope, dict)
        else None
    )
    if not isinstance(prior_coverage, dict):
        prior_coverage = flow_state.get("truncation_coverage") or {}
    by_hop = prior_coverage.get("by_hop") if isinstance(prior_coverage, dict) else []
    if isinstance(by_hop, list):
        # Coverage is cumulative under merge just like the datasets.  Without
        # restoring these companion-query records, the UI labelled the latest
        # one-hop delta as coverage for the whole merged graph.
        walk.hop_coverage = [dict(item) for item in by_hop if isinstance(item, dict)]
    walk.truncated = bool(flow_state.get("truncated"))
    walk.truncated_hops = [
        str(item) for item in (flow_state.get("truncated_hops") or []) if item
    ]
    nodes_ds = record.datasets.get("flow_nodes")
    for row in (nodes_ds.rows if nodes_ds else []):
        if not row or not row[0]:
            continue
        row_flags = {
            str(flag)
            for flag in (row[9] if len(row) > 9 and isinstance(row[9], list) else [])
        }
        walk.nodes[str(row[0])] = {
            "rank": int(row[4] or 0),
            "sector": str(row[2] or ""),
            "project": str(row[3] or ""),
            "is_token_contract": "token_contract" in row_flags,
            "structural_terminal": "structural_terminal" in row_flags,
        }
    edges_ds = record.datasets.get("flow_edges")
    for row in (edges_ds.rows if edges_ds else []):
        if not row or not row[0]:
            continue
        walk.edges[str(row[0])] = {
            "id": str(row[0]),
            "source": str(row[1]),
            "target": str(row[2]),
            "edge_class": str(row[3]),
            "token_address": str(row[4]),
            "symbol": str(row[5] or ""),
            "amount": row[6],
            "amount_usd": row[7],
            "transfer_count": int(row[8] or 0),
            "first_seen": str(row[9] or ""),
            "last_seen": str(row[10] or ""),
            "unknown_usd_rows": (
                int(row[11] or 0)
                if len(row) > 11
                else (1 if row[7] is None else 0)
            ),
        }
    return walk
