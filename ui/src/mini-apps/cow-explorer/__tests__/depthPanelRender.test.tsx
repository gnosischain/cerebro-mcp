// @vitest-environment jsdom
//
// Smoke-render coverage for the DepthPanel shell in the states that do not
// mount ECharts (jsdom has no layout): the pick-a-pair hint, the historical
// empty book (as-of chip + capture-window note), and the debounced
// onLoadDepthAt dispatch from the historical presets.

import { afterEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react";

import { DepthPanel, type DepthPanelProps } from "../components/DepthPanel";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import type { CowExplorerViewState } from "../types";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Array<{ root: Root; host: HTMLElement }> = [];
afterEach(() => {
  for (const { root, host } of roots.splice(0)) {
    act(() => root.unmount());
    host.remove();
  }
  vi.useRealTimers();
});

function render(node: React.ReactElement): HTMLElement {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => root.render(node));
  roots.push({ root, host });
  return host;
}

function descriptor(key: string, columns: string[], rows: unknown[][]): DatasetDescriptor {
  return {
    key, title: key, sql: "-- test", database: "cow_db",
    columns: columns.map((name) => ({ name, type: "Unknown" })),
    stats: { row_count: rows.length, rows_returned: rows.length, mode: "exact_capped", source_rows: rows.length, row_cap: 10000, truncated: false, warnings: [] },
    preview_rows: rows,
    provenance: { coverage: { mode: "observed_snapshot", warning_codes: [] } },
  } as unknown as DatasetDescriptor;
}

function hydrate(columns: string[], rows: unknown[][]): HydratedDataset {
  return {
    rows, columns, columnTypes: columns.map(() => "Unknown"),
    phase: "complete", rowsLoaded: rows.length, rowsExpected: rows.length,
    error: null, hydrating: false, truncated: false,
  };
}

const HORIZON_COLUMNS = [
  "earliest_supported_at", "latest_observed_at", "captured_orders",
  "earliest_creation_seen", "source_observed_at",
];
const HORIZON_ROW = ["2026-07-20T15:58:00Z", "2026-07-23T09:00:00Z", 1287, "2026-03-02T09:12:00Z", "2026-07-23T09:00:00Z"];
const DEPTH_COLUMNS = ["order_uid", "side", "price", "amount_base", "amount_quote", "source_observed_at"];

const OPEN_PAIRS_COLUMNS = [
  "token0", "token1", "token0_symbol", "token1_symbol", "open_orders", "source_observed_at",
];

function makeProps(overrides: {
  pair?: Partial<CowExplorerViewState["pair"]>;
  depthAt?: string;
  depthRows?: unknown[][];
  openPairRows?: unknown[][];
  onSelectPair?: (base: string, quote: string) => void;
  onLoadDepthAt?: (ts: string | "live") => void;
} = {}): DepthPanelProps {
  const state = {
    section: "markets",
    chain_id: 100,
    chain_name: "Gnosis",
    pair: { base: "0xbase", quote: "0xquote", base_symbol: "GNO", quote_symbol: "WXDAI", ...overrides.pair },
    depth_at: overrides.depthAt ?? "",
    loaded_groups: { "markets.depth": true },
    icon_overlay: {},
    scope_id: "test",
  } as unknown as CowExplorerViewState;
  const depthRows = overrides.depthRows ?? [];
  return {
    state,
    descriptors: {
      pair_depth: descriptor("pair_depth", DEPTH_COLUMNS, depthRows),
      depth_horizon: descriptor("depth_horizon", HORIZON_COLUMNS, [HORIZON_ROW]),
      open_intent_pairs: descriptor("open_intent_pairs", OPEN_PAIRS_COLUMNS, overrides.openPairRows ?? []),
    },
    hydrated: {
      pair_depth: hydrate(DEPTH_COLUMNS, depthRows),
      depth_horizon: hydrate(HORIZON_COLUMNS, [HORIZON_ROW]),
      open_intent_pairs: hydrate(OPEN_PAIRS_COLUMNS, overrides.openPairRows ?? []),
    },
    viewId: "test-view",
    fetchRows: async () => null,
    onEntity: () => undefined,
    onSelectPair: overrides.onSelectPair,
    onLoadDepthAt: overrides.onLoadDepthAt,
  };
}

describe("DepthPanel", () => {
  it("asks for a pair before rendering any book", () => {
    const host = render(<DepthPanel {...makeProps({ pair: { base: "", quote: "" } })} />);
    expect(host.textContent).toContain("Order-book depth");
    expect(host.textContent).toContain("Pick a pair");
  });

  it("renders the historical empty state with as-of chip and capture-window note", () => {
    const host = render(<DepthPanel {...makeProps({ depthAt: "2026-07-22T12:00:00Z" })} />);
    expect(host.textContent).toContain("As of 2026-07-22 12:00");
    expect(host.textContent).toContain("No reconstructable open intents at this time");
    expect(host.textContent).toContain("order-capture window (since 2026-07-20 15:58");
    expect(host.textContent).toContain("No known open intents");
  });

  it("offers rescue chips for pairs with a standing book and routes clicks to onSelectPair", () => {
    const picked: string[][] = [];
    const host = render(
      <DepthPanel
        {...makeProps({
          openPairRows: [
            ["0xaaa", "0xbbb", "WBNB", "USDT", 17, "2026-07-23T09:00:00Z"],
            ["0xccc", "0xddd", "", "CAKE", 5, "2026-07-23T09:00:00Z"],
          ],
          onSelectPair: (base, quote) => picked.push([base, quote]),
        })}
      />,
    );
    expect(host.textContent).toContain("Pairs with a standing book on Gnosis right now:");
    expect(host.textContent).toContain("WBNB/USDT");
    const chip = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("WBNB/USDT"));
    expect(chip).toBeTruthy();
    act(() => chip!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(picked).toEqual([["0xaaa", "0xbbb"]]);
  });

  it("explains a chain with zero standing intents instead of dead-ending", () => {
    const host = render(<DepthPanel {...makeProps({ openPairRows: [] })} />);
    expect(host.textContent).toContain("currently has no standing open intents on any");
    expect(host.textContent).toContain("Switch networks to find a live book.");
  });

  it("renders the live empty state with the Live chip", () => {
    const host = render(<DepthPanel {...makeProps()} />);
    expect(host.textContent).toContain("Live");
    expect(host.textContent).toContain("No known open intents for this pair right now.");
  });

  it("debounces historical preset clicks into onLoadDepthAt", () => {
    vi.useFakeTimers();
    const calls: Array<string | "live"> = [];
    const host = render(
      <DepthPanel {...makeProps({ depthAt: "2026-07-22T12:00:00Z", onLoadDepthAt: (ts) => calls.push(ts) })} />,
    );
    const liveButton = [...host.querySelectorAll("button")].find((b) => b.textContent === "Live")!;
    act(() => {
      liveButton.click();
    });
    expect(calls).toEqual([]); // not yet — debounced
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(calls).toEqual(["live"]);
  });
});
