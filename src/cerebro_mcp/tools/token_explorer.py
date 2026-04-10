"""Token Explorer mini app.

Exact, task-shaped mini app: pick a token, see metadata, bridge flows,
liquidity-provider counts, and (optionally) recent price history.

The launcher tool ``open_token_explorer`` returns a lightweight
``MiniAppPayload`` (no row data beyond the per-dataset preview page) that
the React frontend bound to ``ui://cerebro/token_explorer`` consumes via
the ext-apps SDK. The model can also drive the focus filter via
``update_token_explorer_focus``.
"""

from __future__ import annotations

import importlib.resources
import logging
from typing import Any

from mcp.types import CallToolResult, TextContent

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.mini_app_models import MiniAppPayload, SummaryCard
from cerebro_mcp.tools import mini_apps
from cerebro_mcp.tools.metadata import TOKEN_REGISTRY, _ADDRESS_TO_TOKEN

logger = logging.getLogger(__name__)


TOKEN_EXPLORER_APP_ID = "token_explorer"
TOKEN_EXPLORER_URI = "ui://cerebro/token_explorer"


# --- Bundled React UI ---

_BUNDLED_TOKEN_EXPLORER_HTML: str | None = None


def get_token_explorer_html() -> str:
    """Load the Vite-built single-file React app from the static package."""
    global _BUNDLED_TOKEN_EXPLORER_HTML
    if _BUNDLED_TOKEN_EXPLORER_HTML is None:
        try:
            _BUNDLED_TOKEN_EXPLORER_HTML = (
                importlib.resources.files("cerebro_mcp")
                .joinpath("static/token_explorer.html")
                .read_text("utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError):
            _BUNDLED_TOKEN_EXPLORER_HTML = (
                "<!doctype html><html><body>"
                "<div id='root'>token_explorer.html not built</div>"
                "</body></html>"
            )
    return _BUNDLED_TOKEN_EXPLORER_HTML


# --- SQL templates ---

_BRIDGE_FLOWS_SQL = """
SELECT
  date,
  bridge,
  source_chain,
  dest_chain,
  direction,
  volume_token,
  volume_usd,
  txs
FROM int_bridges_flows_daily
WHERE token = {token:String}
  AND date >= toDate({start_date:String})
ORDER BY date DESC
LIMIT 1500
"""

_LP_COUNTS_SQL = """
SELECT
  window,
  token,
  value AS unique_lp_count,
  change_pct
FROM fct_execution_pools_lps_latest
WHERE token = {token:String}
ORDER BY
  CASE window WHEN '7D' THEN 1 WHEN '30D' THEN 2 WHEN '90D' THEN 3 ELSE 4 END
"""

_PRICE_HISTORY_SQL = """
SELECT
  block_date AS date,
  symbol,
  avg(price) AS price_usd
FROM crawlers_data.dune_prices
WHERE symbol = {token:String}
  AND block_date >= toDate({start_date:String})
GROUP BY date, symbol
ORDER BY date DESC
LIMIT 365
"""


_HOLDERS_SQL = """
SELECT
  date,
  token,
  token_class,
  value AS holder_count
FROM api_execution_tokens_holders_daily
WHERE token = {token:String}
  AND date >= toDate({start_date:String})
ORDER BY date DESC
LIMIT 1500
"""

_POOL_TVL_SQL = """
SELECT
  date,
  token,
  label AS pool,
  tvl_type,
  value AS tvl_usd
FROM api_execution_pools_tvl_daily
WHERE token = {token:String}
  AND date >= toDate({start_date:String})
ORDER BY date DESC
LIMIT 1500
"""

_POOL_VOLUME_SQL = """
SELECT
  date,
  token,
  label AS pool,
  volume_type,
  value AS volume_usd
FROM api_execution_pools_volume_daily
WHERE token = {token:String}
  AND date >= toDate({start_date:String})
ORDER BY date DESC
LIMIT 1500
"""


def _resolve_token(symbol_or_address: str) -> dict[str, Any] | None:
    query = symbol_or_address.strip().lower()
    return TOKEN_REGISTRY.get(query) or _ADDRESS_TO_TOKEN.get(query)


def get_token_catalog() -> list[dict[str, Any]]:
    """Return an ordered list of tokens bundled in the launch payload.

    Read directly from the existing in-memory ``TOKEN_REGISTRY`` — no
    ClickHouse call. Keeps the launch payload small enough (<50 KB) to
    inline.
    """
    return [
        {
            "key": key,
            "symbol": info["symbol"],
            "name": info["name"],
            "address": info["address"],
            "decimals": info["decimals"],
            "has_price": info["address"] != "native",
        }
        for key, info in sorted(TOKEN_REGISTRY.items(), key=lambda pair: pair[1]["symbol"].lower())
    ]


def _metadata_dataset(
    ch: ClickHouseManager, token_info: dict[str, Any]
) -> "mini_apps.CachedDataset":
    """Build the metadata dataset entirely in-memory (no SQL needed)."""
    from cerebro_mcp.mini_app_cache import CachedDataset
    from cerebro_mcp.mini_app_models import DatasetStats

    columns = ["field", "value"]
    rows: list[list[Any]] = [
        ["symbol", token_info["symbol"]],
        ["name", token_info["name"]],
        ["address", token_info["address"]],
        ["decimals", token_info["decimals"]],
    ]
    stats = DatasetStats(
        row_count=len(rows),
        rows_returned=len(rows),
        mode="exact_bounded",
        sample_source_rows=len(rows),
        elapsed_seconds=0.0,
        warnings=[],
    )
    return CachedDataset(
        columns=columns,
        column_types=["str", "str"],
        rows=rows,
        stats=stats,
        sql="-- in-memory token registry --",
        database="dbt",
        parameters={"symbol": token_info["symbol"]},
    )


def _safe_load(
    ch: ClickHouseManager,
    sql: str,
    parameters: dict[str, Any],
    label: str,
) -> "mini_apps.CachedDataset":
    """Load a dataset and never raise.

    Token Explorer runs several independent queries (bridge flows, LP
    counts, price history) and a failure in one panel should not tank the
    whole view — we surface it as a per-panel preview_only warning
    instead, so the rest of the app still renders.
    """
    try:
        return mini_apps.load_bounded_dataset(
            ch, sql, database="dbt", parameters=parameters
        )
    except Exception as exc:
        logger.warning("token_explorer %s load failed: %s", label, exc)
        from cerebro_mcp.mini_app_cache import CachedDataset
        from cerebro_mcp.mini_app_models import DatasetStats

        stats = DatasetStats(
            row_count=0,
            rows_returned=0,
            mode="preview_only",
            sample_source_rows=0,
            elapsed_seconds=0.0,
            warnings=[f"{label} unavailable: {exc}"],
        )
        return CachedDataset(
            columns=[],
            column_types=[],
            rows=[],
            stats=stats,
            sql=sql,
            database="dbt",
            parameters=parameters,
        )


def _build_summary_cards(
    token_info: dict[str, Any],
    bridge_ds: "mini_apps.CachedDataset",
    lp_ds: "mini_apps.CachedDataset",
    price_ds: "mini_apps.CachedDataset | None",
) -> list[SummaryCard]:
    cards: list[SummaryCard] = [
        SummaryCard(
            label="Token",
            value=f"{token_info['symbol']} — {token_info['name']}",
            tone="neutral",
        )
    ]

    # Bridge volume + tx count from latest available row
    if bridge_ds.rows and "volume_usd" in bridge_ds.columns:
        vol_idx = bridge_ds.columns.index("volume_usd")
        tx_idx = (
            bridge_ds.columns.index("txs") if "txs" in bridge_ds.columns else None
        )
        latest_volume = 0.0
        latest_tx = 0
        for row in bridge_ds.rows:
            try:
                latest_volume += float(row[vol_idx] or 0)
                if tx_idx is not None:
                    latest_tx += int(row[tx_idx] or 0)
            except (TypeError, ValueError):
                continue
        cards.append(
            SummaryCard(
                label="Bridge volume (USD)",
                value=f"${latest_volume:,.0f}",
                tone="positive" if latest_volume > 0 else "neutral",
            )
        )
        cards.append(
            SummaryCard(
                label="Bridge txs",
                value=f"{latest_tx:,}",
                tone="neutral",
            )
        )
    else:
        cards.append(
            SummaryCard(label="Bridge volume (USD)", value="—", tone="neutral")
        )

    # LP count from the most recent window row (7D preferred)
    if lp_ds.rows and "unique_lp_count" in lp_ds.columns:
        win_idx = lp_ds.columns.index("window") if "window" in lp_ds.columns else None
        lp_idx = lp_ds.columns.index("unique_lp_count")
        change_idx = (
            lp_ds.columns.index("change_pct")
            if "change_pct" in lp_ds.columns
            else None
        )
        chosen = lp_ds.rows[0]
        if win_idx is not None:
            for row in lp_ds.rows:
                if str(row[win_idx]).upper() == "7D":
                    chosen = row
                    break
        try:
            lp_value = int(chosen[lp_idx] or 0)
        except (TypeError, ValueError):
            lp_value = 0
        delta = None
        if change_idx is not None and chosen[change_idx] is not None:
            try:
                delta = f"{float(chosen[change_idx]) * 100:+.1f}% vs prior"
            except (TypeError, ValueError):
                delta = None
        cards.append(
            SummaryCard(
                label="Unique LPs (7D)",
                value=f"{lp_value:,}",
                delta=delta,
                tone="neutral",
            )
        )
    else:
        cards.append(SummaryCard(label="Unique LPs (7D)", value="—", tone="neutral"))

    # Latest price
    if price_ds and price_ds.rows and "price_usd" in price_ds.columns:
        price_idx = price_ds.columns.index("price_usd")
        try:
            latest_price = float(price_ds.rows[0][price_idx] or 0)
        except (TypeError, ValueError):
            latest_price = 0.0
        cards.append(
            SummaryCard(
                label="Latest price (USD)",
                value=f"${latest_price:,.4f}",
                tone="neutral",
            )
        )

    return cards


def _build_empty_payload(
    *,
    view_id: str,
    title: str,
    catalog: list[dict[str, Any]],
) -> MiniAppPayload:
    """Catalog-only launch payload with no attached data."""
    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=view_id,
        app_id=TOKEN_EXPLORER_APP_ID,
        title=title,
        status="ready",
        summary_cards=[
            SummaryCard(
                label="Tokens available",
                value=str(len(catalog)),
                tone="neutral",
            ),
            SummaryCard(
                label="Status",
                value="Pick a token",
                tone="neutral",
            ),
        ],
        datasets={},
        view_state={
            "mode": "empty",
            "token_catalog": catalog,
            "selected_token": "",
            "start_date": "2024-01-01",
            "include_price": True,
            "selected_metric": "bridge_volume",
            "bridge": "",
            "direction": "both",
        },
        provenance={"source": "catalog", "catalog_size": len(catalog)},
        warnings=[],
    )


def _build_initial_payload(
    *,
    view_id: str,
    title: str,
    token_info: dict[str, Any],
    metadata_ds: "mini_apps.CachedDataset",
    bridge_ds: "mini_apps.CachedDataset",
    lp_ds: "mini_apps.CachedDataset",
    holders_ds: "mini_apps.CachedDataset | None" = None,
    pool_tvl_ds: "mini_apps.CachedDataset | None" = None,
    pool_vol_ds: "mini_apps.CachedDataset | None" = None,
    price_ds: "mini_apps.CachedDataset | None" = None,
    catalog: list[dict[str, Any]] | None = None,
    start_date: str = "2024-01-01",
    include_price: bool = True,
) -> MiniAppPayload:
    descriptors = {
        "metadata": mini_apps.build_dataset_descriptor(
            key="metadata", dataset=metadata_ds, title="Token metadata"
        ),
        "bridge_flows": mini_apps.build_dataset_descriptor(
            key="bridge_flows", dataset=bridge_ds, title="Bridge flows (daily)"
        ),
        "lp_counts": mini_apps.build_dataset_descriptor(
            key="lp_counts", dataset=lp_ds, title="Liquidity providers"
        ),
    }
    if holders_ds is not None and holders_ds.rows:
        descriptors["holders"] = mini_apps.build_dataset_descriptor(
            key="holders", dataset=holders_ds, title="Holder count (daily)"
        )
    if pool_tvl_ds is not None and pool_tvl_ds.rows:
        descriptors["pool_tvl"] = mini_apps.build_dataset_descriptor(
            key="pool_tvl", dataset=pool_tvl_ds, title="Pool TVL (USD, daily)"
        )
    if pool_vol_ds is not None and pool_vol_ds.rows:
        descriptors["pool_volume"] = mini_apps.build_dataset_descriptor(
            key="pool_volume", dataset=pool_vol_ds, title="Pool volume (USD, daily)"
        )
    if price_ds is not None:
        descriptors["price_history"] = mini_apps.build_dataset_descriptor(
            key="price_history", dataset=price_ds, title="Price history"
        )

    return MiniAppPayload(
        type="INITIAL_LOAD",
        view_id=view_id,
        app_id=TOKEN_EXPLORER_APP_ID,
        title=title,
        status="ready",
        summary_cards=_build_summary_cards(token_info, bridge_ds, lp_ds, price_ds),
        datasets=descriptors,
        view_state={
            "mode": "loaded",
            "token_catalog": catalog or [],
            "selected_token": token_info["symbol"],
            "start_date": start_date,
            "include_price": include_price and price_ds is not None,
            "selected_metric": "bridge_volume",
            "bridge": "",
            "direction": "both",
        },
        provenance={
            "source_tools": [
                "get_bridge_flows_by_token",
                "get_liquidity_providers_by_token",
            ],
            "token": token_info["symbol"],
        },
        warnings=mini_apps.collect_dataset_warnings(
            metadata_ds, bridge_ds, lp_ds, holders_ds, pool_tvl_ds, pool_vol_ds, price_ds
        ),
    )


def register_token_explorer_tools(mcp, ch: ClickHouseManager) -> None:
    """Register the Token Explorer launcher and delta tools."""

    mini_apps.register_app(
        TOKEN_EXPLORER_APP_ID,
        title="Token Explorer",
        resource_uri=TOKEN_EXPLORER_URI,
    )

    @mcp.resource(
        TOKEN_EXPLORER_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_token_explorer_app() -> str:
        """Serve the bundled Vite/React single-file app for the Token Explorer URI."""
        return get_token_explorer_html()

    def _load_token_into_view(
        *,
        view_id: str,
        title: str,
        token_info: dict[str, Any],
        start_date: str,
        include_price: bool,
    ) -> MiniAppPayload:
        """Run the four per-panel queries and attach datasets to a view."""
        metadata_ds = _metadata_dataset(ch, token_info)
        bridge_ds = _safe_load(
            ch,
            _BRIDGE_FLOWS_SQL,
            {"token": token_info["symbol"], "start_date": start_date},
            "bridge_flows",
        )
        lp_ds = _safe_load(
            ch,
            _LP_COUNTS_SQL,
            {"token": token_info["symbol"]},
            "lp_counts",
        )
        price_ds: "mini_apps.CachedDataset | None" = None
        if include_price and token_info["address"] != "native":
            price_ds = _safe_load(
                ch,
                _PRICE_HISTORY_SQL,
                {"token": token_info["symbol"], "start_date": start_date},
                "price_history",
            )

        holders_ds = _safe_load(
            ch,
            _HOLDERS_SQL,
            {"token": token_info["symbol"], "start_date": start_date},
            "holders",
        )
        pool_tvl_ds = _safe_load(
            ch,
            _POOL_TVL_SQL,
            {"token": token_info["symbol"], "start_date": start_date},
            "pool_tvl",
        )
        pool_vol_ds = _safe_load(
            ch,
            _POOL_VOLUME_SQL,
            {"token": token_info["symbol"], "start_date": start_date},
            "pool_volume",
        )

        mini_apps.attach_dataset(view_id, "metadata", metadata_ds)
        mini_apps.attach_dataset(view_id, "bridge_flows", bridge_ds)
        mini_apps.attach_dataset(view_id, "lp_counts", lp_ds)
        mini_apps.attach_dataset(view_id, "holders", holders_ds)
        mini_apps.attach_dataset(view_id, "pool_tvl", pool_tvl_ds)
        mini_apps.attach_dataset(view_id, "pool_volume", pool_vol_ds)
        if price_ds is not None:
            mini_apps.attach_dataset(view_id, "price_history", price_ds)

        catalog = get_token_catalog()
        payload = _build_initial_payload(
            view_id=view_id,
            title=title,
            token_info=token_info,
            metadata_ds=metadata_ds,
            bridge_ds=bridge_ds,
            lp_ds=lp_ds,
            holders_ds=holders_ds,
            pool_tvl_ds=pool_tvl_ds,
            pool_vol_ds=pool_vol_ds,
            price_ds=price_ds,
            catalog=catalog,
            start_date=start_date,
            include_price=include_price,
        )
        mini_apps.patch_view_state(view_id, payload.view_state)
        return payload

    @mcp.tool(
        meta={
            "ui": {"resourceUri": TOKEN_EXPLORER_URI},
            "ui/resourceUri": TOKEN_EXPLORER_URI,
        }
    )
    def open_token_explorer(
        symbol_or_address: str = "",
        start_date: str = "2024-01-01",
        include_price: bool = True,
        title: str = "",
    ) -> CallToolResult:
        """Open the Token Explorer mini app.

        Call with no arguments to land on a catalog picker — the user (or
        you) can then pick a token from the dropdown and the frontend will
        call ``load_token_explorer_token`` to fetch data.

        If ``symbol_or_address`` is provided, the app is launched with
        that token pre-loaded (metadata, bridge flows, LP counts, and
        optionally price history).

        Args:
            symbol_or_address: Optional token symbol (e.g. ``GNO``) or
                contract address. If empty, the launcher returns a
                catalog-only empty view.
            start_date: ISO date — bridge and price datasets start here.
            include_price: When True, also load 365 days of price history.
            title: Optional override for the view title.

        Returns:
            Interactive UI resource for ``ui://cerebro/token_explorer``.
        """
        # Empty-launch path: no ClickHouse call, just bundle the catalog.
        if not symbol_or_address.strip():
            view_id = mini_apps.create_view(
                TOKEN_EXPLORER_APP_ID, title or "Token Explorer"
            )
            payload = _build_empty_payload(
                view_id=view_id,
                title=title or "Token Explorer",
                catalog=get_token_catalog(),
            )
            mini_apps.patch_view_state(view_id, payload.view_state)
            return mini_apps.payload_to_call_tool_result(
                payload,
                summary_text=(
                    f"Token Explorer ready — pick a token from the catalog. "
                    f"view_id={view_id[:8]}"
                ),
            )

        # Pre-loaded path: resolve the token, fail fast if unknown.
        token_info = _resolve_token(symbol_or_address)
        if token_info is None:
            return mini_apps.error_call_tool_result(
                f"Token '{symbol_or_address}' not found in registry."
            )

        view_id = mini_apps.create_view(
            TOKEN_EXPLORER_APP_ID,
            title or f"Token Explorer — {token_info['symbol']}",
        )
        payload = _load_token_into_view(
            view_id=view_id,
            title=title or f"Token Explorer — {token_info['symbol']}",
            token_info=token_info,
            start_date=start_date,
            include_price=include_price,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Token Explorer ready for {token_info['symbol']} "
                f"(view_id={view_id[:8]})"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": TOKEN_EXPLORER_URI},
            "ui/resourceUri": TOKEN_EXPLORER_URI,
        }
    )
    def load_token_explorer_token(
        view_id: str,
        symbol_or_address: str,
        start_date: str = "2024-01-01",
        include_price: bool = True,
    ) -> CallToolResult:
        """Load data for a specific token into an existing Token Explorer view.

        Used by the in-app dropdown and by the LLM to swap tokens without
        opening a fresh view. Re-emits ``INITIAL_LOAD`` for the same
        ``view_id`` so the frontend replaces the datasets in place while
        the catalog stays visible.
        """
        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        token_info = _resolve_token(symbol_or_address)
        if token_info is None:
            return mini_apps.error_call_tool_result(
                f"Token '{symbol_or_address}' not found in registry."
            )

        payload = _load_token_into_view(
            view_id=view_id,
            title=record.title or f"Token Explorer — {token_info['symbol']}",
            token_info=token_info,
            start_date=start_date,
            include_price=include_price,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=(
                f"Token Explorer loaded {token_info['symbol']} into view "
                f"{view_id[:8]}"
            ),
        )

    @mcp.tool(
        meta={
            "ui": {"resourceUri": TOKEN_EXPLORER_URI},
            "ui/resourceUri": TOKEN_EXPLORER_URI,
        }
    )
    def update_token_explorer_focus(
        view_id: str,
        metric: str,
        bridge: str = "",
        direction: str = "",
    ) -> CallToolResult:
        """Patch the Token Explorer focus filters in an open view.

        Args:
            view_id: The view ID returned by ``open_token_explorer``.
            metric: One of ``bridge_volume``, ``bridge_txs``, ``lp_count``,
                ``price``.
            bridge: Optional bridge name filter (empty string = all).
            direction: Optional flow direction (``inbound``/``outbound``/``""``).
        """
        allowed_metrics = {"bridge_volume", "bridge_txs", "lp_count", "price"}
        if metric not in allowed_metrics:
            return mini_apps.error_call_tool_result(
                f"metric must be one of {sorted(allowed_metrics)}"
            )

        record = mini_apps.get_view(view_id)
        if record is None:
            return mini_apps.error_call_tool_result(
                f"Unknown or expired view_id: {view_id}"
            )

        patch = {
            "selected_metric": metric,
            "bridge": bridge,
            "direction": direction,
        }
        mini_apps.patch_view_state(view_id, patch)

        payload = MiniAppPayload(
            type="PATCH_VIEW_STATE",
            view_id=view_id,
            app_id=TOKEN_EXPLORER_APP_ID,
            title=record.title,
            patch=patch,
        )
        return mini_apps.payload_to_call_tool_result(
            payload,
            summary_text=f"Token Explorer focus updated → {metric}",
        )


__all__ = [
    "TOKEN_EXPLORER_APP_ID",
    "TOKEN_EXPLORER_URI",
    "register_token_explorer_tools",
    "get_token_explorer_html",
]
