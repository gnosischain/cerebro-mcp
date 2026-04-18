"""Yield Opportunities mini app."""

from __future__ import annotations

import importlib.resources
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.mini_app_cache import CachedDataset
from cerebro_mcp.mini_app_models import DatasetStats, MiniAppPayload, SummaryCard
from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.mini_apps import MiniAppQueryError

logger = logging.getLogger(__name__)


YIELD_OPPORTUNITIES_APP_ID = "yield_opportunities"
YIELD_OPPORTUNITIES_URI = "ui://cerebro/yield_opportunities"

DEFAULT_TITLE = "Yield Opportunities"
DEFAULT_SORT = "headline_rate_desc"
VALID_SORTS = {
    "headline_rate_desc",
    "headline_rate_asc",
    "tvl_desc",
    "fees_7d_desc",
    "volume_7d_desc",
    "utilization_desc",
}
VALID_TYPES = {"", "lp", "lending"}
VALID_SIMULATION_MODES = {"forward", "historical_replay"}

_BUNDLED_YIELD_OPPORTUNITIES_HTML: str | None = None


def get_yield_opportunities_html() -> str:
    """Load the Vite-built single-file React app from the static package."""
    global _BUNDLED_YIELD_OPPORTUNITIES_HTML
    if _BUNDLED_YIELD_OPPORTUNITIES_HTML is None:
        try:
            _BUNDLED_YIELD_OPPORTUNITIES_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/yield_opportunities.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_YIELD_OPPORTUNITIES_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>yield_opportunities.html not built</div>"
                "</body></html>"
            )
    return _BUNDLED_YIELD_OPPORTUNITIES_HTML


_OPPORTUNITIES_SQL = """
SELECT
  o.type,
  o.token,
  o.name,
  lower(o.address) AS address,
  o.pool_key,
  o.protocol,
  o.yield_apr,
  o.yield_apy,
  o.borrow_apy,
  o.tvl,
  o.total_supplied,
  o.total_borrowed,
  o.fees_7d,
  o.volume_usd_7d,
  o.net_apr_7d,
  o.utilization_rate,
  o.fee_pct,
  o.rate_trend_14d,
  lower(lmm.reserve_address) AS reserve_address,
  CASE
    WHEN lower(o.type) = 'lp'
      THEN concat('lp:', lower(o.protocol), ':', lower(o.address))
    ELSE concat(
      'lending:',
      lower(o.protocol),
      ':',
      lower(coalesce(lmm.reserve_address, o.address))
    )
  END AS opportunity_key,
  CASE
    WHEN lower(o.type) = 'lp' THEN o.net_apr_7d
    ELSE o.yield_apy
  END AS headline_rate,
  CASE
    WHEN lower(o.type) = 'lp'
      THEN coalesce(o.net_apr_7d, 0) - coalesce(o.yield_apr, 0)
    ELSE NULL
  END AS lvr_apr_7d
FROM api_execution_yields_opportunities_latest o
LEFT JOIN lending_market_mapping lmm
  ON lower(lmm.protocol) = lower(o.protocol)
 AND lower(lmm.supply_token_address) = lower(o.address)
ORDER BY headline_rate DESC NULLS LAST, lower(o.protocol), lower(o.token), lower(o.name)
LIMIT 1000
"""

_LP_HISTORY_SQL = """
SELECT
  date,
  {opportunity_key:String} AS opportunity_key,
  'LP' AS type,
  token,
  replaceOne(pool, concat(' • ', protocol), '') AS name,
  lower(pool_address) AS address,
  pool AS pool_key,
  protocol,
  fee_apr_7d,
  coalesce(net_apr_7d, 0) - coalesce(fee_apr_7d, 0) AS lvr_apr_7d,
  net_apr_7d,
  tvl_usd AS tvl,
  fees_usd_daily,
  volume_usd_daily
FROM fct_execution_pools_daily
WHERE lower(pool_address) = {address:String}
  AND lower(protocol) = {protocol:String}
ORDER BY date DESC
LIMIT 400
"""

_LENDING_HISTORY_SQL = """
WITH utilization AS (
  SELECT
    protocol,
    token_address,
    date,
    cumulative_scaled_supply,
    cumulative_scaled_borrow,
    utilization_rate
  FROM int_execution_lending_aave_utilization_daily
)
SELECT
  a.date,
  {opportunity_key:String} AS opportunity_key,
  'Lending' AS type,
  a.symbol AS token,
  a.symbol AS name,
  lower(coalesce(m.reserve_address, m.supply_token_address)) AS address,
  CAST(NULL, 'Nullable(String)') AS pool_key,
  a.protocol,
  a.apy_daily AS yield_apy,
  a.borrow_apy_variable_daily AS borrow_apy,
  u.utilization_rate,
  (toFloat64(u.cumulative_scaled_supply) * a.liquidity_index / 1e27)
      / power(10, m.decimals) * coalesce(pr.price, 0) AS total_supplied,
  (toFloat64(u.cumulative_scaled_borrow) * a.variable_borrow_index / 1e27)
      / power(10, m.decimals) * coalesce(pr.price, 0) AS total_borrowed
FROM int_execution_lending_aave_daily a
INNER JOIN lending_market_mapping m
  ON lower(m.protocol) = lower(a.protocol)
 AND lower(m.reserve_address) = {address:String}
LEFT JOIN utilization u
  ON lower(u.protocol) = lower(a.protocol)
 AND lower(u.token_address) = lower(a.token_address)
 AND u.date = a.date
LEFT JOIN int_execution_token_prices_daily pr
  ON pr.symbol = a.symbol
 AND pr.date = a.date
WHERE lower(a.protocol) = {protocol:String}
  AND lower(a.token_address) = lower(m.reserve_address)
ORDER BY a.date DESC
LIMIT 400
"""


@dataclass(frozen=True)
class OpportunityRef:
    key: str
    opportunity_type: str
    protocol: str
    address: str
    token: str
    name: str


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _rows_dataset(
    *,
    columns: list[str],
    rows: list[list[Any]],
    sql: str,
    database: str = "dbt",
    parameters: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> CachedDataset:
    column_types = ["str" for _ in columns]
    stats = DatasetStats(
        row_count=len(rows),
        rows_returned=len(rows),
        mode="exact_bounded",
        sample_source_rows=len(rows),
        elapsed_seconds=0.0,
        warnings=warnings or [],
    )
    return CachedDataset(
        columns=columns,
        column_types=column_types,
        rows=rows,
        stats=stats,
        sql=sql,
        database=database,
        parameters=parameters,
    )


def _empty_history_dataset(label: str) -> CachedDataset:
    return _rows_dataset(
        columns=["date", "opportunity_key"],
        rows=[],
        sql=f"-- {label} empty --",
        warnings=[f"{label} not loaded"],
    )


def _date_from_string(value: str, *, default: date | None = None) -> date:
    if not value:
        if default is None:
            raise ValueError("date is required")
        return default
    return datetime.strptime(value, "%Y-%m-%d").date()


def _coerce_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_rate(rate: float) -> float:
    if math.isnan(rate) or math.isinf(rate):
        return 0.0
    if abs(rate) > 1.5:
        return rate / 100.0
    return rate


def _parse_opportunity_key(opportunity_key: str) -> OpportunityRef:
    parts = opportunity_key.strip().lower().split(":")
    if len(parts) != 3:
        raise ValueError(
            "opportunity_key must have the form "
            "'lp:<protocol>:<pool_address>' or 'lending:<protocol>:<reserve_address>'"
        )
    opportunity_type, protocol, address = parts
    if opportunity_type not in {"lp", "lending"}:
        raise ValueError("opportunity_key type must be 'lp' or 'lending'")
    if not protocol:
        raise ValueError("opportunity_key protocol is required")
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError("opportunity_key address must be a 0x-prefixed address")
    return OpportunityRef(
        key=opportunity_key.strip().lower(),
        opportunity_type=opportunity_type,
        protocol=protocol,
        address=address,
        token="",
        name="",
    )


def _row_to_mapping(dataset: CachedDataset, row: list[Any]) -> dict[str, Any]:
    return {
        dataset.columns[idx]: row[idx] if idx < len(row) else None
        for idx in range(len(dataset.columns))
    }


def _find_opportunity(
    dataset: CachedDataset | None, opportunity_key: str
) -> dict[str, Any] | None:
    if dataset is None:
        return None
    key = opportunity_key.strip().lower()
    try:
        key_index = dataset.columns.index("opportunity_key")
    except ValueError:
        return None
    for row in dataset.rows:
        if key_index < len(row) and str(row[key_index]).lower() == key:
            return _row_to_mapping(dataset, row)
    return None


def _collect_titles(record: mini_apps.ViewRecord) -> dict[str, str]:
    return {
        "opportunities": "Ranked opportunities",
        "selected_history": "Selected opportunity history",
        "compare_history": "Comparison history",
    }


def _build_summary_cards(record: mini_apps.ViewRecord) -> list[SummaryCard]:
    opportunities = record.datasets.get("opportunities")
    selected_key = str(record.view_state.get("selected_opportunity_key", "")).lower()
    compare_key = str(record.view_state.get("compare_with", "")).lower()

    cards: list[SummaryCard] = []
    if opportunities is not None:
        rows = opportunities.rows
        cards.append(
            SummaryCard(
                label="Opportunities",
                value=f"{len(rows):,}",
                tone="neutral",
            )
        )
        if rows:
            type_idx = opportunities.columns.index("type")
            protocol_idx = opportunities.columns.index("protocol")
            cards.append(
                SummaryCard(
                    label="LP / lending",
                    value=(
                        f"{sum(1 for row in rows if str(row[type_idx]).lower() == 'lp')}"
                        f" / "
                        f"{sum(1 for row in rows if str(row[type_idx]).lower() != 'lp')}"
                    ),
                    tone="neutral",
                )
            )
            cards.append(
                SummaryCard(
                    label="Protocols",
                    value=str(
                        len(
                            {
                                str(row[protocol_idx]).lower()
                                for row in rows
                                if protocol_idx < len(row) and row[protocol_idx]
                            }
                        )
                    ),
                    tone="neutral",
                )
            )

    selected = _find_opportunity(opportunities, selected_key)
    if selected:
        headline = _coerce_number(selected.get("headline_rate"))
        cards.append(
            SummaryCard(
                label="Selected",
                value=str(selected.get("name") or selected.get("token") or "Selected"),
                delta=f"{headline:.2f}%",
                tone="positive" if headline > 0 else "warning",
            )
        )

    if compare_key:
        compare = _find_opportunity(opportunities, compare_key)
        if compare:
            cards.append(
                SummaryCard(
                    label="Comparing",
                    value=str(compare.get("name") or compare.get("token") or "Comparison"),
                    tone="warning",
                )
            )

    simulation = record.view_state.get("simulation")
    if isinstance(simulation, dict) and simulation.get("ending_value_usd") is not None:
        cards.append(
            SummaryCard(
                label="Simulated ending value",
                value=f"${_coerce_number(simulation.get('ending_value_usd')):,.2f}",
                delta=f"{_coerce_number(simulation.get('return_pct')):.2f}%",
                tone="positive"
                if _coerce_number(simulation.get("gain_usd")) >= 0
                else "negative",
            )
        )

    return cards[:5]


def _build_payload_from_record(
    record: mini_apps.ViewRecord,
    *,
    warnings: list[str] | None = None,
) -> MiniAppPayload:
    titles = _collect_titles(record)
    descriptors = {
        key: mini_apps.build_dataset_descriptor(
            key=key,
            dataset=dataset,
            title=titles.get(key, key.replace("_", " ").title()),
        )
        for key, dataset in record.datasets.items()
    }
    all_warnings = _dedupe_strings(
        (warnings or [])
        + mini_apps.collect_dataset_warnings(*record.datasets.values())
    )
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=record.view_id,
        app_id=YIELD_OPPORTUNITIES_APP_ID,
        title=record.title,
        status="ready",
        summary_cards=_build_summary_cards(record),
        datasets=descriptors,
        view_state=record.view_state,
        provenance={"source": "dbt+runtime"},
        warnings=all_warnings,
    )


def _load_opportunities_dataset(ch: ClickHouseManager) -> CachedDataset:
    return mini_apps.load_bounded_dataset(
        ch,
        _OPPORTUNITIES_SQL,
        database="dbt",
        parameters=None,
    )


def _load_history_dataset(
    ch: ClickHouseManager,
    opportunity_key: str,
) -> CachedDataset:
    ref = _parse_opportunity_key(opportunity_key)
    parameters = {
        "opportunity_key": ref.key,
        "address": ref.address,
        "protocol": ref.protocol,
    }
    sql = _LP_HISTORY_SQL if ref.opportunity_type == "lp" else _LENDING_HISTORY_SQL
    return mini_apps.load_bounded_dataset(
        ch,
        sql,
        database="dbt",
        parameters=parameters,
    )


def _seed_view_state(
    *,
    query: str,
    token: str,
    opportunity_type: str,
    protocol: str,
) -> dict[str, Any]:
    return {
        "query": query,
        "filters": {
            "token": token,
            "type": opportunity_type.lower(),
            "protocol": protocol,
        },
        "sort": DEFAULT_SORT,
        "active_tab": "Overview",
        "selected_opportunity_key": "",
        "compare_with": "",
        "loaded_detail_keys": [],
        "simulation": None,
        "mobile_panel": "ranking",
    }


def _build_forward_simulation(
    *,
    opportunity: dict[str, Any],
    principal: float,
    start_date: str,
    end_date: str,
    compound: bool,
) -> dict[str, Any]:
    start = _date_from_string(
        start_date,
        default=datetime.now(timezone.utc).date(),
    )
    default_end = start + timedelta(days=365)
    end = _date_from_string(end_date, default=default_end)
    if end <= start:
        raise ValueError("end_date must be after start_date")

    raw_rate = (
        _coerce_number(opportunity.get("net_apr_7d"))
        if str(opportunity.get("type", "")).lower() == "lp"
        else _coerce_number(opportunity.get("yield_apy"))
    )
    annual_rate = _normalize_rate(raw_rate)
    if str(opportunity.get("type", "")).lower() == "lp":
        daily_rate = annual_rate / 365.0
    else:
        daily_rate = math.pow(max(0.0, 1.0 + annual_rate), 1.0 / 365.0) - 1.0

    days = max((end - start).days, 1)
    current = principal
    gain = 0.0
    series: list[dict[str, Any]] = []
    for day_offset in range(days + 1):
        current_day = start + timedelta(days=day_offset)
        if day_offset == 0:
            series.append(
                {
                    "date": current_day.isoformat(),
                    "value_usd": round(current, 2),
                    "gain_usd": round(gain, 2),
                }
            )
            continue
        if compound:
            current *= 1.0 + daily_rate
        else:
            gain += principal * daily_rate
            current = principal + gain
        if compound:
            gain = current - principal
        series.append(
            {
                "date": current_day.isoformat(),
                "value_usd": round(current, 2),
                "gain_usd": round(gain, 2),
            }
        )

    ending_value = current
    gain = ending_value - principal
    return_pct = (gain / principal) * 100 if principal else 0.0
    annualized = return_pct if days == 365 else ((ending_value / principal) ** (365 / days) - 1) * 100
    return {
        "mode": "forward",
        "principal_usd": principal,
        "compound": compound,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "ending_value_usd": round(ending_value, 2),
        "gain_usd": round(gain, 2),
        "return_pct": round(return_pct, 4),
        "annualized_return_pct": round(annualized, 4),
        "series": series,
    }


def _build_historical_replay(
    *,
    history: CachedDataset,
    opportunity: dict[str, Any],
    principal: float,
    start_date: str,
    end_date: str,
    compound: bool,
) -> dict[str, Any]:
    date_idx = history.columns.index("date")
    rate_column = "net_apr_7d" if str(opportunity.get("type", "")).lower() == "lp" else "yield_apy"
    rate_idx = history.columns.index(rate_column)
    rows = sorted(history.rows, key=lambda row: str(row[date_idx]))
    if not rows:
        raise ValueError("No history rows are available for historical replay")

    if start_date:
        start = _date_from_string(start_date)
        rows = [row for row in rows if _date_from_string(str(row[date_idx])) >= start]
    if end_date:
        end = _date_from_string(end_date)
        rows = [row for row in rows if _date_from_string(str(row[date_idx])) <= end]
    if not rows:
        raise ValueError("No history rows remain after applying the date range")

    start = _date_from_string(str(rows[0][date_idx]))
    end = _date_from_string(str(rows[-1][date_idx]))
    current = principal
    gain = 0.0
    series: list[dict[str, Any]] = [
        {
            "date": start.isoformat(),
            "value_usd": round(current, 2),
            "gain_usd": 0.0,
        }
    ]

    for row in rows:
        annual_rate = _normalize_rate(_coerce_number(row[rate_idx]))
        if str(opportunity.get("type", "")).lower() == "lp":
            daily_rate = annual_rate / 365.0
        else:
            daily_rate = math.pow(max(0.0, 1.0 + annual_rate), 1.0 / 365.0) - 1.0
        if compound:
            current *= 1.0 + daily_rate
            gain = current - principal
        else:
            gain += principal * daily_rate
            current = principal + gain
        series.append(
            {
                "date": str(row[date_idx]),
                "value_usd": round(current, 2),
                "gain_usd": round(gain, 2),
            }
        )

    days = max((end - start).days, 1)
    ending_value = current
    gain = ending_value - principal
    return_pct = (gain / principal) * 100 if principal else 0.0
    annualized = ((ending_value / principal) ** (365 / days) - 1) * 100
    return {
        "mode": "historical_replay",
        "principal_usd": principal,
        "compound": compound,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "ending_value_usd": round(ending_value, 2),
        "gain_usd": round(gain, 2),
        "return_pct": round(return_pct, 4),
        "annualized_return_pct": round(annualized, 4),
        "series": series,
    }


def register_yield_opportunities_tools(mcp, ch: ClickHouseManager) -> None:
    mini_apps.register_app(
        YIELD_OPPORTUNITIES_APP_ID,
        title=DEFAULT_TITLE,
        resource_uri=YIELD_OPPORTUNITIES_URI,
    )

    @mcp.resource(
        YIELD_OPPORTUNITIES_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_yield_opportunities_app() -> str:
        return get_yield_opportunities_html()

    @mcp.tool(
        meta={
            "ui": {"resourceUri": YIELD_OPPORTUNITIES_URI},
            "ui/resourceUri": YIELD_OPPORTUNITIES_URI,
        }
    )
    def open_yield_opportunities(
        query: str = "",
        token: str = "",
        type: str = "",
        protocol: str = "",
        title: str = "",
    ) -> CallToolResult:
        """Open the Yield Opportunities mini app."""
        type_value = type.strip().lower()
        if type_value not in VALID_TYPES:
            return mini_apps.error_call_tool_result(
                "type must be '', 'lp', or 'lending'"
            )

        try:
            opportunities = _load_opportunities_dataset(ch)
        except MiniAppQueryError as exc:
            return mini_apps.error_call_tool_result(f"Yield opportunities query failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("open_yield_opportunities failed")
            return mini_apps.error_call_tool_result(str(exc))

        view_id = mini_apps.create_view(
            YIELD_OPPORTUNITIES_APP_ID,
            title or DEFAULT_TITLE,
        )
        mini_apps.replace_view_datasets(view_id, {"opportunities": opportunities})
        view_state = _seed_view_state(
            query=query,
            token=token,
            opportunity_type=type_value,
            protocol=protocol,
        )
        mini_apps.patch_view_state(view_id, view_state)
        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = _build_payload_from_record(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Yield Opportunities ready with {opportunities.stats.row_count:,} rows "
                f"(view_id={view_id[:8]})"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": YIELD_OPPORTUNITIES_URI},
            "ui/resourceUri": YIELD_OPPORTUNITIES_URI,
        }
    )
    def load_yield_opportunity(
        view_id: str,
        opportunity_key: str,
        compare_with: str = "",
    ) -> CallToolResult:
        """Load history for one opportunity, plus one optional comparator."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")

        try:
            ref = _parse_opportunity_key(opportunity_key)
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        selected = _find_opportunity(record.datasets.get("opportunities"), ref.key)
        if selected is None:
            return mini_apps.error_call_tool_result(
                f"Opportunity '{opportunity_key}' is not present in the current ranking dataset."
            )

        compare_key = compare_with.strip().lower()
        compare_selected: dict[str, Any] | None = None
        if compare_key:
            try:
                _parse_opportunity_key(compare_key)
            except ValueError as exc:
                return mini_apps.error_call_tool_result(str(exc))
            if compare_key == ref.key:
                return mini_apps.error_call_tool_result("compare_with must be different from opportunity_key")
            compare_selected = _find_opportunity(record.datasets.get("opportunities"), compare_key)
            if compare_selected is None:
                return mini_apps.error_call_tool_result(
                    f"Comparison opportunity '{compare_with}' is not present in the current ranking dataset."
                )

        try:
            selected_history = _load_history_dataset(ch, ref.key)
            compare_history = (
                _load_history_dataset(ch, compare_key) if compare_key else _empty_history_dataset("compare history")
            )
        except MiniAppQueryError as exc:
            return mini_apps.error_call_tool_result(f"Opportunity history query failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("load_yield_opportunity failed")
            return mini_apps.error_call_tool_result(str(exc))

        new_datasets = dict(record.datasets)
        new_datasets["selected_history"] = selected_history
        if compare_key:
            new_datasets["compare_history"] = compare_history
        else:
            new_datasets.pop("compare_history", None)
        mini_apps.replace_view_datasets(view_id, new_datasets)

        loaded_keys = sorted(
            {
                *(
                    str(key).lower()
                    for key in record.view_state.get("loaded_detail_keys", [])
                    if key
                ),
                ref.key,
                *( [compare_key] if compare_key else [] ),
            }
        )
        mini_apps.patch_view_state(
            view_id,
            {
                "selected_opportunity_key": ref.key,
                "compare_with": compare_key,
                "loaded_detail_keys": loaded_keys,
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        payload = _build_payload_from_record(updated)
        compare_label = (
            f" vs {compare_selected.get('name') or compare_selected.get('token')}"
            if compare_selected
            else ""
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Loaded yield opportunity "
                f"{selected.get('name') or selected.get('token')}{compare_label} "
                f"into view {view_id[:8]}"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": YIELD_OPPORTUNITIES_URI},
            "ui/resourceUri": YIELD_OPPORTUNITIES_URI,
        }
    )
    def update_yield_opportunities_focus(
        view_id: str,
        sort: str = "",
        token: str = "",
        type: str = "",
        protocol: str = "",
    ) -> CallToolResult:
        """Patch client-side ranking focus in an open yield view."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")

        patch: dict[str, Any] = {"filters": {}}
        if sort:
            if sort not in VALID_SORTS:
                return mini_apps.error_call_tool_result(
                    f"sort must be one of {sorted(VALID_SORTS)}"
                )
            patch["sort"] = sort
        type_value = type.strip().lower()
        if type and type_value not in VALID_TYPES:
            return mini_apps.error_call_tool_result("type must be '', 'lp', or 'lending'")
        if token:
            patch["filters"]["token"] = token
        if type:
            patch["filters"]["type"] = type_value
        if protocol:
            patch["filters"]["protocol"] = protocol
        if not patch["filters"]:
            patch.pop("filters")
        mini_apps.patch_view_state(view_id, patch)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=YIELD_OPPORTUNITIES_APP_ID,
            title=record.title,
            patch=patch,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text="Yield Opportunities focus updated.",
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": YIELD_OPPORTUNITIES_URI},
            "ui/resourceUri": YIELD_OPPORTUNITIES_URI,
        }
    )
    def run_yield_simulation(
        view_id: str,
        opportunity_key: str,
        mode: str,
        principal: float,
        start_date: str = "",
        end_date: str = "",
        compound: bool = True,
    ) -> CallToolResult:
        """Run a simple yield projection or historical replay."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")
        if mode not in VALID_SIMULATION_MODES:
            return mini_apps.error_call_tool_result(
                f"mode must be one of {sorted(VALID_SIMULATION_MODES)}"
            )
        if principal <= 0:
            return mini_apps.error_call_tool_result("principal must be greater than 0")

        try:
            ref = _parse_opportunity_key(opportunity_key)
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        opportunity = _find_opportunity(record.datasets.get("opportunities"), ref.key)
        if opportunity is None:
            return mini_apps.error_call_tool_result(
                f"Opportunity '{opportunity_key}' is not present in the current ranking dataset."
            )

        selected_history = record.datasets.get("selected_history")
        if (
            selected_history is None
            or "opportunity_key" not in selected_history.columns
            or not selected_history.rows
            or str(selected_history.rows[0][selected_history.columns.index("opportunity_key")]).lower() != ref.key
        ):
            try:
                selected_history = _load_history_dataset(ch, ref.key)
            except MiniAppQueryError as exc:
                return mini_apps.error_call_tool_result(f"Opportunity history query failed: {exc}")
            mini_apps.attach_dataset(view_id, "selected_history", selected_history)

        try:
            if mode == "forward":
                simulation = _build_forward_simulation(
                    opportunity=opportunity,
                    principal=float(principal),
                    start_date=start_date,
                    end_date=end_date,
                    compound=compound,
                )
            else:
                assert selected_history is not None
                simulation = _build_historical_replay(
                    history=selected_history,
                    opportunity=opportunity,
                    principal=float(principal),
                    start_date=start_date,
                    end_date=end_date,
                    compound=compound,
                )
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        patch = {
            "selected_opportunity_key": ref.key,
            "active_tab": "Simulation",
            "simulation": simulation,
        }
        mini_apps.patch_view_state(view_id, patch)
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=YIELD_OPPORTUNITIES_APP_ID,
            title=record.title,
            patch=patch,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Yield simulation complete for {opportunity.get('name') or opportunity.get('token')} "
                f"({mode})."
            ),
        )


__all__ = [
    "YIELD_OPPORTUNITIES_APP_ID",
    "YIELD_OPPORTUNITIES_URI",
    "get_yield_opportunities_html",
    "register_yield_opportunities_tools",
]
