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

// Mirrors the server `pair_depth_heatmap` projection (relative price bins).
const HEATMAP_COLUMNS = [
  "bucket", "bucket_mid", "rel_pct", "side", "depth_base", "orders", "bucket_seconds",
];

function makeProps(overrides: {
  pair?: Partial<CowExplorerViewState["pair"]>;
  depthAt?: string;
  depthRows?: unknown[][];
  openPairRows?: unknown[][];
  onSelectPair?: (base: string, quote: string) => void;
  onLoadDepthAt?: (ts: string | "live") => void;
  /** Attach a pair_depth_heatmap dataset (stub-error or genuine-empty). */
  heatmapError?: string;
  heatmapRows?: unknown[][];
  heatmapLoaded?: boolean | "partial";
  onLoadDepthHeatmap?: DepthPanelProps["onLoadDepthHeatmap"];
} = {}): DepthPanelProps {
  const state = {
    section: "markets",
    chain_id: 100,
    chain_name: "Gnosis",
    pair: { base: "0xbase", quote: "0xquote", base_symbol: "GNO", quote_symbol: "WXDAI", ...overrides.pair },
    depth_at: overrides.depthAt ?? "",
    loaded_groups: {
      "markets.depth": true,
      ...(overrides.heatmapLoaded !== undefined
        ? { "markets.depth_heatmap": overrides.heatmapLoaded }
        : {}),
    },
    icon_overlay: {},
    scope_id: "test",
  } as unknown as CowExplorerViewState;
  const depthRows = overrides.depthRows ?? [];
  const props: DepthPanelProps = {
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
    onLoadDepthHeatmap: overrides.onLoadDepthHeatmap,
  };
  if (overrides.heatmapError !== undefined || overrides.heatmapRows !== undefined) {
    const rows = overrides.heatmapRows ?? [];
    const heatmapDescriptor = descriptor("pair_depth_heatmap", HEATMAP_COLUMNS, rows);
    if (overrides.heatmapError) {
      // Stub-descriptor contract: a failed query ships zero rows with the
      // real error in provenance.coverage (see shared/datasetError.ts).
      (heatmapDescriptor.provenance as { coverage: Record<string, unknown> }).coverage = {
        mode: "reconstructed_point_in_time",
        warning_codes: ["query_failed"],
        error: overrides.heatmapError,
      };
    }
    props.descriptors.pair_depth_heatmap = heatmapDescriptor;
    props.hydrated.pair_depth_heatmap = hydrate(HEATMAP_COLUMNS, rows);
  }
  return props;
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
    // Two-floor note: slider reaches the backfill-reconstructed floor
    // (earliest_creation_seen) with the capture start as the fidelity boundary.
    expect(host.textContent).toContain("reaches back to 2026-03-02 09:12");
    expect(host.textContent).toContain("Books before 2026-07-20 15:58");
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

  it("surfaces a heatmap stub failure as an error card, not the empty state", () => {
    const calls: Array<[string, { force?: boolean } | undefined]> = [];
    const host = render(
      <DepthPanel
        {...makeProps({
          heatmapError: "ClickHouse ran out of memory (code 241).",
          heatmapRows: [],
          heatmapLoaded: "partial",
          onLoadDepthHeatmap: (window, opts) => calls.push([window, opts]),
        })}
      />,
    );
    const heatmapTab = [...host.querySelectorAll("button")].find((b) => b.textContent === "Footprint")!;
    act(() => heatmapTab.click());
    expect(host.textContent).toContain("ClickHouse ran out of memory (code 241).");
    expect(host.textContent).not.toContain("No resting depth reconstructed");
    // Retry must re-request with force so the server's negative failure
    // cache is bypassed (the tab-open request has no force flag).
    const retry = [...host.querySelectorAll("button")].find((b) => b.textContent === "Retry")!;
    act(() => retry.click());
    // Retry re-sends the CURRENT resolution too, not just the force flag.
    expect(calls[calls.length - 1]).toEqual(["7d", { force: true, bucketSeconds: 0 }]);
  });

  it("keeps the genuine-empty heatmap message and adds the rescue guidance", () => {
    const host = render(
      <DepthPanel
        {...makeProps({ heatmapRows: [], heatmapLoaded: true, openPairRows: [] })}
      />,
    );
    const heatmapTab = [...host.querySelectorAll("button")].find((b) => b.textContent === "Footprint")!;
    act(() => heatmapTab.click());
    expect(host.textContent).toContain("No resting depth reconstructed");
    expect(host.textContent).toContain("currently has no standing open intents on any");
  });

  it("renders a decodable colour legend instead of an unlabelled ramp", () => {
    // The regression guard for the whole redesign: the previous chart's
    // visualMap carried `formatter: () => ""` because its cell values were
    // unitless percentile ranks, so nothing on screen said what a colour meant.
    const host = render(
      <DepthPanel
        {...makeProps({
          heatmapRows: [
            ["2026-07-20T10:00:00Z", 100, 1, "ask", 5, 3, 3600],
            ["2026-07-20T10:00:00Z", 100, -1, "bid", 2, 1, 3600],
            ["2026-07-20T11:00:00Z", 101, 2, "ask", 40, 9, 3600],
          ],
          heatmapLoaded: true,
        })}
      />,
    );
    const footprintTab = [...host.querySelectorAll("button")].find((b) => b.textContent === "Footprint")!;
    act(() => footprintTab.click());
    const legend = host.querySelector(".cow-fp-legend")!;
    expect(legend).toBeTruthy();
    // Units are named, and both sides are keyed by which HALF of a cell they
    // occupy — side is position, not hue.
    expect(legend.textContent).toContain("resting depth (GNO)");
    expect(legend.textContent).toContain("left half");
    expect(legend.textContent).toContain("right half");
    expect(legend.querySelectorAll("tbody tr").length).toBe(2);
    // Every swatch carries a real range in its accessible name.
    const swatches = [...legend.querySelectorAll("tbody i")];
    expect(swatches.length).toBeGreaterThan(0);
    for (const swatch of swatches) {
      expect(swatch.getAttribute("aria-label")).toMatch(/GNO, \d+ cells$/);
    }
  });

  it("shows the FOOTPRINT's own docs and caveats in the (i), not the ladder's", () => {
    // The popover used to render pair_depth's docs on every tab, so the
    // footprint's approximations were documented but unreachable.
    const host = render(
      <DepthPanel
        {...makeProps({
          heatmapRows: [["2026-07-20T10:00:00Z", 100, 0, "ask", 5, 3, 3600]],
          heatmapLoaded: true,
        })}
      />,
    );
    const footprintTab = [...host.querySelectorAll("button")].find((b) => b.textContent === "Footprint")!;
    act(() => footprintTab.click());
    const infoButton = host.querySelector<HTMLButtonElement>(
      'button[aria-label="About this data"]',
    )!;
    act(() => infoButton.click());
    const text = host.textContent ?? "";
    expect(text).toContain("FOOTPRINT");
    // The three things the chart cannot show you must be stated.
    expect(text).toContain("MEDIAN PRICE OF THE ORDERS CREATED IN THAT BUCKET");
    expect(text).toContain("falls back to the window-wide median");
    expect(text).toContain("more than ±20% away are NOT charted");
  });

  it("states the ±20% clip in the always-visible footprint note", () => {
    const host = render(
      <DepthPanel
        {...makeProps({
          heatmapRows: [["2026-07-20T10:00:00Z", 100, 0, "ask", 5, 3, 3600]],
          heatmapLoaded: true,
        })}
      />,
    );
    const footprintTab = [...host.querySelectorAll("button")].find((b) => b.textContent === "Footprint")!;
    act(() => footprintTab.click());
    const note = host.querySelector(".cow-depth-note")!;
    expect(note.textContent).toContain("within ±20% of each bucket's market price");
    expect(note.textContent).toContain("time-weighted");
  });

  it("offers time-resolution chips valid for the selected window", () => {
    const host = render(<DepthPanel {...makeProps({ heatmapRows: [], heatmapLoaded: true })} />);
    const footprintTab = [...host.querySelectorAll("button")].find((b) => b.textContent === "Footprint")!;
    act(() => footprintTab.click());
    const labels = [...host.querySelectorAll("button")].map((b) => b.textContent);
    // Default window is 7d: auto plus widths yielding 8..120 buckets.
    expect(labels).toContain("auto");
    expect(labels).toContain("6h");
    // 15m over 7d would be 672 buckets — far past the row budget, so it is
    // not offered rather than silently coarsened.
    expect(labels).not.toContain("15m");
    // Both price-axis modes are reachable.
    expect(labels).toContain("% from market");
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
