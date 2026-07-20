import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { FlowsView } from "../modes/FlowsView";
import { buildInitialState } from "../state/graphReducer";
import type { GraphExplorerViewState } from "../types";

describe("FlowsView forensic safety", () => {
  it("renders a first-load failure even when no graph rows exist", () => {
    const server = {} as GraphExplorerViewState;
    const html = renderToStaticMarkup(
      <FlowsView
        server={server}
        local={buildInitialState(server)}
        dispatch={vi.fn()}
        flowNodes={undefined}
        flowEdges={undefined}
        nodeEvidence={undefined}
        edgeEvidence={undefined}
        evidenceExpectation={null}
        requestFlows={vi.fn()}
        traceFlow={vi.fn()}
        loading={false}
        loadError="source relation was dropped"
        onSelectNode={vi.fn()}
        onSelectEdge={vi.fn()}
        onClearSelection={vi.fn()}
        onBrowseInvestigate={vi.fn()}
        onOpenTransactions={vi.fn()}
      />,
    );

    expect(html).toContain("Money Trail load failed");
    expect(html).toContain("source relation was dropped");
    expect(html).toContain('role="alert"');
  });

  it("renders a failed forensic scope even when the tool promise resolved", () => {
    const server = {
      flows: {
        seeds: ["0x1111000000000000000000000000000000000001"],
        direction: "out",
        hops: 1,
        range_days: 30,
        t0: "2026-06-01 00:00:00",
        t1: "2026-07-01 00:00:00",
        min_usd: 10,
        tokens: [],
        include_bridges: true,
        node_count: 0,
        edge_count: 0,
        truncated: false,
        truncated_hops: [],
        expanded: {},
        token_catalog: [],
        scope: {
          status: "failed",
          warnings: ["flow source contract failed: relation missing"],
        },
      },
    } as unknown as GraphExplorerViewState;
    const html = renderToStaticMarkup(
      <FlowsView
        server={server}
        local={buildInitialState(server)}
        dispatch={vi.fn()}
        flowNodes={undefined}
        flowEdges={undefined}
        nodeEvidence={undefined}
        edgeEvidence={undefined}
        evidenceExpectation={null}
        requestFlows={vi.fn()}
        traceFlow={vi.fn()}
        loading={false}
        loadError={null}
        onSelectNode={vi.fn()}
        onSelectEdge={vi.fn()}
        onClearSelection={vi.fn()}
        onBrowseInvestigate={vi.fn()}
        onOpenTransactions={vi.fn()}
      />,
    );

    expect(html).toContain("Money Trail load failed");
    expect(html).toContain("flow source contract failed: relation missing");
  });

  it("keeps measured flow coverage and admission bounds in the compact summary", () => {
    const server = {
      flows: {
        seeds: ["0x1111000000000000000000000000000000000001"],
        direction: "out",
        hops: 2,
        range_days: 30,
        t0: "2026-06-01 00:00:00",
        t1: "2026-07-01 00:00:00",
        min_usd: 10,
        tokens: [],
        include_bridges: true,
        node_count: 0,
        edge_count: 0,
        truncated: true,
        truncated_hops: [1],
        expanded: {},
        token_catalog: [],
        scope: {
          scope_id: "flows:coverage:7",
          request_id: 7,
          status: "partial",
          window: {
            t0: "2026-06-01 00:00:00",
            t1: "2026-07-01 00:00:00",
            source: "money.applied",
          },
          data_horizon: "2026-07-01",
          sources: [
            {
              kind: "dbt_aggregate",
              name: "int_execution_transfers_whitelisted_daily",
              role: "primary",
              status: "ok",
              horizon: "2026-07-01",
              fetched_at: "2026-07-19T12:34:56Z",
            },
          ],
          coverage: {
            rows: { shown: 400, total: 2404 },
            nodes: { shown: 401, total: 2405 },
            edges: { shown: 400, total: 2404 },
            usd: { known: 912, total: 1000, unknown_rows: 3 },
          },
          truncation: {
            truncated: true,
            rule: "per-hop node budget 400, admitted USD-descending",
          },
          truncation_coverage: {
            budget_per_hop: 400,
            shown_counterparties: 400,
            total_counterparties: 2404,
            dropped_counterparties: 2004,
            retained_usd_fraction: 0.912,
            counting_basis: "sum_of_per_hop_unique_counterparties",
          },
          residuals: ["Native xDAI transfers are not represented."],
          warnings: [
            "Whitelisted-token aggregate excludes non-whitelisted money-flow discovery.",
          ],
          verification: { status: "verified", method: "companion count" },
        },
      },
    } as unknown as GraphExplorerViewState;

    const html = renderToStaticMarkup(
      <FlowsView
        server={server}
        local={buildInitialState(server)}
        dispatch={vi.fn()}
        flowNodes={undefined}
        flowEdges={undefined}
        nodeEvidence={undefined}
        edgeEvidence={undefined}
        evidenceExpectation={null}
        requestFlows={vi.fn()}
        traceFlow={vi.fn()}
        loading={false}
        loadError={null}
        onSelectNode={vi.fn()}
        onSelectEdge={vi.fn()}
        onClearSelection={vi.fn()}
        onBrowseInvestigate={vi.fn()}
        onOpenTransactions={vi.fn()}
      />,
    );

    expect(html).not.toContain("400/2,404 counterparties");
    expect(html).not.toContain("2,004 dropped");
    expect(html).not.toContain("91.2% measured USD retained");
    expect(html).toContain("aria-label=\"Evidence: PARTIAL, 1 source");
    expect(html).not.toContain("ge-scope-disclosure");
    expect(html).not.toContain("int_execution_transfers_whitelisted_daily");
    const warning =
      "Whitelisted-token aggregate excludes non-whitelisted money-flow discovery.";
    expect(html).not.toContain(warning);
  });

  it("puts the authoritative table before the segmented map and exposes unpriced evidence", () => {
    const seed = "0x1111000000000000000000000000000000000001";
    const recipient = "0x2222000000000000000000000000000000000002";
    const token = "0xaaaa000000000000000000000000000000000001";
    const server = {
      flows: {
        seeds: [seed],
        direction: "out",
        hops: 1,
        range_days: 30,
        t0: "2026-06-01 00:00:00",
        t1: "2026-07-01 00:00:00",
        min_usd: 10,
        tokens: [],
        include_bridges: false,
        node_count: 2,
        edge_count: 1,
        truncated: false,
        truncated_hops: [],
        expanded: {},
        token_catalog: [],
        scope: {
          scope_id: "flows:test:1",
          request_id: 1,
          status: "partial",
          window: {
            t0: "2026-06-01 00:00:00",
            t1: "2026-07-01 00:00:00",
            source: "range_days=30",
          },
          data_horizon: "2026-07-01",
          sources: [],
          coverage: {
            rows: { shown: 1, total: 1 },
            nodes: { shown: 2, total: 2 },
            edges: { shown: 1, total: 1 },
            usd: { known: 0, total: null, unknown_rows: 1 },
          },
          truncation: { truncated: false, rule: null },
          residuals: [],
          warnings: [],
          verification: { status: "verified", method: "test fixture" },
          truncation_coverage: {
            budget_per_hop: 400,
            shown_counterparties: 1,
            total_counterparties: 1,
          },
          token_universe: {
            addresses: [token],
            count: 1,
            as_of: "2026-07-01 00:00:00",
            source: "dbt.stg_pools__tokens_meta",
            sha256: "abc",
          },
        },
      },
    } as unknown as GraphExplorerViewState;
    const dataset = (rows: unknown[][]) => ({
      rows,
      columns: [],
      columnTypes: [],
      phase: "complete" as const,
      rowsLoaded: rows.length,
      rowsExpected: rows.length,
      error: null,
      hydrating: false,
      truncated: false,
    });
    const html = renderToStaticMarkup(
      <FlowsView
        server={server}
        local={buildInitialState(server)}
        dispatch={vi.fn()}
        flowNodes={dataset([
          [seed, "Seed", "", "", 0, 0, null, "", "", []],
          [recipient, "Recipient", "", "", 1, null, 0, "", "", []],
        ])}
        flowEdges={dataset([
          [
            `flow:${seed}->${recipient}:${token}`,
            seed,
            recipient,
            "transfer",
            token,
            "UNK",
            12,
            null,
            2,
            "2026-06-02",
            "2026-06-20",
            1,
          ],
        ])}
        nodeEvidence={undefined}
        edgeEvidence={undefined}
        evidenceExpectation={null}
        requestFlows={vi.fn()}
        traceFlow={vi.fn()}
        loading={false}
        loadError={null}
        onSelectNode={vi.fn()}
        onSelectEdge={vi.fn()}
        onClearSelection={vi.fn()}
        onBrowseInvestigate={vi.fn()}
        onOpenTransactions={vi.fn()}
      />,
    );

    expect(html.indexOf("Ranked movements")).toBeGreaterThanOrEqual(0);
    expect(html.indexOf("Ranked movements")).toBeLessThan(
      html.indexOf("Sankey-style hop map"),
    );
    expect(html).not.toContain("Evidence table · authoritative");
    expect(html).toContain("Resize evidence table and Sankey");
    expect(html).toContain("Aggregated transfer adjacency — not transaction-matched custody");
    expect(html).toContain("Unpriced");
    expect(html).toContain("categorical width; value unknown");
    expect(html).not.toContain("1 applied token");
    expect(html).toContain("ge-evidence-trigger");
    expect(html).not.toContain("500 visible");
  });
});
