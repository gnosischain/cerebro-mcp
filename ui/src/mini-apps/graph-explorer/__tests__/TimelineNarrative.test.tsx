// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import {
  parseTimelineNarrativeRows,
  timelineMatchesAppliedMoney,
  TimelineNarrativeTable,
  TimelineScopeDisclosure,
} from "../modes/TimelineView";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

function dataset(
  rows: unknown[][],
  columns: string[],
  overrides: Partial<HydratedDataset> = {},
): HydratedDataset {
  return {
    rows,
    columns,
    columnTypes: columns.map(() => "String"),
    phase: "complete",
    rowsLoaded: rows.length,
    rowsExpected: rows.length,
    error: null,
    hydrating: false,
    truncated: false,
    ...overrides,
  };
}

describe("Over time narrative", () => {
  it("invalidates every old dataset when the applied Money Trail scope changes", () => {
    const scope = {
      money_contract: {
        source_flow_scope_id: "flows:7",
        seed_ids: ["0xaaa"],
        direction: "out",
        tokens: ["0xtoken"],
        min_usd: 10,
        t0: "2026-06-01 00:00:00",
        t1: "2026-07-01 00:00:00",
      },
    };
    const flows = {
      seeds: ["0xaaa"],
      direction: "out" as const,
      hops: 1,
      range_days: 30,
      t0: "2026-06-01 00:00:00",
      t1: "2026-07-01 00:00:00",
      min_usd: 10,
      tokens: ["0xtoken"],
      include_bridges: false,
      node_count: 1,
      edge_count: 0,
      truncated: false,
      truncated_hops: [],
      expanded: {},
      token_catalog: [],
      scope: { scope_id: "flows:7" },
    };

    expect(timelineMatchesAppliedMoney(scope, flows as never)).toBe(true);
    for (const changed of [
      { ...flows, scope: { scope_id: "flows:8" } },
      { ...flows, seeds: ["0xbbb"] },
      { ...flows, direction: "in" as const },
      { ...flows, tokens: ["0xother"] },
      { ...flows, min_usd: 25 },
      { ...flows, t0: "2026-06-02 00:00:00" },
      { ...flows, t1: "2026-06-30 00:00:00" },
    ]) {
      expect(timelineMatchesAppliedMoney(scope, changed as never)).toBe(false);
    }
  });

  it("parses the compact Money Trail contract without turning unknown USD into zero", () => {
    const rows = parseTimelineNarrativeRows([
      ["2026-07-01", "0xabc", "0xtoken", "new", null, 12.5, null],
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      bucket: "2026-07-01",
      counterpartyId: "0xabc",
      tokenAddress: "0xtoken",
      change: "first_observed",
      previousKnownUsd: null,
      currentKnownUsd: 12.5,
      deltaKnownUsd: null,
      previousTokenAmount: null,
    });
  });

  it("accepts the transitional profile/scope columns and renders a keyboard-native table", async () => {
    const onSelect = vi.fn();
    const narrative = dataset(
      [["2026-07-01", "transfers", "0xabc", "", "increased", 10, 15, 5, "scope-7"]],
      [
        "bucket_start",
        "profile",
        "counterparty_id",
        "token_address",
        "change",
        "previous_value",
        "current_value",
        "delta_value",
        "scope_id",
      ],
    );

    await act(async () => {
      root.render(
        <TimelineNarrativeTable
          dataset={narrative}
          loading={false}
          onSelectCounterparty={onSelect}
        />,
      );
    });

    expect(container.querySelectorAll("th[scope=col]")).toHaveLength(10);
    expect(container.textContent).toContain("increased");
    expect(container.textContent).toContain("transfers");
    const button = container.querySelector<HTMLButtonElement>("button");
    expect(button?.type).toBe("button");
    await act(async () => button?.click());
    expect(onSelect).toHaveBeenCalledWith("0xabc");
  });

  it("renders the rich directional contract and keeps Mint/Burn terminals non-investigable", async () => {
    const onSelect = vi.fn();
    const zero = "0x0000000000000000000000000000000000000000";
    const token = "0x9999000000000000000000000000000000000009";
    const columns = [
      "bucket_start", "direction", "event_kind", "counterparty_id",
      "counterparty_label", "token_address", "token_symbol", "raw_amount",
      "normalized_amount", "transfer_count", "previous_token_amount",
      "current_token_amount", "delta_token_amount", "previous_known_usd",
      "current_known_usd", "delta_known_usd", "price_coverage",
      "volume_driven_usd_effect", "price_driven_usd_effect", "change",
      "scope_id",
    ];
    const narrative = dataset([[
      "2026-07-01", "in", "mint", zero, "Zero address", token, "TOK",
      "1000000000000000000", 1, 3, 0, 1, 1, 0, 2.5, 2.5, 1, 2.5, 0,
      "first_observed", "scope-8",
    ]], columns);

    const parsed = parseTimelineNarrativeRows(narrative.rows, narrative.columns);
    expect(parsed[0]).toMatchObject({
      direction: "in",
      eventKind: "mint",
      counterpartyId: zero,
      counterpartyLabel: "Zero address",
      rawAmount: "1000000000000000000",
      normalizedAmount: 1,
      transferCount: 3,
      priceCoverage: 1,
      volumeDrivenUsdEffect: 2.5,
      priceDrivenUsdEffect: 0,
    });

    await act(async () => {
      root.render(
        <TimelineNarrativeTable
          dataset={narrative}
          loading={false}
          onSelectCounterparty={onSelect}
        />,
      );
    });

    expect(container.textContent).toContain("Mint and Burn are supply events");
    expect(container.textContent).toContain(zero);
    expect(container.textContent).toContain(token);
    expect(container.textContent).toContain("raw 1000000000000000000");
    expect(container.textContent).toContain("volume +$2.50");
    expect(container.querySelector("button")).toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("withholds trend claims until verification is explicitly verified", async () => {
    const render = async (status: string) => {
      await act(async () => {
        root.render(
          <TimelineScopeDisclosure
            scope={{
              scope_id: "timeline:8",
              status: "ready",
              verification: { status },
              window: { t0: "2026-01-01", t1: "2026-07-01", source: "timeline.range_days" },
              data_horizon: "2026-06-30",
              coverage: { rows: { shown: 4, total: null } },
              sources: [{
                kind: "dbt_aggregate",
                name: "dbt.transfer_daily",
                role: "primary",
                status: "ok",
                fetched_at: "2026-07-19T00:00:00Z",
              }],
            }}
          />,
        );
      });
    };

    await render("unverified");
    expect(container.querySelector(".ge-scope-disclosure")).toBeNull();
    expect(container.querySelector(".ge-evidence-trigger")?.getAttribute("aria-label"))
      .toContain("Evidence: READY");
    expect(container.textContent).not.toContain("dbt.transfer_daily");
    await act(async () => {
      container.querySelector<HTMLButtonElement>(
        ".ge-evidence-trigger",
      )?.click();
    });
    expect(container.textContent).toContain("Trend claims withheld");
    expect(container.textContent).toContain("4 changes");
    expect(container.textContent).toContain("dbt.transfer_daily");

    await render("verified");
    expect(container.textContent).toContain("trend interpretation is enabled");
  });
});
