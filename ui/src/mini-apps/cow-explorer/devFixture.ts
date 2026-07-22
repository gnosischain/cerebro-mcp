import type { DatasetDescriptor, MiniAppPayload } from "../shared/miniAppTypes";
import type { CowExplorerViewState } from "./types";

function descriptor(key: string, columns: string[], rows: unknown[][]): DatasetDescriptor {
  return {
    key, title: key.split("_").join(" "), sql: "-- development fixture", database: "cow_db",
    columns: columns.map((name) => ({ name, type: "Unknown" })),
    stats: { row_count: rows.length, rows_returned: rows.length, mode: "exact_capped", source_rows: rows.length, row_cap: 10000, truncated: false, warnings: [] },
    preview_rows: rows,
    provenance: { coverage: { actual_start: "2026-07-01T00:00:00Z", actual_end: "2026-07-20T00:00:00Z", mode: "checkpoint_bounded", warning_codes: [] } },
  };
}

export const MOCK_PAYLOAD: MiniAppPayload<CowExplorerViewState> = {
  type: "INITIAL_LOAD", view_id: "cow-dev", app_id: "cow_explorer", title: "CoW Data Explorer", status: "ready",
  datasets: {
    network_summary: descriptor("network_summary", ["chain_id", "trade_count", "order_count", "competition_count_all_indexed"], [[1, 694210, 522000, 10220], [100, 496201, 310400, 8110]]),
    network_activity: descriptor("network_activity", ["bucket", "chain_id", "trade_count"], [["2026-07-18", 1, 820], ["2026-07-19", 1, 910]]),
    top_pairs: descriptor("top_pairs", ["chain_id", "token0", "token1", "fill_count"], [[1, "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 12000]]),
  },
  view_state: {
    section: "overview", environment_scope: "production", environment: "production", chain_id: 0, chain_name: "All networks",
    chain_options: [{ chain_id: 1, name: "Ethereum", native_symbol: "ETH", environment: "production", explorer: { provider: "blockscout", brand: "Blockscout", base_url: "https://eth.blockscout.com", transaction_url_template: "https://eth.blockscout.com/tx/{hash}", address_url_template: "https://eth.blockscout.com/address/{address}", token_url_template: "https://eth.blockscout.com/token/{address}" } }],
    explorer: null, pair: { base: "", quote: "", base_symbol: "", quote_symbol: "" }, interval: "1h",
    date_range: { kind: "relative", anchor: "latest_indexed", window_days: 30, start_at: "", end_at: "" },
    filters: { status: "", owner: "", solver: "", token: "" }, selected_entity: null, breadcrumbs: [],
    search: { query: "", candidates: [] }, applied_request_id: 0, scope_id: "production:0:overview:0",
    coverage: {}, coverage_warnings: ["partial_backfill"], warnings: ["partial_backfill"], dataset_revisions: { network_summary: 1, network_activity: 1, top_pairs: 1 },
    loaded_groups: {
      "overview.core": true, "overview.breakdown": true,
      "markets.core": false, "markets.charts": false, "markets.tape": false,
      "trades.core": false, "trades.tape": false,
      "orders.core": false, "orders.intents": false, "orders.quality": false,
      "auctions.core": false, "auctions.list": false,
      "solvers.core": false, "solvers.detail": false,
      "traders.core": false,
      "patterns.core": false, "patterns.affinity": false, "patterns.quality": false,
      "live.core": false, "live.feed": false, "live.intents": false,
    },
    section_fingerprints: { overview: "dev" },
    section_datasets: { overview: ["network_summary", "network_activity", "top_pairs"] },
    section_lru: ["overview"],
    icon_overlay: {},
    dataset_titles: {},
  },
};
