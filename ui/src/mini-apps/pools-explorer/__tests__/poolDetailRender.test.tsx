// @vitest-environment jsdom
//
// Smoke-render coverage for PoolDetail across the pool shapes the data forces:
// a probed Uniswap pool (P_WETH), a single full-range Swapr pool (P_FULL), a
// state-only pool (P_ZERO), a reserves-only Balancer pool (P_BAL), a failed
// pool_profile_at stub (error card + Retry -> group retry), the debounced
// profile date picker, and the ONE-SHOT heatmap load. ChartCard is mocked —
// jsdom has no layout for ECharts; the option builders have their own tests.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../components/ChartCard", () => ({
  ChartCard: ({ chartId }: { chartId: string }) => <div data-chart={chartId} />,
}));

import { PoolDetail } from "../detail/PoolDetail";
import { P_BAL, P_FULL, P_NOSTATE, P_WETH, P_ZERO, entityPayload } from "../devFixture";
import type { PlxViewContext } from "../sections/common";
import { EMPTY_DRAFT } from "../state/toolArgs";
import type { PoolsExplorerViewState } from "../types";
import { DEFAULT_CLIENT_STATE, type PlxClientState } from "../urlState";

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

interface Spy {
  retries: Array<[string, string]>;
  dates: string[];
  heatmaps: Array<[string, { force?: boolean } | undefined]>;
}

function ctxFor(
  address: string,
  overrides: { client?: Partial<PlxClientState>; state?: Partial<PoolsExplorerViewState>; failProfile?: boolean } = {},
): { ctx: PlxViewContext; spy: Spy } {
  const payload = entityPayload("pool", address);
  const descriptors = { ...payload.datasets! };
  if (overrides.failProfile) {
    // Stub-descriptor contract: a failed query ships zero rows with the real
    // error in provenance.coverage.
    descriptors.pool_profile_at = {
      ...descriptors.pool_profile_at,
      preview_rows: [],
      stats: { ...descriptors.pool_profile_at.stats, row_count: 0, rows_returned: 0 },
      provenance: { coverage: { mode: "publication_verified", warning_codes: ["query_failed"], error: "ClickHouse ran out of memory (code 241)." } },
    };
  }
  const spy: Spy = { retries: [], dates: [], heatmaps: [] };
  const state: PoolsExplorerViewState = { ...payload.view_state!, ...overrides.state };
  const ctx: PlxViewContext = {
    state,
    descriptors,
    hydrated: {},
    viewId: "test",
    fetchRows: async () => null,
    draft: { ...EMPTY_DRAFT },
    setDraft: () => undefined,
    apply: () => undefined,
    loading: false,
    onEntity: () => undefined,
    failedGroups: [],
    retryGroup: (section, group) => spy.retries.push([section, group]),
    openLink: () => undefined,
    onLoadProfileDate: (date) => spy.dates.push(date),
    onLoadHeatmap: (window, opts) => spy.heatmaps.push([window, opts]),
    client: { ...DEFAULT_CLIENT_STATE, ...overrides.client },
    setClient: () => undefined,
  };
  return { ctx, spy };
}

const tabLabels = (host: HTMLElement) => [...host.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent);

describe("PoolDetail", () => {
  it("renders the probed Uniswap pool: header badges, six tabs, the profile strip and ranges table", () => {
    const { ctx } = ctxFor(P_WETH);
    const host = render(<PoolDetail ctx={ctx} />);
    const text = host.textContent ?? "";
    expect(text).toContain("uniswap_v3 · 0x0cf4");
    expect(text).toContain("Uniswap v3");
    expect(text).toContain("0.30%");
    expect(text).toContain("probed");
    expect(tabLabels(host)).toEqual(["Profile", "History", "Reserves", "Fees", "Ticks", "Publication"]);
    expect(text).toContain("22 ranges");
    expect(text).toContain("matches state liquidity");
    expect(host.querySelector('[data-chart="plx-profile"]')).toBeTruthy();
    expect(host.querySelector(".plx-stateonly")).toBeNull();
    // The price is decimals-adjusted (18/18): no raw marker in the header.
    expect(host.querySelector(".plx-header .plx-price--raw")).toBeNull();
  });

  it("renders the single full-range Swapr pool as a band with raw-unit disclosure", () => {
    const { ctx } = ctxFor(P_FULL);
    const host = render(<PoolDetail ctx={ctx} />);
    const text = host.textContent ?? "";
    expect(text).toContain("1 ranges");
    expect(text).toContain("full-range L");
    expect(text).toContain("prices in raw units");
    expect(host.querySelector(".plx-badge--unresolved")).toBeTruthy();
    expect(host.querySelector(".plx-header .plx-price--raw")).toBeTruthy();
    expect(text).toContain("dyn");
  });

  it("renders the state-only stub on Profile / Ticks / Fees for an unprobed pool, never empty charts", () => {
    const { ctx } = ctxFor(P_ZERO);
    const host = render(<PoolDetail ctx={ctx} />);
    expect(host.querySelector(".plx-stateonly")).toBeTruthy();
    expect(host.textContent).toContain("cl_below_active_threshold");
    expect(host.querySelector('[data-chart="plx-profile"]')).toBeNull();
    expect(host.textContent).toContain("state only");
    for (const tab of ["ticks", "fees"] as const) {
      const other = render(<PoolDetail ctx={ctxFor(P_ZERO, { client: { tab } }).ctx} />);
      expect(other.querySelector(".plx-stateonly"), tab).toBeTruthy();
      expect(other.querySelector(`[data-chart="plx-${tab}"]`), tab).toBeNull();
    }
    // History stays available: state exists even without a probe.
    const history = render(<PoolDetail ctx={ctxFor(P_ZERO, { client: { tab: "history" } }).ctx} />);
    expect(history.querySelector('[data-chart="plx-price"]')).toBeTruthy();
  });

  it("renders a Balancer pool with only the Reserves and Publication tabs and one axis per asset", () => {
    const { ctx } = ctxFor(P_BAL);
    const host = render(<PoolDetail ctx={ctx} />);
    expect(tabLabels(host)).toEqual(["Reserves", "Publication"]);
    // Default tab "profile" is coerced to the first available tab.
    expect(host.querySelector('[data-chart="plx-reserves"]')).toBeTruthy();
    expect(host.textContent).toContain("reserves only");
    expect(host.textContent).toContain("Balancer v2");
    expect(host.querySelector(".plx-stateonly")).toBeNull();
  });

  it("surfaces a failed pool_profile_at stub as an error card whose Retry re-requests the profile group", () => {
    const { ctx, spy } = ctxFor(P_WETH, { failProfile: true, state: { loaded_groups: { ...entityPayload("pool", P_WETH).view_state!.loaded_groups, "pool.profile": "partial" } } });
    const host = render(<PoolDetail ctx={ctx} />);
    expect(host.textContent).toContain("ClickHouse ran out of memory (code 241).");
    expect(host.querySelector('[data-chart="plx-profile"]')).toBeNull();
    const retry = [...host.querySelectorAll("button")].find((button) => button.textContent === "Retry")!;
    act(() => retry.click());
    expect(spy.retries).toEqual([["pool", "profile"]]);
  });

  it("debounces the profile date picker into ONE onLoadProfileDate call", () => {
    vi.useFakeTimers();
    const { ctx, spy } = ctxFor(P_WETH);
    const host = render(<PoolDetail ctx={ctx} />);
    const preset = [...host.querySelectorAll("button")].find((button) => button.textContent === "−30d")!;
    act(() => preset.click());
    const latest = [...host.querySelectorAll("button")].find((button) => button.textContent === "Latest")!;
    act(() => latest.click());
    expect(spy.dates).toEqual([]);
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(spy.dates).toEqual([""]);
    act(() => preset.click());
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(spy.dates).toEqual(["", "2026-08-17"]);
    expect(host.textContent).toContain("profile since 2023-10-02");
  });

  it("loads the heatmap exactly once when the Over time view opens, and forces on retry", () => {
    const base = entityPayload("pool", P_WETH).view_state!;
    const { ctx, spy } = ctxFor(P_WETH, {
      client: { view: "over_time" },
      state: { loaded_groups: { ...base.loaded_groups, "pool.heatmap": false } },
    });
    delete ctx.descriptors.pool_profile_heatmap;
    const host = render(<PoolDetail ctx={ctx} />);
    expect(spy.heatmaps).toEqual([["1y", undefined]]);
    // A re-render with the same scope/pool/window never re-fires the request.
    act(() => roots[roots.length - 1].root.render(<PoolDetail ctx={{ ...ctx }} />));
    expect(spy.heatmaps).toHaveLength(1);
    expect(host.querySelector('[aria-label="Loading liquidity heatmap"]')).toBeTruthy();
    // A failed stub renders the error card; Retry bypasses the failure cache.
    const failed = ctxFor(P_WETH, { client: { view: "over_time" }, state: { loaded_groups: { ...base.loaded_groups, "pool.heatmap": "partial" } } });
    failed.ctx.descriptors.pool_profile_heatmap = {
      ...failed.ctx.descriptors.pool_profile_heatmap,
      preview_rows: [],
      stats: { ...failed.ctx.descriptors.pool_profile_heatmap.stats, row_count: 0, rows_returned: 0 },
      provenance: { coverage: { warning_codes: ["query_failed"], error: "budget exceeded" } },
    };
    const failedHost = render(<PoolDetail ctx={failed.ctx} />);
    expect(failedHost.textContent).toContain("budget exceeded");
    const retry = [...failedHost.querySelectorAll("button")].find((button) => button.textContent === "Retry")!;
    act(() => retry.click());
    expect(failed.spy.heatmaps[failed.spy.heatmaps.length - 1]).toEqual(["1y", { force: true }]);
  });

  it("renders the preloaded heatmap with a labelled legend and skips the request", () => {
    const { ctx, spy } = ctxFor(P_WETH, { client: { view: "over_time" } });
    const host = render(<PoolDetail ctx={ctx} />);
    expect(spy.heatmaps).toEqual([]);
    expect(host.querySelector('[data-chart="plx-heatmap"]')).toBeTruthy();
    const legend = host.querySelector(".plx-legend")!;
    expect(legend.textContent).toContain("active liquidity (L)");
    expect(legend.textContent).toContain("current tick");
    for (const swatch of legend.querySelectorAll("tbody i")) {
      expect(swatch.getAttribute("aria-label")).toMatch(/cells$/);
    }
  });

  it("distinguishes a pool with NO state row from one whose liquidity is zero", () => {
    const noState = render(<PoolDetail ctx={ctxFor(P_NOSTATE).ctx} />);
    expect(noState.textContent).toContain("no state row");
    expect(noState.textContent).not.toContain("no liquidity");
    // P_ZERO has a published state row and genuinely zero liquidity.
    const zero = render(<PoolDetail ctx={ctxFor(P_ZERO).ctx} />);
    expect(zero.textContent).toContain("no liquidity");
    expect(zero.textContent).not.toContain("no state row");
  });

  it("explains an address that is not a configured pool", () => {
    const { ctx } = ctxFor("0x0000000000000000000000000000000000000001");
    const host = render(<PoolDetail ctx={ctx} />);
    expect(host.textContent).toContain("is not a configured pool");
  });
});
