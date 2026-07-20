"""Money Trail → Over time (app-only).

This loader deliberately does *not* reuse Graph Explorer relationship
profiles.  Over time is the temporal reading of the same whitelisted,
USD-ranked transfer contract as Money Trail:

* one applied seed/filter/window contract;
* one counterparty universe ranked over that complete window;
* that universe frozen across every time bucket;
* exact pre-budget global and per-bucket coverage; and
* a narrative diff shipped before the optional graph overview.

The prior implementation mixed a 90-day Investigate node set with an
independent 365-day axis and could invert a trend.  Trend claims therefore
remain disabled until independent SQL reconciliation explicitly verifies this
new range/universe/bucket/narrative pipeline.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.semantic.flow_queries import (
    FLOWS_RELATION,
    LABELS_RELATION,
    PRICES_RELATION,
    TOKENS_META_RELATION,
    build_flow_labels_sql,
    build_timeline_bucket_coverage_sql,
    build_timeline_bucket_edges_sql,
    build_timeline_global_coverage_sql,
    build_timeline_universe_sql,
    flow_edge_id,
)
from cerebro_mcp.semantic.address_semantics import (
    is_structural_terminal,
    money_event_kind,
    structural_terminal_label,
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

_GRAINS = ("day", "week", "month")
_MONEY_PROFILE = "token_transfers"


def _step_bucket(value: date, grain: str) -> date:
    if grain == "day":
        return value + timedelta(days=1)
    if grain == "week":
        return value + timedelta(days=7)
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def _floor_bucket(value: date, grain: str) -> date:
    if grain == "day":
        return value
    if grain == "week":
        return value - timedelta(days=value.weekday())
    return value.replace(day=1)


def _bucket_window(t0: str, t1_exclusive: str, grain: str) -> tuple[str, str, int]:
    """Bucketed axis enclosing the exact half-open query window."""
    first = date.fromisoformat(str(t0)[:10])
    end = date.fromisoformat(str(t1_exclusive)[:10])
    if end <= first:
        raise ValueError("t1 must be later than t0")
    start_bucket = _floor_bucket(first, grain)
    # t1 is exclusive.  If it is itself a bucket boundary, do not manufacture
    # a trailing bucket outside the applied Money Trail window.
    last_included = end - timedelta(days=1)
    end_bucket = _step_bucket(_floor_bucket(last_included, grain), grain)
    count = 0
    cursor = start_bucket
    while cursor < end_bucket:
        count += 1
        cursor = _step_bucket(cursor, grain)
    return start_bucket.isoformat(), end_bucket.isoformat(), count


def _bucket_axis(start: str, end_exclusive: str, grain: str) -> list[str]:
    current = date.fromisoformat(start)
    end = date.fromisoformat(end_exclusive)
    buckets: list[str] = []
    while current < end:
        buckets.append(current.isoformat())
        current = _step_bucket(current, grain)
    return buckets


def _default_window(range_days: int) -> tuple[str, str, int]:
    days = max(1, int(range_days or constants.FLOWS_DEFAULT_RANGE_DAYS))
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days)
    return (
        f"{start.isoformat()} 00:00:00",
        f"{end.isoformat()} 00:00:00",
        days,
    )


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _bucket_row(row: list[Any]) -> dict[str, Any] | None:
    """Normalize the rich bucket contract and older eight-column fixtures."""

    if len(row) < 8:
        return None
    if len(row) >= 13:
        return {
            "source": str(row[0]).lower(),
            "target": str(row[1]).lower(),
            "token": str(row[2]).lower(),
            "symbol": str(row[3] or ""),
            "bucket": str(row[4]),
            "raw": _decimal(row[5]),
            "normalized": _decimal(row[6]),
            "known_usd": _decimal(row[7]),
            "transfer_count": int(row[8] or 0),
            "priced_rows": int(row[9] or 0),
            "source_rows": int(row[10] or 0),
            "unknown_price_rows": int(row[11] or 0),
            "unknown_decimals_rows": int(row[12] or 0),
        }

    # Transitional contract: normalized/raw token amounts and exact price-row
    # coverage were not yet published. Preserve nullability instead of
    # fabricating token amounts from USD.
    known_usd = _decimal(row[5])
    unknown = int(row[7] or 0)
    return {
        "source": str(row[0]).lower(),
        "target": str(row[1]).lower(),
        "token": str(row[2]).lower(),
        "symbol": str(row[3] or ""),
        "bucket": str(row[4]),
        "raw": None,
        "normalized": None,
        "known_usd": known_usd,
        "transfer_count": int(row[6] or 0),
        "priced_rows": 1 if known_usd is not None and unknown == 0 else 0,
        "source_rows": 1,
        "unknown_price_rows": unknown,
        "unknown_decimals_rows": 0,
    }


def _sum_nullable(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return left + right


def _usd_effects(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    """Decompose measured USD change when both buckets are fully priced."""

    def state(value: dict[str, Any] | None) -> tuple[Decimal, Decimal, float]:
        if value is None:
            return Decimal(0), Decimal(0), 1.0
        amount = value.get("normalized")
        usd = value.get("known_usd")
        coverage = float(value.get("price_coverage") or 0)
        if amount is None or usd is None:
            raise ValueError
        return amount, usd, coverage

    try:
        q0, u0, c0 = state(before)
        q1, u1, c1 = state(after)
    except (TypeError, ValueError):
        return None, None
    if c0 < 1 or c1 < 1:
        return None, None
    if q0 == 0 and q1 == 0:
        return 0.0, 0.0
    if q0 == 0:
        return float(u1), 0.0
    if q1 == 0:
        return float(-u0), 0.0
    p0 = u0 / q0
    p1 = u1 / q1
    volume = (q1 - q0) * p0
    price = q1 * (p1 - p0)
    return float(volume), float(price)


def _narrative_rows(
    bucket_rows: list[list[Any]],
    *,
    seeds: set[str],
    buckets: list[str],
    scope_id: str,
    labels: dict[str, str] | None = None,
) -> list[list[Any]]:
    """Diff one-hop token activity without treating supply endpoints as actors.

    The comparison is keyed by direction, event kind, counterparty, token, and
    bucket. Token quantity—not USD—is the change basis, so price movement
    cannot masquerade as transfer-volume movement. USD effects are published
    only when both compared buckets have complete price coverage.
    """

    labels = labels or {}
    values: dict[str, dict[tuple[str, str, str, str], dict[str, Any]]] = {
        bucket: {} for bucket in buckets
    }
    for raw_row in bucket_rows:
        row = _bucket_row(raw_row)
        if row is None or row["bucket"] not in values:
            continue
        source, target = row["source"], row["target"]
        if source in seeds:
            direction = "out"
        elif target in seeds:
            direction = "in"
        else:
            continue
        event_kind = money_event_kind(source, target)
        if event_kind == "mint":
            counterparty = source
        elif event_kind == "burn":
            counterparty = target
        else:
            counterparty = target if direction == "out" else source
        if counterparty in seeds:
            continue
        key = (direction, event_kind, counterparty, row["token"])
        aggregate = values[row["bucket"]].get(key)
        source_rows = max(0, int(row["source_rows"]))
        priced_rows = max(0, int(row["priced_rows"]))
        if aggregate is None:
            aggregate = {
                "counterparty_label": (
                    structural_terminal_label(counterparty)
                    if is_structural_terminal(counterparty)
                    else labels.get(counterparty, "")
                ),
                "token_symbol": row["symbol"],
                "raw": row["raw"],
                "normalized": row["normalized"],
                "known_usd": row["known_usd"],
                "has_known_usd": row["known_usd"] is not None,
                "transfer_count": int(row["transfer_count"]),
                "priced_rows": priced_rows,
                "source_rows": source_rows,
                "unknown_price_rows": int(row["unknown_price_rows"]),
                "unknown_decimals_rows": int(row["unknown_decimals_rows"]),
            }
            values[row["bucket"]][key] = aggregate
        else:
            aggregate["raw"] = _sum_nullable(aggregate["raw"], row["raw"])
            aggregate["normalized"] = _sum_nullable(
                aggregate["normalized"], row["normalized"]
            )
            if row["known_usd"] is not None:
                aggregate["known_usd"] = (
                    (aggregate["known_usd"] or Decimal(0)) + row["known_usd"]
                )
                aggregate["has_known_usd"] = True
            aggregate["transfer_count"] += int(row["transfer_count"])
            aggregate["priced_rows"] += priced_rows
            aggregate["source_rows"] += source_rows
            aggregate["unknown_price_rows"] += int(row["unknown_price_rows"])
            aggregate["unknown_decimals_rows"] += int(
                row["unknown_decimals_rows"]
            )

        denominator = max(0, int(aggregate["source_rows"]))
        aggregate["price_coverage"] = (
            min(1.0, float(aggregate["priced_rows"]) / denominator)
            if denominator
            else None
        )
        if not aggregate["has_known_usd"]:
            aggregate["known_usd"] = None

    narrative: list[list[Any]] = []
    previous: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for bucket in buckets:
        current = values.get(bucket, {})
        for key in sorted(set(previous) | set(current)):
            direction, event_kind, counterparty, token = key
            before = previous.get(key)
            after = current.get(key)
            if before is None and after is not None:
                change = "first_observed"
            elif before is not None and after is None:
                change = "not_observed"
            else:
                assert before is not None and after is not None
                before_basis = before["normalized"] or before["raw"]
                after_basis = after["normalized"] or after["raw"]
                if before_basis is None or after_basis is None or before_basis == after_basis:
                    continue
                change = "increased" if after_basis > before_basis else "decreased"

            before_amount = Decimal(0) if before is None else before["normalized"]
            after_amount = Decimal(0) if after is None else after["normalized"]
            delta_amount = (
                after_amount - before_amount
                if before_amount is not None and after_amount is not None
                else None
            )
            before_usd = Decimal(0) if before is None else before["known_usd"]
            after_usd = Decimal(0) if after is None else after["known_usd"]
            delta_usd = (
                after_usd - before_usd
                if before_usd is not None and after_usd is not None
                else None
            )
            display_state = after or before
            assert display_state is not None
            volume_effect, price_effect = _usd_effects(before, after)
            current_raw = Decimal(0) if after is None else after["raw"]
            current_normalized = Decimal(0) if after is None else after["normalized"]
            narrative.append(
                [
                    bucket,
                    direction,
                    event_kind,
                    counterparty,
                    display_state["counterparty_label"] or None,
                    token or None,
                    display_state["token_symbol"] or None,
                    None if current_raw is None else str(current_raw),
                    _float(current_normalized),
                    0 if after is None else after["transfer_count"],
                    _float(before_amount),
                    _float(after_amount),
                    _float(delta_amount),
                    _float(before_usd),
                    _float(after_usd),
                    _float(delta_usd),
                    display_state.get("price_coverage"),
                    volume_effect,
                    price_effect,
                    change,
                    scope_id,
                ]
            )
        previous = current
    return narrative


def _query(
    ch: ClickHouseManager,
    sql: str,
    params: dict[str, Any],
    limit: int,
) -> list[list[Any]]:
    result = mini_apps.run_structured_query(
        ch,
        sql,
        database="dbt",
        parameters=params,
        requested_max_rows=max(1, int(limit)),
    )
    return [list(row) for row in result.rows]


def register_timeline_tools(mcp, ch: ClickHouseManager) -> dict[str, Any]:
    """Register Money Trail's Over-time loader."""

    @mcp.tool(meta=mini_apps.APP_ONLY_META)
    def load_graph_timeline(
        view_id: str,
        seed_node_id: str = "",
        seed_node_ids: list[str] | None = None,
        profiles: list[str] | None = None,
        grain: str = "",
        range_days: int = 0,
        max_rows: int = 0,
        request_id: int = 0,
        direction: str = "",
        tokens: list[str] | None = None,
        min_usd: float = -1.0,
        t0: str = "",
        t1: str = "",
        node_budget: int = 0,
    ) -> CallToolResult:
        """Load a fixed-universe, bucketed Money Trail (app-only).

        Legacy arguments and the ``load_graph_timeline`` tool name remain
        accepted for deep-link/API compatibility.  ``profiles`` no longer
        changes the dataset: Over time always uses the whitelisted transfer
        contract, and that fact is disclosed in scope provenance.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )
        state = record.view_state
        flow_state = dict(state.get("flows") or {})
        investigate = dict(state.get("investigate") or {})
        warnings: list[str] = []
        request_id = max(0, int(request_id or 0))
        scope_id = new_scope_id("timeline", request_id)

        grain = (grain or constants.TIMELINE_DEFAULT_GRAIN).strip().lower()
        if grain not in _GRAINS:
            return mini_apps.error_call_tool_result(
                f"grain must be one of {', '.join(_GRAINS)}"
            )
        if bool(t0) != bool(t1):
            return mini_apps.error_call_tool_result(
                "t0 and t1 must be provided together"
            )

        explicit_seeds = [
            value
            for value in (
                _normalize_node_id(str(seed))
                for seed in (seed_node_ids or [])
            )
            if value
        ]
        explicit_seed = _normalize_node_id(seed_node_id)
        if explicit_seed:
            explicit_seeds.insert(0, explicit_seed)
        explicit_seeds = list(dict.fromkeys(explicit_seeds))
        flow_seeds = [
            value
            for value in (
                _normalize_node_id(str(seed))
                for seed in (flow_state.get("seeds") or [])
            )
            if value
        ]
        using_flow_scope = not explicit_seeds and bool(flow_seeds)
        if explicit_seeds:
            seeds = explicit_seeds
            seed_source = (
                "explicit_seed"
                if explicit_seed and not seed_node_ids
                else "explicit_seeds"
            )
        elif flow_seeds:
            seeds = list(dict.fromkeys(flow_seeds))
            seed_source = "flows.applied"
        else:
            compatibility_seed = _normalize_node_id(
                str((investigate.get("seed") or {}).get("id") or "")
            )
            if not compatibility_seed:
                return mini_apps.error_call_tool_result(
                    "Load Money Trail first or provide seed_node_id."
                )
            seeds = [compatibility_seed]
            seed_source = "investigate_compatibility_seed"
            warnings.append(
                "No applied Money Trail seed existed; used the legacy "
                "Investigate seed with the Money Trail transfer contract."
            )
        structural_seeds = [seed for seed in seeds if is_structural_terminal(seed)]
        if structural_seeds:
            return mini_apps.error_call_tool_result(
                f"{structural_terminal_label(structural_seeds[0])} is a Mint/Burn "
                "terminal and cannot be used as an activity seed."
            )

        effective_direction = (
            direction
            or (str(flow_state.get("direction") or "") if using_flow_scope else "")
            or "out"
        ).lower()
        if effective_direction not in ("out", "in", "both"):
            return mini_apps.error_call_tool_result(
                "direction must be 'out', 'in', or 'both'"
            )
        effective_tokens = (
            [str(token).lower() for token in tokens if str(token)]
            if tokens is not None
            else (
                [str(token).lower() for token in flow_state.get("tokens") or []]
                if using_flow_scope
                else []
            )
        )
        effective_min_usd = (
            float(min_usd)
            if min_usd >= 0
            else (
                float(flow_state.get("min_usd") or 0)
                if using_flow_scope
                else constants.FLOWS_DEFAULT_MIN_USD
            )
        )
        if effective_min_usd > 0:
            warnings.append(
                f"USD minimum {effective_min_usd:g} applies only to measured "
                "USD. Wholly unpriced movements remain visible as unpriced "
                "evidence and are never treated as zero."
            )

        if t0 and t1:
            effective_t0, effective_t1 = str(t0), str(t1)
            effective_range_days = int(range_days or 0)
            window_source = "explicit_t0_t1"
        elif using_flow_scope and flow_state.get("t0") and flow_state.get("t1"):
            effective_t0 = str(flow_state["t0"])
            effective_t1 = str(flow_state["t1"])
            effective_range_days = int(flow_state.get("range_days") or 0)
            window_source = "flows.applied_window"
        else:
            effective_t0, effective_t1, effective_range_days = _default_window(
                int(range_days or constants.TIMELINE_DEFAULT_RANGE_DAYS)
            )
            window_source = "timeline.range_days"
        try:
            range_start, range_end, bucket_count = _bucket_window(
                effective_t0, effective_t1, grain
            )
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        budget = max(
            1,
            min(
                int(node_budget or constants.FLOWS_PER_HOP_NODE_BUDGET),
                constants.FLOWS_MAX_NODES,
            ),
        )
        row_cap = max(1, int(max_rows or constants.TIMELINE_MAX_ROWS))
        if profiles is not None and set(profiles) != {_MONEY_PROFILE}:
            warnings.append(
                "Legacy timeline profile filters were ignored; Over time uses "
                f"only dbt.{FLOWS_RELATION}."
            )

        required_sources = [
            (
                FLOWS_RELATION,
                ("date", "token_address", "from", "to", "amount_raw", "transfer_count"),
                "primary",
            ),
            (TOKENS_META_RELATION, ("token_address", "token", "decimals"), "enrichment"),
            (PRICES_RELATION, ("symbol", "date", "price"), "enrichment"),
        ]
        contracts: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        for relation, columns, role in required_sources:
            horizon_column = "date" if "date" in columns else None
            contract = validate_source_contract(
                ch,
                relation,
                columns,
                probe_horizon=True,
                horizon_column=horizon_column,
            )
            contracts.append(contract)
            sources.append(
                source_record(
                    kind="dbt_aggregate",
                    name=f"dbt.{relation}",
                    role=role,
                    status="ok" if contract["ok"] else "error",
                    horizon=contract.get("horizon"),
                    horizon_basis=contract.get("horizon_basis"),
                    fetched_at=contract.get("freshness_checked_at"),
                    error=contract.get("error"),
                )
            )
            if not contract["ok"]:
                warnings.append(
                    f"source contract failed for dbt.{relation}: "
                    f"{contract.get('error') or 'unknown error'}"
                )

        # Address labels are presentation enrichment only. The transfer
        # narrative remains usable—and full addresses remain visible—when the
        # optional relation is missing or stale.
        label_contract = validate_source_contract(
            ch,
            LABELS_RELATION,
            ("address", "project", "sector", "introduced_at"),
            probe_horizon=True,
            horizon_column="introduced_at",
        )
        sources.append(
            source_record(
                kind="dbt_aggregate",
                name=f"dbt.{LABELS_RELATION}",
                role="enrichment",
                status="ok" if label_contract["ok"] else "error",
                horizon=label_contract.get("horizon"),
                horizon_basis=label_contract.get("horizon_basis"),
                fetched_at=label_contract.get("freshness_checked_at"),
                error=label_contract.get("error"),
            )
        )
        if not label_contract["ok"]:
            warnings.append(
                "Address-label enrichment is unavailable; full addresses are "
                "shown without attribution labels."
            )

        universe_rows: list[list[Any]] = []
        global_rows: list[list[Any]] = []
        bucket_rows: list[list[Any]] = []
        bucket_total_rows: list[list[Any]] = []
        query_failures: list[str] = []

        if all(contract["ok"] for contract in contracts):
            queries = [
                (
                    "full-range universe",
                    build_timeline_universe_sql(
                        seed_ids=seeds,
                        direction=effective_direction,
                        t0=effective_t0,
                        t1_exclusive=effective_t1,
                        min_usd=effective_min_usd,
                        tokens=effective_tokens,
                        limit=budget,
                    ),
                    budget + 1,
                    "universe",
                ),
                (
                    "global coverage",
                    build_timeline_global_coverage_sql(
                        seed_ids=seeds,
                        direction=effective_direction,
                        t0=effective_t0,
                        t1_exclusive=effective_t1,
                        min_usd=effective_min_usd,
                        tokens=effective_tokens,
                    ),
                    1,
                    "global",
                ),
            ]
            for label, (sql, params), limit, target in queries:
                try:
                    rows = _query(ch, sql, params, limit)
                    if target == "universe":
                        universe_rows = rows
                    else:
                        global_rows = rows
                except Exception as exc:
                    query_failures.append(f"{label}: {exc}")

        universe_policy_truncated = len(universe_rows) > budget
        admitted_universe = universe_rows[:budget]
        counterparties = [str(row[0]) for row in admitted_universe if row]
        universe_ids = list(dict.fromkeys([*seeds, *counterparties]))

        if all(contract["ok"] for contract in contracts) and not query_failures:
            bucket_queries = [
                (
                    "fixed-universe buckets",
                    build_timeline_bucket_edges_sql(
                        seed_ids=seeds,
                        universe_ids=universe_ids,
                        direction=effective_direction,
                        t0=effective_t0,
                        t1_exclusive=effective_t1,
                        grain=grain,
                        min_usd=effective_min_usd,
                        tokens=effective_tokens,
                        limit=row_cap,
                    ),
                    row_cap + 1,
                    "buckets",
                ),
                (
                    "per-bucket coverage",
                    build_timeline_bucket_coverage_sql(
                        seed_ids=seeds,
                        direction=effective_direction,
                        t0=effective_t0,
                        t1_exclusive=effective_t1,
                        grain=grain,
                        min_usd=effective_min_usd,
                        tokens=effective_tokens,
                    ),
                    bucket_count + 1,
                    "coverage",
                ),
            ]
            for label, (sql, params), limit, target in bucket_queries:
                try:
                    rows = _query(ch, sql, params, limit)
                    if target == "buckets":
                        bucket_rows = rows
                    else:
                        bucket_total_rows = rows
                except Exception as exc:
                    query_failures.append(f"{label}: {exc}")

        bucket_rows_truncated = len(bucket_rows) > row_cap
        bucket_rows = bucket_rows[:row_cap]
        if query_failures:
            warnings.extend(query_failures)
            sources[0]["status"] = "error"
            sources[0]["error"] = "; ".join(query_failures)

        labels: dict[str, str] = {}
        label_enrichment_failed = not label_contract["ok"]
        if label_contract["ok"] and counterparties:
            try:
                label_sql, label_params = build_flow_labels_sql(counterparties)
                for label_row in _query(
                    ch, label_sql, label_params, max(1, len(counterparties) + 1)
                ):
                    if not label_row:
                        continue
                    address = str(label_row[0]).lower()
                    label = str(label_row[1] or "") if len(label_row) > 1 else ""
                    if label:
                        labels[address] = label
            except Exception as exc:
                label_enrichment_failed = True
                sources[-1]["status"] = "error"
                sources[-1]["error"] = str(exc)
                warnings.append(
                    f"Address-label enrichment failed: {exc}; full addresses remain available."
                )

        parsed_bucket_rows = [
            parsed for row in bucket_rows if (parsed := _bucket_row(row)) is not None
        ]
        structural_ids = sorted(
            {
                endpoint
                for row in parsed_bucket_rows
                for endpoint in (row["source"], row["target"])
                if is_structural_terminal(endpoint)
            }
        )
        node_rows = [
            [
                node_id,
                "structural_terminal" if is_structural_terminal(node_id) else "address",
                (
                    structural_terminal_label(node_id)
                    if is_structural_terminal(node_id)
                    else labels.get(node_id, short_id(node_id))
                ),
                [_MONEY_PROFILE],
            ]
            for node_id in dict.fromkeys([*universe_ids, *structural_ids])
        ]
        edge_rows: list[list[Any]] = []
        known_usd = 0.0
        unknown_usd_rows = 0
        unique_edges: set[str] = set()
        seeds_set = set(seeds)
        shown_by_bucket: dict[str, dict[str, Any]] = {}
        for row in parsed_bucket_rows:
            source, target, token = row["source"], row["target"], row["token"]
            bucket = row["bucket"]
            amount_usd = _float(row["known_usd"])
            transfer_count = int(row["transfer_count"])
            unknown = int(row["unknown_price_rows"]) + int(
                row["unknown_decimals_rows"]
            )
            event_kind = money_event_kind(source, target)
            edge_id = flow_edge_id(source, target, token, event_kind)
            unique_edges.add(edge_id)
            edge_rows.append(
                [
                    edge_id,
                    source,
                    target,
                    _MONEY_PROFILE,
                    amount_usd,
                    transfer_count,
                    True,
                    bucket,
                    bucket,
                ]
            )
            stats = shown_by_bucket.setdefault(
                bucket,
                {
                    "counterparties": set(),
                    "edges": 0,
                    "known_usd": 0.0,
                    "unknown": 0,
                    "supply_events": 0,
                },
            )
            if source not in seeds_set and not is_structural_terminal(source):
                stats["counterparties"].add(source)
            if target not in seeds_set and not is_structural_terminal(target):
                stats["counterparties"].add(target)
            if event_kind != "transfer":
                stats["supply_events"] += 1
            stats["edges"] += 1
            stats["unknown"] += unknown
            if amount_usd is not None:
                stats["known_usd"] += amount_usd
                known_usd += amount_usd
            unknown_usd_rows += unknown

        global_total_counterparties: int | None = None
        global_total_edges: int | None = None
        global_total_usd: float | None = None
        global_unknown_usd_edges = 0
        excluded_unknown_usd_edges: int | None = None
        global_supply_event_edges: int | None = None
        if global_rows and len(global_rows[0]) >= 3:
            global_total_counterparties = int(global_rows[0][0] or 0)
            global_total_edges = int(global_rows[0][1] or 0)
            global_total_usd = (
                None if global_rows[0][2] is None else float(global_rows[0][2])
            )
            # The final two columns were added to make the positive-USD
            # eligibility boundary forensic.  Old fixtures/results are
            # treated as having no unknown candidates, not as an unknown
            # total, because their supplied total is independently explicit.
            global_unknown_usd_edges = (
                int(global_rows[0][4] or 0) if len(global_rows[0]) > 4 else 0
            )
            excluded_unknown_usd_edges = (
                int(global_rows[0][5] or 0) if len(global_rows[0]) > 5 else 0
            )
            global_supply_event_edges = (
                int(global_rows[0][6] or 0) if len(global_rows[0]) > 6 else 0
            )
        if excluded_unknown_usd_edges:
            # Defensive compatibility warning: Revision 3's query contract
            # admits unpriced groups, so a nonzero legacy value means an older
            # relation/query result answered part of this load.
            warnings.append(
                f"A legacy coverage result reported {excluded_unknown_usd_edges} "
                "unpriced edge group(s) excluded by the USD minimum. The scope "
                "is partial because Revision 3 retains those groups."
            )

        total_by_bucket = {
            str(row[0]): row for row in bucket_total_rows if len(row) >= 5
        }
        bucket_coverage: list[dict[str, Any]] = []
        total_bucket_rows = 0
        total_unknown_rows = 0
        for bucket in _bucket_axis(range_start, range_end, grain):
            shown = shown_by_bucket.get(
                bucket,
                {
                    "counterparties": set(),
                    "edges": 0,
                    "known_usd": 0.0,
                    "unknown": 0,
                    "supply_events": 0,
                },
            )
            total = total_by_bucket.get(bucket)
            total_counterparties = int(total[1] or 0) if total else 0
            total_edges = int(total[2] or 0) if total else 0
            total_known_usd = (
                None
                if total is not None and total[3] is None
                else float((total[3] if total is not None else 0) or 0)
            )
            total_unknown = int(total[4] or 0) if total else 0
            total_supply_events = int(total[5] or 0) if total and len(total) > 5 else 0
            total_bucket_rows += total_edges
            total_unknown_rows += total_unknown
            complete_total_usd = None if total_unknown else total_known_usd
            retained = (
                shown["known_usd"] / complete_total_usd
                if complete_total_usd not in (None, 0)
                else (1.0 if complete_total_usd == 0 else None)
            )
            bucket_coverage.append(
                {
                    "bucket": bucket,
                    "counterparties": {
                        "shown": len(shown["counterparties"]),
                        "total": total_counterparties,
                    },
                    "edges": {"shown": shown["edges"], "total": total_edges},
                    "supply_events": {
                        "shown": shown["supply_events"],
                        "total": total_supply_events,
                    },
                    "usd": {
                        "known": round(float(shown["known_usd"]), 6),
                        "total": (
                            None
                            if complete_total_usd is None
                            else round(complete_total_usd, 6)
                        ),
                        "unknown_rows": total_unknown,
                        "retained_fraction": retained,
                    },
                }
            )

        if total_unknown_rows:
            warnings.append(
                f"{total_unknown_rows} bucketed daily source row(s) lacked "
                "price enrichment; narrative USD values are known subtotals "
                "and complete USD totals remain unknown."
            )

        buckets = _bucket_axis(range_start, range_end, grain)
        narrative_rows = _narrative_rows(
            bucket_rows,
            seeds=seeds_set,
            buckets=buckets,
            scope_id=scope_id,
            labels=labels,
        )
        truncated = bool(
            universe_policy_truncated
            or bucket_rows_truncated
            or (excluded_unknown_usd_edges or 0) > 0
            or (
                global_total_counterparties is not None
                and len(counterparties) < global_total_counterparties
            )
        )
        source_contract_failed = any(not contract["ok"] for contract in contracts)
        if source_contract_failed or (query_failures and not edge_rows):
            status = "failed"
        elif (
            query_failures
            or truncated
            or total_unknown_rows
            or global_unknown_usd_edges
            or label_enrichment_failed
        ):
            status = "partial"
        else:
            status = "ready"

        result_observed_through = max(
            (str(row[0]) for row in bucket_total_rows if row and row[0]),
            default=None,
        )
        data_horizon = contracts[0].get("horizon") if contracts else None
        complete_total_usd = (
            global_total_usd
            if (
                global_total_usd is not None
                and total_unknown_rows == 0
                and global_unknown_usd_edges == 0
                and (excluded_unknown_usd_edges or 0) == 0
            )
            else None
        )
        scope = forensic_scope(
            scope_id=scope_id,
            request_id=request_id,
            status=status,
            t0=effective_t0,
            t1=effective_t1,
            window_source=window_source,
            data_horizon=data_horizon,
            result_observed_through=result_observed_through,
            sources=sources,
            rows_returned=len(narrative_rows),
            rows_total=(
                len(narrative_rows)
                if not query_failures and not truncated
                else None
            ),
            nodes_returned=len(node_rows),
            nodes_total=(
                len(seeds) + global_total_counterparties + len(structural_ids)
                if global_total_counterparties is not None and not bucket_rows_truncated
                else None
            ),
            edges_returned=len(unique_edges),
            edges_total=global_total_edges,
            known_usd=round(known_usd, 6),
            total_usd=(
                None if complete_total_usd is None else round(complete_total_usd, 6)
            ),
            unknown_usd_rows=unknown_usd_rows,
            truncated=truncated,
            truncation_rule=(
                f"one full-range USD-ranked universe capped at {budget} "
                f"counterparties; bucket rows capped at {row_cap}; "
                + (
                    f"positive min_usd={effective_min_usd:g} filters only "
                    "measured-USD groups while wholly unpriced groups remain visible"
                    if effective_min_usd > 0
                    else "min_usd=0 includes wholly unpriced full-range edge groups"
                )
            ),
            coverage_note=(
                "one-hop whitelisted ERC-20 activity only; ordinary counterparties "
                "are ranked once over the entire applied Money Trail range and "
                "fixed across every bucket; Mint/Burn terminals are reported "
                "separately and never counted as counterparties"
            ),
            residuals=(
                "token coverage is limited to the warehouse whitelist",
                "daily grain cannot establish same-day transaction ordering",
                "native xDAI and internal calls are not represented",
                "bridge deposit rows are not part of this whitelisted-transfer temporal contract",
                "trend claims remain disabled until independent SQL reconciliation",
            ),
            warnings=warnings,
            verification_status="unverified",
            verification_method="pending independent SQL reconciliation",
        )
        flow_scope = flow_state.get("scope") or {}
        scope["money_contract"] = {
            "source_flow_scope_id": (
                flow_scope.get("scope_id") if isinstance(flow_scope, dict) else None
            ),
            "seed_source": seed_source,
            "seed_ids": seeds,
            "direction": effective_direction,
            "tokens": sorted(effective_tokens),
            "min_usd": effective_min_usd,
            "t0": effective_t0,
            "t1": effective_t1,
            "node_budget": budget,
        }
        scope["usd_filter_coverage"] = {
            "min_usd": effective_min_usd,
            "eligibility": (
                "priced_amount_at_or_above_minimum_or_unpriced"
                if effective_min_usd > 0
                else "priced_or_wholly_unpriced"
            ),
            "eligible_unknown_usd_edges": global_unknown_usd_edges,
            "excluded_unknown_usd_edges": excluded_unknown_usd_edges,
            "counting_basis": "full_range_source_target_token_groups",
        }
        scope["supply_events"] = {
            "shown": sum(
                coverage["supply_events"]["shown"] for coverage in bucket_coverage
            ),
            "total": (
                sum(
                    coverage["supply_events"]["total"]
                    for coverage in bucket_coverage
                )
                if not query_failures
                else None
            ),
            "full_range_edge_groups": global_supply_event_edges,
            "counting_basis": "bucket_source_target_token_rows",
            "counterparty_counts_exclude_structural_terminals": True,
        }
        scope["universe"] = {
            "source": "full_range_whitelisted_transfers_usd_desc",
            "fixed_across_buckets": True,
            "counterparties": {
                "shown": len(counterparties),
                "total": global_total_counterparties,
            },
            "node_budget": budget,
        }
        scope["bucket_coverage"] = bucket_coverage
        scope["reconciliation"] = {
            "status": "unverified",
            "trend_claims_enabled": False,
        }

        mini_apps.attach_dataset(
            view_id,
            "timeline_nodes",
            dataset_from_rows(constants.NODES_COLUMNS, node_rows, "timeline_nodes"),
        )
        mini_apps.attach_dataset(
            view_id,
            "timeline_edges",
            dataset_from_rows(
                constants.TIMELINE_EDGES_COLUMNS, edge_rows, "timeline_edges"
            ),
        )
        mini_apps.attach_dataset(
            view_id,
            "timeline_narrative",
            dataset_from_rows(
                constants.TIMELINE_NARRATIVE_COLUMNS,
                narrative_rows,
                "timeline_narrative",
            ),
        )
        mini_apps.patch_view_state(
            view_id,
            {
                "timeline": {
                    "anchor": {"id": seeds[0], "kind": "address"},
                    "seed_ids": seeds,
                    "scope": "money",
                    "forensic_scope": scope,
                    "profiles": [_MONEY_PROFILE],
                    "profile_shapes": {_MONEY_PROFILE: "flow"},
                    "direction": effective_direction,
                    "tokens": sorted(effective_tokens),
                    "min_usd": effective_min_usd,
                    "node_budget": budget,
                    "grain": grain,
                    "range_days": effective_range_days,
                    "range_start": range_start,
                    "range_end": range_end,
                    "bucket_count": bucket_count,
                    "window_buckets": int(
                        (state.get("timeline") or {}).get("window_buckets")
                        or constants.TIMELINE_DEFAULT_WINDOW_BUCKETS
                    ),
                },
                "dataset_scopes": {
                    "timeline_nodes": scope_id,
                    "timeline_edges": scope_id,
                    "timeline_narrative": scope_id,
                },
                "warnings": warnings,
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        return mini_apps.payload_to_call_tool_result(
            build_payload(updated),
            summary_text=(
                f"Over time loaded for {len(seeds)} Money Trail seed(s): "
                f"{len(counterparties)} of "
                f"{global_total_counterparties if global_total_counterparties is not None else '?'} "
                f"counterparties, {len(edge_rows)} bucket edge row(s); trend "
                "claims disabled pending reconciliation."
            ),
        )

    mini_apps.mark_app_only("load_graph_timeline")
    return {"load_graph_timeline": load_graph_timeline}
