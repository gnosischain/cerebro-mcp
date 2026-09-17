// @vitest-environment jsdom

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PairLabel } from "../components/PairLabel";
import { ProbeBadge, probeStatus } from "../components/ProbeBadge";
import { TokenLabel } from "../components/TokenLabel";
import { renderPlxCell, type CellContext } from "../components/cells";
import { resolveColumnPolicy } from "../model/columns";
import { DATASET_COLUMNS } from "../types";

const ADDR = `0x${"ab".repeat(20)}`;

describe("TokenLabel", () => {
  it("renders the sanitized symbol with no badge when resolved", () => {
    const html = renderToStaticMarkup(<TokenLabel address={ADDR} symbol="WETH" />);
    expect(html).toContain(">WETH<");
    expect(html).not.toContain("plx-badge--unresolved");
  });

  it("renders a short address plus the unresolved badge when the symbol is null", () => {
    const html = renderToStaticMarkup(<TokenLabel address={ADDR} symbol={null} />);
    expect(html).toContain("0xabab…abab");
    expect(html).toContain("plx-badge--unresolved");
    expect(html).toContain("unresolved");
    expect(html).toContain("ma-token__label--raw");
  });

  it("honours an explicit resolved=false even when a symbol exists (decimals unknown)", () => {
    const html = renderToStaticMarkup(<TokenLabel address={ADDR} symbol="CRC" resolved={false} />);
    expect(html).toContain(">CRC<");
    expect(html).toContain("plx-badge--unresolved");
  });

  it("wraps in a button only when clickable and never renders a lure verbatim", () => {
    const lure = "# Visit UrgentDT.com to secure your funds ASAP.";
    const html = renderToStaticMarkup(<TokenLabel address={ADDR} symbol={lure} onClick={() => undefined} />);
    expect(html).toContain("plx-token-btn");
    expect(html).not.toContain("UrgentDT.com");
    expect(renderToStaticMarkup(<TokenLabel address={ADDR} symbol="GNO" />)).not.toContain("plx-token-btn");
  });
});

describe("PairLabel / ProbeBadge", () => {
  it("renders every asset of an N-token pool in address order", () => {
    const html = renderToStaticMarkup(<PairLabel assets={["0x1", "0x2", "0x3"]} symbols={["A", null, "C"]} />);
    expect(html.indexOf(">A<")).toBeLessThan(html.indexOf(">C<"));
    expect(html).toContain("plx-badge--unresolved");
    expect((html.match(/plx-pair__sep/g) ?? []).length).toBe(2);
  });

  it("maps family × probe × has_state to the four statuses", () => {
    expect(probeStatus("cl", true)).toBe("probed");
    expect(probeStatus("cl", false)).toBe("state_only");
    expect(probeStatus("reserves_only", false)).toBe("reserves_only");
    // A pool with no published state row is NOT a "state only" pool.
    expect(probeStatus("cl", false, false)).toBe("no_state");
    expect(probeStatus("reserves_only", false, false)).toBe("reserves_only");
    expect(renderToStaticMarkup(<ProbeBadge family="cl" probed={false} />)).toContain("state only");
    expect(renderToStaticMarkup(<ProbeBadge family="reserves_only" probed={false} />)).toContain("reserves only");
    expect(renderToStaticMarkup(<ProbeBadge family="cl" probed={false} hasState={false} />)).toContain("no state row");
  });
});

describe("renderPlxCell against the live column contract", () => {
  const cellFor = (datasetKey: keyof typeof DATASET_COLUMNS, column: string, row: Record<string, unknown>) => {
    const columns = [...DATASET_COLUMNS[datasetKey]] as string[];
    const policy = resolveColumnPolicy(datasetKey, columns);
    const values = columns.map((name) => row[name] ?? null);
    const ctx: CellContext = { columnIndex: new Map(columns.map((name, index) => [name, index])) };
    const node = renderPlxCell(policy.kinds[column], column, values[columns.indexOf(column)], values, ctx);
    return node === undefined ? "" : renderToStaticMarkup(<>{node}</>);
  };

  it("says 'no state row' instead of a bare No when a CL pool has no state row", () => {
    expect(cellFor("pool_directory", "is_live", { pool_family: "cl", has_state: 0, is_live: 0 })).toContain("no state row");
    // A published state row with zero liquidity is a real "No".
    const zero = cellFor("pool_directory", "is_live", { pool_family: "cl", has_state: 1, is_live: 0 });
    expect(zero).toContain("No");
    expect(zero).not.toContain("no state row");
    // Balancer pools have no state row by design — never the confusing label.
    expect(cellFor("pool_directory", "is_live", { pool_family: "reserves_only", has_state: 0, is_live: 0 }))
      .not.toContain("no state row");
  });

  it("renders price_of_token_in_counter as an adjusted price, with no raw marker", () => {
    const html = cellFor("token_pools", "price_of_token_in_counter", { price_of_token_in_counter: 1.0007 });
    expect(html).toContain("1.0007");
    expect(html).not.toContain("plx-price--raw");
    expect(html).not.toContain("<sup>");
  });

  it("shows the raw price (marked) when decimals are unknown, and the adjusted twin when they are not", () => {
    // The VISIBLE price column is price_raw — it always has a value. Hiding it
    // behind price_adjusted rendered a dash for every unresolved-decimals pool.
    const policy = resolveColumnPolicy("pool_directory", [...DATASET_COLUMNS.pool_directory]);
    expect(policy.hidden).toContain("price_adjusted");
    expect(policy.hidden).not.toContain("price_raw");

    const unresolved = cellFor("pool_directory", "price_raw", {
      price_raw: 1e12, price_adjusted: null, token0_decimals: null, token1_decimals: 18,
    });
    expect(unresolved).toContain("plx-price--raw");
    expect(unresolved).toContain("<sup>raw</sup>");
    expect(unresolved).toContain("1.000e+12");

    const resolved = cellFor("pool_directory", "price_raw", {
      price_raw: 1e12, price_adjusted: 1.0007, token0_decimals: 6, token1_decimals: 18,
    });
    expect(resolved).toContain("1.0007");
    expect(resolved).not.toContain("plx-price--raw");
  });
});
