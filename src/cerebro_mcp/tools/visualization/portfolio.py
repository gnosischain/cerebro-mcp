"""Safe-aware portfolio mini app."""

from __future__ import annotations

import importlib.resources
import logging
import re
from typing import Any

from mcp.types import CallToolResult

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.runtime.mini_app_cache import CachedDataset
from cerebro_mcp.models.mini_app import DatasetStats, MiniAppPayload, SummaryCard
from cerebro_mcp.tools.visualization import mini_apps, web_apps
from cerebro_mcp.tools.visualization.mini_apps import MiniAppQueryError

logger = logging.getLogger(__name__)


PORTFOLIO_APP_ID = "portfolio"
PORTFOLIO_URI = "ui://cerebro/portfolio"

DEFAULT_TITLE = "Portfolio"
VALID_SECTIONS = {"overview", "relationships", "yields", "gpay", "circles"}
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
ERC20_TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SAFE_ADDED_OWNER_TOPIC = "9465fa0c962cc76958e6373a993326400c1c94f8be2fe3a952adfa7f60b2ea26"
SAFE_REMOVED_OWNER_TOPIC = "f8d49fc529812e9a7c5c50e69c20f0dccc0db8fa95c98bc58cc9a4f1c1299eaf"
SAFE_CHANGED_THRESHOLD_TOPIC = "610f7ff2b304ae8903c3de74c60c6ab1f7d6226b3f52c5161905bb5ad4039c93"

_BUNDLED_PORTFOLIO_HTML: str | None = None


def get_portfolio_html() -> str:
    """Load the Vite-built single-file React app from the static package."""
    global _BUNDLED_PORTFOLIO_HTML
    if _BUNDLED_PORTFOLIO_HTML is None:
        try:
            _BUNDLED_PORTFOLIO_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/portfolio.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_PORTFOLIO_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>portfolio.html not built</div>"
                "</body></html>"
            )
    return _BUNDLED_PORTFOLIO_HTML


_YIELDS_KPIS_SQL = """
SELECT *
FROM api_execution_yields_user_kpis
WHERE lower(wallet_address) = {address:String}
LIMIT 1
"""

_GPAY_LIFETIME_SQL = """
SELECT *
FROM api_execution_gpay_user_lifetime_metrics
WHERE lower(wallet_address) = {address:String}
LIMIT 1
"""

_SAFE_INFO_SQL = """
SELECT
  safe_address,
  creation_version,
  block_date,
  block_timestamp
FROM int_execution_safes
WHERE lower(safe_address) = {address:String}
LIMIT 1
"""

_SAFE_CURRENT_OWNERS_SQL = """
SELECT
  safe_address,
  owner,
  became_owner_at,
  current_threshold
FROM int_execution_safes_current_owners
WHERE lower(safe_address) = {address:String}
ORDER BY became_owner_at ASC, owner ASC
LIMIT 200
"""

_OWNER_SAFE_RELATIONS_SQL = """
SELECT
  safe_address,
  owner,
  became_owner_at,
  current_threshold
FROM int_execution_safes_current_owners
WHERE lower(owner) = {address:String}
ORDER BY became_owner_at ASC, safe_address ASC
LIMIT 200
"""

_GPAY_WALLET_SQL = """
SELECT
  address,
  activation_date,
  creation_time
FROM int_execution_gpay_wallets
WHERE lower(address) = {address:String}
LIMIT 1
"""

_CIRCLES_CURRENT_SQL = """
SELECT
  c.avatar,
  c.avatar_type,
  c.name,
  c.block_timestamp,
  m.metadata_name,
  m.metadata_preview_image_url
FROM api_execution_circles_v2_avatars_current c
LEFT JOIN api_execution_circles_v2_avatar_metadata m
  ON m.avatar = c.avatar
WHERE lower(c.avatar) = {address:String}
LIMIT 1
"""

_CIRCLES_TRUSTS_SUMMARY_SQL = """
SELECT *
FROM api_execution_circles_v2_avatar_trusts_summary
WHERE lower(avatar) = {avatar:String}
LIMIT 1
"""

_CIRCLES_BALANCE_SUMMARY_SQL = """
SELECT
  count() AS holdings_count,
  sum(balance_demurraged) AS balance_demurraged
FROM api_execution_circles_v2_avatar_balances_latest
WHERE lower(avatar) = {avatar:String}
"""

_CIRCLES_TOKENS_HELD_SQL = """
SELECT
  avatar,
  tokens_held_count
FROM api_execution_circles_v2_avatar_tokens_held_count
WHERE lower(avatar) = {avatar:String}
LIMIT 1
"""

_GPAY_BALANCES_LATEST_SQL = """
WITH latest AS (
  SELECT max(date) AS max_date
  FROM api_execution_gpay_user_balances_daily
  WHERE lower(wallet_address) = {address:String}
)
SELECT
  wallet_address,
  date,
  token,
  label,
  value_native,
  value_usd
FROM api_execution_gpay_user_balances_daily
WHERE lower(wallet_address) = {address:String}
  AND date = (SELECT max_date FROM latest)
ORDER BY value_usd DESC, token ASC
LIMIT 200
"""

_YIELDS_LP_POSITIONS_SQL = """
SELECT
  provider,
  pool_address,
  protocol,
  tick_lower,
  tick_upper,
  capital_in_usd,
  capital_out_usd,
  fees_collected_usd,
  is_active,
  is_in_range,
  pool_current_tick,
  entry_date,
  last_action_date
FROM api_execution_yields_user_lp_positions
WHERE lower(provider) = {address:String}
ORDER BY last_action_date DESC, protocol ASC
LIMIT 2000
"""

_YIELDS_LENDING_POSITIONS_SQL = """
SELECT *
FROM api_execution_yields_user_lending_positions
WHERE lower(user_address) = {address:String}
ORDER BY balance_usd DESC, protocol ASC
LIMIT 2000
"""

_YIELDS_FEE_COLLECTIONS_SQL = """
SELECT *
FROM api_execution_yields_user_fee_collections_daily
WHERE lower(provider) = {address:String}
ORDER BY date DESC, fees_usd DESC
LIMIT 2000
"""

_YIELDS_LENDING_BALANCES_SQL = """
SELECT *
FROM api_execution_yields_user_lending_balances_daily
WHERE lower(user_address) = {address:String}
ORDER BY date DESC, balance_usd DESC
LIMIT 2000
"""

_YIELDS_ACTIVITY_SQL = """
SELECT *
FROM api_execution_yields_user_activity
WHERE lower(wallet_address) = {address:String}
ORDER BY block_timestamp DESC
LIMIT 2000
"""

_GPAY_BALANCES_DAILY_SQL = """
SELECT *
FROM api_execution_gpay_user_balances_daily
WHERE lower(wallet_address) = {address:String}
ORDER BY date DESC, value_usd DESC
LIMIT 2000
"""

_GPAY_PAYMENTS_SQL = """
SELECT *
FROM api_execution_gpay_user_payments_daily
WHERE lower(wallet_address) = {address:String}
ORDER BY date DESC, value DESC
LIMIT 2000
"""

_GPAY_CASHBACK_SQL = """
SELECT *
FROM api_execution_gpay_user_cashback_daily
WHERE lower(wallet_address) = {address:String}
ORDER BY date DESC, value DESC
LIMIT 2000
"""

_GPAY_ACTIVITY_SQL = """
SELECT *
FROM api_execution_gpay_user_activity
WHERE lower(wallet_address) = {address:String}
ORDER BY timestamp DESC
LIMIT 2000
"""

_GPAY_ACTIVITY_LIVE_OVERLAY_SQL = f"""
WITH tokens AS (
  SELECT
    lower(address) AS token_address,
    symbol,
    decimals,
    date_start,
    date_end
  FROM tokens_whitelist
),
deduped_logs AS (
  SELECT
    concat('0x', transaction_hash) AS transaction_hash,
    concat('0x', lower(address)) AS token_contract,
    topic1,
    topic2,
    data,
    block_timestamp
  FROM execution.logs
  WHERE topic0 = '{ERC20_TRANSFER_TOPIC}'
    AND block_timestamp >= toStartOfDay(now())
    AND block_timestamp <= now()
),
transfers AS (
  SELECT
    l.transaction_hash,
    l.block_timestamp,
    t.token_address,
    t.symbol,
    t.decimals,
    lower(concat('0x', substring(l.topic1, 25, 40))) AS sender,
    lower(concat('0x', substring(l.topic2, 25, 40))) AS receiver,
    reinterpretAsInt256(reverse(unhex(l.data))) AS value_raw
  FROM deduped_logs l
  INNER JOIN tokens t
    ON lower(l.token_contract) = t.token_address
   AND l.block_timestamp >= t.date_start
   AND (t.date_end IS NULL OR l.block_timestamp < t.date_end)
  WHERE lower(concat('0x', substring(l.topic1, 25, 40))) = {{address:String}}
     OR lower(concat('0x', substring(l.topic2, 25, 40))) = {{address:String}}
)
SELECT
  transaction_hash,
  {{address:String}} AS wallet_address,
  block_timestamp AS timestamp,
  toDate(block_timestamp) AS date,
  CASE
    WHEN sender = {{address:String}} AND receiver = '0x4822521e6135cd2599199c83ea35179229a172ee'
      THEN 'Payment'
    WHEN receiver = {{address:String}} AND sender = '0x4822521e6135cd2599199c83ea35179229a172ee'
      THEN 'Reversal'
    WHEN receiver = {{address:String}} AND sender = '0xcdf50be9061086e2ecfe6e4a1bf9164d43568eec'
      THEN 'Cashback'
    WHEN receiver = {{address:String}} AND sender = '0x0000000000000000000000000000000000000000'
      THEN 'Fiat Top Up'
    WHEN sender = {{address:String}} AND receiver = '0x0000000000000000000000000000000000000000'
      THEN 'Fiat Off-ramp'
    WHEN receiver = {{address:String}}
      THEN 'Crypto Deposit'
    ELSE 'Crypto Withdrawal'
  END AS action,
  symbol,
  CASE
    WHEN sender = {{address:String}} THEN 'out'
    ELSE 'in'
  END AS direction,
  round(toFloat64(value_raw) / power(10, decimals), 6) AS amount,
  round((toFloat64(value_raw) / power(10, decimals)) * coalesce(p.price, 0), 2) AS amount_usd,
  CASE
    WHEN sender = {{address:String}} THEN receiver
    ELSE sender
  END AS counterparty
FROM transfers
LEFT JOIN int_execution_token_prices_daily p
  ON p.date = toDate(block_timestamp)
 AND p.symbol = symbol
ORDER BY timestamp DESC
LIMIT 500
"""

_CIRCLES_METADATA_SQL = """
SELECT *
FROM api_execution_circles_v2_avatar_metadata
WHERE lower(avatar) = {avatar:String}
LIMIT 1
"""

_CIRCLES_BALANCES_SQL = """
SELECT *
FROM api_execution_circles_v2_avatar_balances_latest
WHERE lower(avatar) = {avatar:String}
ORDER BY balance_demurraged DESC, token_address ASC
LIMIT 1000
"""

_CIRCLES_DISTRIBUTION_SQL = """
SELECT *
FROM api_execution_circles_v2_avatar_token_distribution
WHERE lower(avatar) = {avatar:String}
ORDER BY balance_demurraged DESC, holder_category ASC
LIMIT 1000
"""

_CIRCLES_TRUST_RELATIONS_SQL = """
SELECT *
FROM api_execution_circles_v2_trust_relations_current
WHERE lower(truster) = {avatar:String}
   OR lower(trustee) = {avatar:String}
ORDER BY valid_from DESC
LIMIT 2000
"""

_CIRCLES_MINT_ACTIVITY_SQL = """
SELECT *
FROM api_execution_circles_v2_avatar_mint_activity_daily
WHERE lower(avatar) = {avatar:String}
ORDER BY date DESC
LIMIT 2000
"""

_SAFE_LIVE_OWNER_EVENTS_SQL = f"""
SELECT
  block_timestamp,
  CASE
    WHEN topic0 = '{SAFE_ADDED_OWNER_TOPIC}' THEN 'added_owner'
    WHEN topic0 = '{SAFE_REMOVED_OWNER_TOPIC}' THEN 'removed_owner'
    ELSE 'changed_threshold'
  END AS event_kind,
  lower(concat('0x', substring(data, 25, 40))) AS owner,
  toUInt32(reinterpretAsUInt256(reverse(unhex(data)))) AS threshold
FROM execution.logs
WHERE lower(address) = replaceAll({{address:String}}, '0x', '')
  AND topic0 IN (
    '{SAFE_ADDED_OWNER_TOPIC}',
    '{SAFE_REMOVED_OWNER_TOPIC}',
    '{SAFE_CHANGED_THRESHOLD_TOPIC}'
  )
  AND block_timestamp >= toStartOfDay(now())
  AND block_timestamp <= now()
ORDER BY block_timestamp ASC
LIMIT 200
"""


SECTION_DATASETS: dict[str, list[tuple[str, str, str]]] = {
    "yields": [
        ("yields_kpis", _YIELDS_KPIS_SQL, "Yield KPIs"),
        ("yields_lp_positions", _YIELDS_LP_POSITIONS_SQL, "LP positions"),
        ("yields_lending_positions", _YIELDS_LENDING_POSITIONS_SQL, "Lending positions"),
        ("yields_fee_collections", _YIELDS_FEE_COLLECTIONS_SQL, "Fee collections"),
        ("yields_lending_balances", _YIELDS_LENDING_BALANCES_SQL, "Lending balances"),
        ("yields_activity", _YIELDS_ACTIVITY_SQL, "Yield activity"),
    ],
    "gpay": [
        ("gpay_lifetime", _GPAY_LIFETIME_SQL, "Gnosis Pay lifetime"),
        ("gpay_balances_latest", _GPAY_BALANCES_LATEST_SQL, "Latest balances"),
        ("gpay_balances_daily", _GPAY_BALANCES_DAILY_SQL, "Balance history"),
        ("gpay_payments", _GPAY_PAYMENTS_SQL, "Payments"),
        ("gpay_cashback", _GPAY_CASHBACK_SQL, "Cashback"),
        ("gpay_activity", _GPAY_ACTIVITY_SQL, "Gnosis Pay activity"),
    ],
    "circles": [
        ("circles_metadata", _CIRCLES_METADATA_SQL, "Circles identity"),
        ("circles_balances", _CIRCLES_BALANCES_SQL, "Circles balances"),
        ("circles_distribution", _CIRCLES_DISTRIBUTION_SQL, "Token distribution"),
        ("circles_trusts_summary", _CIRCLES_TRUSTS_SUMMARY_SQL, "Trust summary"),
        ("circles_trust_relations", _CIRCLES_TRUST_RELATIONS_SQL, "Trust relations"),
        ("circles_mint_activity", _CIRCLES_MINT_ACTIVITY_SQL, "Mint activity"),
    ],
}


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_address(address: str) -> str:
    value = address.strip().lower()
    if not ADDRESS_RE.match(value):
        raise ValueError("address must be a valid 0x-prefixed 20-byte hex string")
    return value


def _short_address(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}"


def _label_for_address(address: str, presence: dict[str, Any] | None = None) -> str:
    if presence:
        display_name = str(presence.get("circles_display_name") or "").strip()
        if display_name:
            return display_name
    return _short_address(address)


def _rows_dataset(
    *,
    columns: list[str],
    rows: list[list[Any]],
    sql: str,
    parameters: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> CachedDataset:
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
        column_types=["str" for _ in columns],
        rows=rows,
        stats=stats,
        sql=sql,
        database="dbt",
        parameters=parameters,
    )


def _empty_dataset(label: str, sql: str, parameters: dict[str, Any]) -> CachedDataset:
    return _rows_dataset(
        columns=[],
        rows=[],
        sql=sql,
        parameters=parameters,
        warnings=[f"{label} unavailable"],
    )


def _mapping(result: mini_apps.StructuredResult) -> dict[str, Any] | None:
    if not result.rows:
        return None
    row = result.rows[0]
    return {
        result.columns[idx]: row[idx] if idx < len(row) else None
        for idx in range(len(result.columns))
    }


def _mapping_list(result: mini_apps.StructuredResult) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in result.rows:
        out.append(
            {
                result.columns[idx]: row[idx] if idx < len(row) else None
                for idx in range(len(result.columns))
            }
        )
    return out


def _run_one_row_safe(
    ch: ClickHouseManager,
    *,
    sql: str,
    parameters: dict[str, Any],
    label: str,
    warnings: list[str],
    database: str = "dbt",
) -> dict[str, Any] | None:
    try:
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database=database,
            parameters=parameters,
            requested_max_rows=1,
        )
    except Exception as exc:
        logger.warning("portfolio %s query failed: %s", label, exc)
        warnings.append(f"{label} unavailable: {exc}")
        return None
    return _mapping(result)


def _run_rows_safe(
    ch: ClickHouseManager,
    *,
    sql: str,
    parameters: dict[str, Any],
    label: str,
    warnings: list[str],
    database: str = "dbt",
    limit: int = 500,
) -> list[dict[str, Any]]:
    try:
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database=database,
            parameters=parameters,
            requested_max_rows=limit,
        )
    except Exception as exc:
        logger.warning("portfolio %s query failed: %s", label, exc)
        warnings.append(f"{label} unavailable: {exc}")
        return []
    return _mapping_list(result)


def _load_exact_dataset_safe(
    ch: ClickHouseManager,
    *,
    sql: str,
    parameters: dict[str, Any],
    label: str,
    warnings: list[str],
    database: str = "dbt",
    limit: int = 500,
) -> CachedDataset:
    try:
        result = mini_apps.run_structured_query(
            ch,
            sql,
            database=database,
            parameters=parameters,
            requested_max_rows=limit,
        )
        stats = DatasetStats(
            row_count=result.row_count,
            rows_returned=len(result.rows),
            mode="exact_bounded",
            sample_source_rows=result.row_count,
            elapsed_seconds=result.elapsed_seconds,
            warnings=list(result.warnings),
        )
        return CachedDataset(
            columns=list(result.columns),
            column_types=list(result.column_types),
            rows=list(result.rows),
            stats=stats,
            sql=result.sql,
            database=result.database,
            parameters=parameters,
        )
    except Exception as exc:
        logger.warning("portfolio %s dataset load failed: %s", label, exc)
        warnings.append(f"{label} unavailable: {exc}")
        return _empty_dataset(label, sql, parameters)


def _load_bounded_dataset_safe(
    ch: ClickHouseManager,
    *,
    sql: str,
    parameters: dict[str, Any],
    label: str,
    warnings: list[str],
    database: str = "dbt",
) -> CachedDataset:
    try:
        return mini_apps.load_bounded_dataset(
            ch,
            sql,
            database=database,
            parameters=parameters,
        )
    except MiniAppQueryError as exc:
        logger.warning("portfolio %s query failed: %s", label, exc)
        warnings.append(f"{label} unavailable: {exc}")
        return _empty_dataset(label, sql, parameters)
    except Exception as exc:
        logger.warning("portfolio %s dataset load failed: %s", label, exc)
        warnings.append(f"{label} unavailable: {exc}")
        return _empty_dataset(label, sql, parameters)


def _coerce_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _date_candidates(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value)
        if " " in text:
            text = text.split(" ", 1)[0]
        out.append(text)
    return out


def _resolve_presence(
    ch: ClickHouseManager,
    address: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[str] = []
    params = {"address": address}

    yields_row = _run_one_row_safe(
        ch, sql=_YIELDS_KPIS_SQL, parameters=params, label="Yield KPIs", warnings=warnings
    )
    gpay_row = _run_one_row_safe(
        ch, sql=_GPAY_LIFETIME_SQL, parameters=params, label="Gnosis Pay lifetime", warnings=warnings
    )
    safe_row = _run_one_row_safe(
        ch, sql=_SAFE_INFO_SQL, parameters=params, label="Safe info", warnings=warnings
    )
    safe_owner_rows = _run_rows_safe(
        ch, sql=_SAFE_CURRENT_OWNERS_SQL, parameters=params, label="Safe owners", warnings=warnings, limit=200
    )
    owned_safe_rows = _run_rows_safe(
        ch, sql=_OWNER_SAFE_RELATIONS_SQL, parameters=params, label="Owned Safes", warnings=warnings, limit=200
    )
    gpay_wallet_row = _run_one_row_safe(
        ch, sql=_GPAY_WALLET_SQL, parameters=params, label="Gnosis Pay wallet", warnings=warnings
    )
    circles_row = _run_one_row_safe(
        ch, sql=_CIRCLES_CURRENT_SQL, parameters=params, label="Circles avatar", warnings=warnings
    )
    latest_balance_rows = _run_rows_safe(
        ch,
        sql=_GPAY_BALANCES_LATEST_SQL,
        parameters=params,
        label="Latest Gnosis Pay balances",
        warnings=warnings,
        limit=200,
    )

    effective_avatar = str(circles_row.get("avatar")) if circles_row else ""
    circles_summary: dict[str, Any] = {}
    if effective_avatar:
        avatar_params = {"avatar": effective_avatar.lower()}
        circles_summary_row = _run_one_row_safe(
            ch,
            sql=_CIRCLES_TRUSTS_SUMMARY_SQL,
            parameters=avatar_params,
            label="Circles trust summary",
            warnings=warnings,
        )
        balances_summary_row = _run_one_row_safe(
            ch,
            sql=_CIRCLES_BALANCE_SUMMARY_SQL,
            parameters=avatar_params,
            label="Circles balance summary",
            warnings=warnings,
        )
        tokens_held_row = _run_one_row_safe(
            ch,
            sql=_CIRCLES_TOKENS_HELD_SQL,
            parameters=avatar_params,
            label="Circles tokens held count",
            warnings=warnings,
        )
        circles_summary = {
            "avatar": effective_avatar,
            "trusts_given_count": circles_summary_row.get("trusts_given_count") if circles_summary_row else 0,
            "trusts_received_count": circles_summary_row.get("trusts_received_count") if circles_summary_row else 0,
            "holdings_count": balances_summary_row.get("holdings_count") if balances_summary_row else 0,
            "balance_demurraged": balances_summary_row.get("balance_demurraged") if balances_summary_row else 0,
            "tokens_held_count": tokens_held_row.get("tokens_held_count") if tokens_held_row else 0,
        }

    balance_total = sum(_coerce_number(row.get("value_usd")) for row in latest_balance_rows)
    safe_threshold = (
        safe_owner_rows[0].get("current_threshold")
        if safe_owner_rows
        else None
    )
    date_candidates = _date_candidates(
        [
            yields_row.get("first_yield_date") if yields_row else None,
            gpay_row.get("first_activity_date") if gpay_row else None,
            gpay_row.get("last_activity_date") if gpay_row else None,
            safe_row.get("block_date") if safe_row else None,
            circles_row.get("block_timestamp") if circles_row else None,
        ]
    )
    first_activity = min(date_candidates) if date_candidates else ""
    last_activity = max(date_candidates) if date_candidates else ""

    presence = {
        "address": address,
        "has_yields": yields_row is not None,
        "has_gpay": gpay_row is not None,
        "is_circles_avatar": circles_row is not None,
        "circles_avatar": effective_avatar,
        "circles_display_name": (
            str(circles_row.get("metadata_name") or circles_row.get("name") or "")
            if circles_row
            else ""
        ),
        "is_safe": safe_row is not None,
        "owns_safes": bool(owned_safe_rows),
        "safe_creation_version": safe_row.get("creation_version") if safe_row else "",
        "safe_current_threshold": safe_threshold,
        "safe_owner_count": len(safe_owner_rows),
        "is_gpay_safe": gpay_wallet_row is not None,
        "first_activity_date": first_activity,
        "last_activity_date": last_activity,
    }
    overview = {
        "yields_kpis": yields_row or {},
        "gpay_lifetime": gpay_row or {},
        "gpay_latest_balance_usd": round(balance_total, 2),
        "circles_summary": circles_summary,
        "safe": {
            "creation_version": presence["safe_creation_version"],
            "current_threshold": presence["safe_current_threshold"],
            "owner_count": presence["safe_owner_count"],
            "is_gpay_safe": presence["is_gpay_safe"],
        },
    }
    return presence, overview, warnings, safe_owner_rows, owned_safe_rows


def _build_relationship_dataset(
    ch: ClickHouseManager,
    *,
    address: str,
    safe_owner_rows: list[dict[str, Any]],
    owned_safe_rows: list[dict[str, Any]],
    presence: dict[str, Any],
    warnings: list[str],
) -> CachedDataset:
    current_safe_owner_count = len(safe_owner_rows)
    snapshot_rows: list[dict[str, Any]] = []

    overlay_warnings: list[str] = []
    if presence.get("is_safe"):
        live_events = _run_rows_safe(
            ch,
            sql=_SAFE_LIVE_OWNER_EVENTS_SQL,
            parameters={"address": address},
            label="Live Safe owner overlay",
            warnings=overlay_warnings,
            database="dbt",
            limit=200,
        )
        if live_events:
            owner_map = {str(row.get("owner")).lower(): dict(row) for row in safe_owner_rows}
            threshold = presence.get("safe_current_threshold")
            for event in live_events:
                event_kind = str(event.get("event_kind") or "")
                owner = str(event.get("owner") or "").lower()
                if event_kind == "added_owner" and owner:
                    owner_map[owner] = {
                        "safe_address": address,
                        "owner": owner,
                        "became_owner_at": event.get("block_timestamp"),
                        "current_threshold": threshold,
                    }
                elif event_kind == "removed_owner" and owner:
                    owner_map.pop(owner, None)
                elif event_kind == "changed_threshold":
                    threshold = event.get("threshold")
            safe_owner_rows = list(owner_map.values())
            current_safe_owner_count = len(safe_owner_rows)
            for row in safe_owner_rows:
                row["current_threshold"] = threshold
            warnings.append("Showing best-effort same-day Safe ownership updates.")
        warnings.extend(overlay_warnings)

    related_addresses = {
        str(row.get("owner")).lower()
        for row in safe_owner_rows
        if row.get("owner")
    } | {
        str(row.get("safe_address")).lower()
        for row in owned_safe_rows
        if row.get("safe_address")
    }

    related_presence: dict[str, dict[str, Any]] = {}
    for related_address in sorted(related_addresses):
        related_presence[related_address] = _resolve_presence(ch, related_address)[0]

    for row in safe_owner_rows:
        related_address = str(row.get("owner")).lower()
        info = related_presence.get(related_address, {})
        snapshot_rows.append(
            {
                "relation_type": "safe_owner",
                "related_address": related_address,
                "label": _label_for_address(related_address, info),
                "became_related_at": row.get("became_owner_at"),
                "threshold": row.get("current_threshold"),
                "owner_count": current_safe_owner_count,
                "related_is_safe": info.get("is_safe", False),
                "related_is_gpay_safe": info.get("is_gpay_safe", False),
                "related_has_yields": info.get("has_yields", False),
                "related_has_gpay": info.get("has_gpay", False),
                "related_is_circles_avatar": info.get("is_circles_avatar", False),
                "related_circles_display_name": info.get("circles_display_name", ""),
            }
        )

    for row in owned_safe_rows:
        related_address = str(row.get("safe_address")).lower()
        info = related_presence.get(related_address, {})
        snapshot_rows.append(
            {
                "relation_type": "owner_safe",
                "related_address": related_address,
                "label": _label_for_address(related_address, info),
                "became_related_at": row.get("became_owner_at"),
                "threshold": row.get("current_threshold"),
                "owner_count": info.get("safe_owner_count", 0),
                "related_is_safe": info.get("is_safe", False),
                "related_is_gpay_safe": info.get("is_gpay_safe", False),
                "related_has_yields": info.get("has_yields", False),
                "related_has_gpay": info.get("has_gpay", False),
                "related_is_circles_avatar": info.get("is_circles_avatar", False),
                "related_circles_display_name": info.get("circles_display_name", ""),
            }
        )

    snapshot_rows.sort(
        key=lambda row: (
            str(row["relation_type"]),
            str(row["label"]).lower(),
            str(row["related_address"]),
        )
    )
    columns = [
        "relation_type",
        "related_address",
        "label",
        "became_related_at",
        "threshold",
        "owner_count",
        "related_is_safe",
        "related_is_gpay_safe",
        "related_has_yields",
        "related_has_gpay",
        "related_is_circles_avatar",
        "related_circles_display_name",
    ]
    rows = [
        [row.get(column) for column in columns]
        for row in snapshot_rows
    ]
    return _rows_dataset(
        columns=columns,
        rows=rows,
        sql="-- relationships composed in app code --",
        parameters={"address": address},
        warnings=_dedupe_strings(warnings),
    )


def _merge_gpay_activity_overlay(
    ch: ClickHouseManager,
    *,
    address: str,
    snapshot: CachedDataset,
    warnings: list[str],
) -> CachedDataset:
    live_rows = _run_rows_safe(
        ch,
        sql=_GPAY_ACTIVITY_LIVE_OVERLAY_SQL,
        parameters={"address": address},
        label="Live Gnosis Pay activity overlay",
        warnings=warnings,
        database="dbt",
        limit=500,
    )
    if not live_rows:
        return snapshot

    warnings.append("Showing best-effort same-day Gnosis Pay activity updates.")
    columns = snapshot.columns or list(live_rows[0].keys())
    merged: list[list[Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def add_row(row_values: list[Any]) -> None:
        key = tuple(row_values[:6])
        if key in seen:
            return
        seen.add(key)
        merged.append(row_values)

    for row in snapshot.rows:
        add_row(list(row))
    for item in live_rows:
        add_row([item.get(column) for column in columns])

    timestamp_idx = columns.index("timestamp") if "timestamp" in columns else -1
    if timestamp_idx >= 0:
        merged.sort(
            key=lambda row: str(row[timestamp_idx] or ""),
            reverse=True,
        )
    return CachedDataset(
        columns=columns,
        column_types=snapshot.column_types or ["str" for _ in columns],
        rows=merged,
        stats=DatasetStats(
            row_count=len(merged),
            rows_returned=len(merged),
            mode="exact_bounded" if len(merged) <= 2000 else snapshot.stats.mode,
            sample_source_rows=len(merged),
            elapsed_seconds=snapshot.stats.elapsed_seconds,
            warnings=_dedupe_strings(snapshot.stats.warnings + warnings),
        ),
        sql=snapshot.sql,
        database=snapshot.database,
        parameters=snapshot.parameters,
    )


def _build_summary_cards(record: mini_apps.ViewRecord) -> list[SummaryCard]:
    presence = record.view_state.get("presence") or {}
    overview = record.view_state.get("overview") or {}
    address = str(record.view_state.get("current_address") or "")
    domain_count = sum(
        1
        for flag in (
            presence.get("has_yields"),
            presence.get("has_gpay"),
            presence.get("is_circles_avatar"),
            presence.get("is_safe"),
        )
        if flag
    )
    cards = [
        SummaryCard(
            label="Address",
            value=_short_address(address) if address else "Pick an address",
            tone="neutral",
        ),
        SummaryCard(
            label="Domains",
            value=str(domain_count),
            tone="positive" if domain_count else "warning",
        ),
        SummaryCard(
            label="Relationships",
            value=str(record.datasets.get("relationships").stats.row_count if record.datasets.get("relationships") else 0),
            tone="neutral",
        ),
    ]
    if presence.get("has_yields"):
        yields_kpis = overview.get("yields_kpis") or {}
        cards.append(
            SummaryCard(
                label="Yield balance",
                value=f"${_coerce_number(yields_kpis.get('total_lending_balance_usd')):,.2f}",
                delta=f"{int(_coerce_number(yields_kpis.get('active_lending_positions')) + _coerce_number(yields_kpis.get('active_lp_positions')))} positions",
                tone="positive",
            )
        )
    if presence.get("has_gpay"):
        cards.append(
            SummaryCard(
                label="Gnosis Pay",
                value=f"${_coerce_number(overview.get('gpay_latest_balance_usd')):,.2f}",
                delta=f"{int(_coerce_number((overview.get('gpay_lifetime') or {}).get('total_payment_count')))} payments",
                tone="positive",
            )
        )
    return cards[:5]


def _dataset_titles() -> dict[str, str]:
    titles = {
        "relationships": "Relationships",
    }
    for section_rows in SECTION_DATASETS.values():
        for key, _sql, title in section_rows:
            titles[key] = title
    return titles


def _build_payload_from_record(
    record: mini_apps.ViewRecord,
    *,
    warnings: list[str] | None = None,
) -> MiniAppPayload:
    titles = _dataset_titles()
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
        + list(record.view_state.get("warnings") or [])
    )
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=record.view_id,
        app_id=PORTFOLIO_APP_ID,
        title=record.title,
        status="ready",
        summary_cards=_build_summary_cards(record),
        datasets=descriptors,
        view_state=record.view_state,
        provenance={"source": "dbt+runtime"},
        warnings=all_warnings,
    )


def _empty_portfolio_state(title: str) -> dict[str, Any]:
    return {
        "current_address": "",
        "active_section": "overview",
        "loaded_sections": {section: False for section in VALID_SECTIONS},
        "breadcrumbs": [],
        "circles_avatar_override": "",
        "effective_circles_avatar": "",
        "presence": {},
        "overview": {},
        "section_filters": {
            section: {"start_date": "", "token": "", "action": ""}
            for section in VALID_SECTIONS
        },
        "warnings": [],
        "title": title,
    }


def _prepare_breadcrumbs_for_load(
    *,
    current_address: str,
    breadcrumbs: list[dict[str, str]],
    current_label: str,
    target_address: str,
    mode: str,
) -> list[dict[str, str]]:
    target = target_address.lower()
    normalized = [
        {
            "address": str(item.get("address", "")).lower(),
            "label": str(item.get("label", "")),
        }
        for item in breadcrumbs
        if item.get("address")
    ]

    for index, crumb in enumerate(normalized):
        if crumb["address"] == target:
            return normalized[:index]

    if current_address.lower() == target:
        return normalized

    if mode == "navigate" and current_address:
        return normalized + [{"address": current_address.lower(), "label": current_label}]

    return []


def _load_portfolio_address_snapshot(
    ch: ClickHouseManager,
    *,
    address: str,
    circles_avatar_override: str,
) -> tuple[dict[str, CachedDataset], dict[str, Any], list[str]]:
    presence, overview, warnings, safe_owner_rows, owned_safe_rows = _resolve_presence(ch, address)

    effective_avatar = presence.get("circles_avatar") or ""
    override_value = circles_avatar_override.lower()
    if override_value:
        if not effective_avatar:
            override_presence, _override_overview, override_warnings, _, _ = _resolve_presence(ch, override_value)
            warnings.extend(override_warnings)
            if not override_presence.get("is_circles_avatar"):
                raise ValueError("circles_avatar_override must be a valid Circles avatar")
            effective_avatar = override_value
        else:
            override_value = ""

    relationships = _build_relationship_dataset(
        ch,
        address=address,
        safe_owner_rows=safe_owner_rows,
        owned_safe_rows=owned_safe_rows,
        presence=presence,
        warnings=warnings,
    )

    datasets = {"relationships": relationships}
    if not any(
        (
            presence.get("has_yields"),
            presence.get("has_gpay"),
            presence.get("is_circles_avatar"),
            presence.get("is_safe"),
            presence.get("owns_safes"),
        )
    ):
        warnings.append("No portfolio data matched this address in the current snapshot.")

    view_state = {
        "current_address": address,
        "active_section": "overview",
        "loaded_sections": {
            "overview": True,
            "relationships": True,
            "yields": False,
            "gpay": False,
            "circles": False,
        },
        "circles_avatar_override": override_value,
        "effective_circles_avatar": effective_avatar,
        "presence": presence,
        "overview": overview,
        "warnings": _dedupe_strings(warnings),
    }
    return datasets, view_state, warnings


def _load_section_datasets(
    ch: ClickHouseManager,
    *,
    section: str,
    address: str,
    effective_circles_avatar: str,
    warnings: list[str],
) -> dict[str, CachedDataset]:
    datasets: dict[str, CachedDataset] = {}
    section_key = section.lower()
    if section_key not in SECTION_DATASETS:
        return datasets

    for key, sql, label in SECTION_DATASETS[section_key]:
        parameter_key = "avatar" if key.startswith("circles_") else "address"
        parameter_value = effective_circles_avatar if parameter_key == "avatar" else address
        if parameter_key == "avatar" and not parameter_value:
            datasets[key] = _rows_dataset(
                columns=[],
                rows=[],
                sql="-- circles section skipped; no avatar --",
                parameters={"avatar": ""},
                warnings=["Circles section unavailable for this address."],
            )
            continue
        dataset = _load_bounded_dataset_safe(
            ch,
            sql=sql,
            parameters={parameter_key: parameter_value},
            label=label,
            warnings=warnings,
        )
        if key == "gpay_activity":
            dataset = _merge_gpay_activity_overlay(
                ch,
                address=address,
                snapshot=dataset,
                warnings=warnings,
            )
        datasets[key] = dataset
    return datasets


def register_portfolio_tools(mcp, ch: ClickHouseManager) -> None:
    mini_apps.register_app(
        PORTFOLIO_APP_ID,
        title=DEFAULT_TITLE,
        resource_uri=PORTFOLIO_URI,
    )

    @mcp.resource(
        PORTFOLIO_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_portfolio_app() -> str:
        return get_portfolio_html()

    @mcp.tool(
        meta={
            "ui": {"resourceUri": PORTFOLIO_URI},
            "ui/resourceUri": PORTFOLIO_URI,
        }
    )
    def open_portfolio(title: str = "") -> CallToolResult:
        """Open an empty Portfolio mini app."""
        view_id = mini_apps.create_view(PORTFOLIO_APP_ID, title or DEFAULT_TITLE)
        state = _empty_portfolio_state(title or DEFAULT_TITLE)
        mini_apps.patch_view_state(view_id, state)
        record = mini_apps.get_view(view_id)
        assert record is not None
        payload = _build_payload_from_record(record)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Portfolio ready. view_id={view_id[:8]}",
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": PORTFOLIO_URI},
            "ui/resourceUri": PORTFOLIO_URI,
        }
    )
    def load_portfolio_address(
        view_id: str,
        address: str,
        circles_avatar_override: str = "",
    ) -> CallToolResult:
        """Load one address into an existing Portfolio view."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")

        try:
            normalized_address = _normalize_address(address)
            normalized_override = (
                _normalize_address(circles_avatar_override)
                if circles_avatar_override.strip()
                else ""
            )
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        current_address = str(record.view_state.get("current_address") or "")
        current_presence = record.view_state.get("presence") or {}
        breadcrumbs = _prepare_breadcrumbs_for_load(
            current_address=current_address,
            breadcrumbs=list(record.view_state.get("breadcrumbs") or []),
            current_label=_label_for_address(current_address, current_presence) if current_address else "",
            target_address=normalized_address,
            mode="search",
        )

        try:
            datasets, state_patch, warnings = _load_portfolio_address_snapshot(
                ch,
                address=normalized_address,
                circles_avatar_override=normalized_override,
            )
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        mini_apps.replace_view_datasets(view_id, datasets)
        mini_apps.patch_view_state(
            view_id,
            {
                **state_patch,
                "breadcrumbs": breadcrumbs,
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        payload = _build_payload_from_record(updated, warnings=warnings)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Loaded portfolio for {normalized_address} into view {view_id[:8]}",
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": PORTFOLIO_URI},
            "ui/resourceUri": PORTFOLIO_URI,
        }
    )
    def navigate_portfolio_relation(
        view_id: str,
        related_address: str,
    ) -> CallToolResult:
        """Drill one hop into a related Safe or owner address."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")
        try:
            normalized_related = _normalize_address(related_address)
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        relationships = record.datasets.get("relationships")
        if relationships is None:
            return mini_apps.error_call_tool_result("Relationships have not been loaded for this view.")
        try:
            address_idx = relationships.columns.index("related_address")
        except ValueError:
            return mini_apps.error_call_tool_result("Relationship dataset is malformed.")
        if normalized_related not in {
            str(row[address_idx]).lower()
            for row in relationships.rows
            if address_idx < len(row)
        }:
            return mini_apps.error_call_tool_result(
                f"Address {normalized_related} is not a visible relation in the current view."
            )

        current_address = str(record.view_state.get("current_address") or "")
        current_presence = record.view_state.get("presence") or {}
        breadcrumbs = _prepare_breadcrumbs_for_load(
            current_address=current_address,
            breadcrumbs=list(record.view_state.get("breadcrumbs") or []),
            current_label=_label_for_address(current_address, current_presence) if current_address else "",
            target_address=normalized_related,
            mode="navigate",
        )

        try:
            datasets, state_patch, warnings = _load_portfolio_address_snapshot(
                ch,
                address=normalized_related,
                circles_avatar_override="",
            )
        except ValueError as exc:
            return mini_apps.error_call_tool_result(str(exc))

        mini_apps.replace_view_datasets(view_id, datasets)
        mini_apps.patch_view_state(
            view_id,
            {
                **state_patch,
                "breadcrumbs": breadcrumbs,
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        payload = _build_payload_from_record(updated, warnings=warnings)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Navigated to related portfolio {normalized_related}.",
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": PORTFOLIO_URI},
            "ui/resourceUri": PORTFOLIO_URI,
        }
    )
    def load_portfolio_section(
        view_id: str,
        section: str,
    ) -> CallToolResult:
        """Lazy-load one portfolio section into an existing view."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")
        section_key = section.strip().lower()
        if section_key not in VALID_SECTIONS:
            return mini_apps.error_call_tool_result(
                f"section must be one of {sorted(VALID_SECTIONS)}"
            )

        current_address = str(record.view_state.get("current_address") or "")
        if not current_address:
            return mini_apps.error_call_tool_result("Load an address before opening portfolio sections.")

        loaded_sections = dict(record.view_state.get("loaded_sections") or {})
        warnings: list[str] = []
        if section_key not in {"overview", "relationships"} and not loaded_sections.get(section_key):
            datasets = _load_section_datasets(
                ch,
                section=section_key,
                address=current_address,
                effective_circles_avatar=str(record.view_state.get("effective_circles_avatar") or ""),
                warnings=warnings,
            )
            for key, dataset in datasets.items():
                mini_apps.attach_dataset(view_id, key, dataset)
            loaded_sections[section_key] = True

        mini_apps.patch_view_state(
            view_id,
            {
                "active_section": section_key,
                "loaded_sections": loaded_sections,
                "warnings": _dedupe_strings(
                    list(record.view_state.get("warnings") or []) + warnings
                ),
            },
        )
        updated = mini_apps.get_view(view_id)
        assert updated is not None
        payload = _build_payload_from_record(updated, warnings=warnings)
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Portfolio section '{section_key}' loaded.",
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": PORTFOLIO_URI},
            "ui/resourceUri": PORTFOLIO_URI,
        }
    )
    def update_portfolio_focus(
        view_id: str,
        section: str = "",
        start_date: str = "",
        token: str = "",
        action: str = "",
    ) -> CallToolResult:
        """Patch client-side section focus and filters."""
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(f"Unknown or expired view_id: {view_id}")

        section_key = section.strip().lower() or str(record.view_state.get("active_section") or "overview")
        if section_key not in VALID_SECTIONS:
            return mini_apps.error_call_tool_result(
                f"section must be one of {sorted(VALID_SECTIONS)}"
            )

        current_filters = {
            name: dict(values)
            for name, values in (record.view_state.get("section_filters") or {}).items()
            if isinstance(values, dict)
        }
        next_filters = {
            "start_date": start_date,
            "token": token,
            "action": action,
        }
        current_filters[section_key] = next_filters
        patch = {
            "active_section": section_key,
            "section_filters": current_filters,
        }
        mini_apps.patch_view_state(view_id, patch)
        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=PORTFOLIO_APP_ID,
            title=record.title,
            patch=patch,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Portfolio focus updated for section '{section_key}'.",
        )

    web_apps.register_web_app(
        app_id=PORTFOLIO_APP_ID,
        open_tool="open_portfolio",
        html_loader=get_portfolio_html,
        tools={
            "open_portfolio": open_portfolio,
            "load_portfolio_address": load_portfolio_address,
            "navigate_portfolio_relation": navigate_portfolio_relation,
            "load_portfolio_section": load_portfolio_section,
            "update_portfolio_focus": update_portfolio_focus,
        },
    )


__all__ = [
    "PORTFOLIO_APP_ID",
    "PORTFOLIO_URI",
    "get_portfolio_html",
    "register_portfolio_tools",
]
