// @vitest-environment jsdom
//
// The chain-state token overlay. Every test here is about PROVENANCE, not
// formatting: the state indexer has metadata for 68 of the ~3,400 tokens these
// pools hold, so most labels now come from a live RPC read — and a live read
// has no publication behind it. The rules these tests pin:
//
//   indexer > overlay > short address, and the indexer is NEVER overwritten;
//   anything the overlay supplied carries a marker and says which block;
//   a token neither knows stays a short address;
//   the loader fires once per scope, not once per render.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { metadataWarning } from "../PoolsExplorerApp";
import { PairLabel } from "../components/PairLabel";
import { PriceCell } from "../components/PriceCell";
import { TokenLabel } from "../components/TokenLabel";
import { TokenLabelCoverage } from "../components/TokenLabelCoverage";
import { renderPlxCell, type CellContext } from "../components/cells";
import {
  CRC, CRC2, DEV_OVERLAY_BLOCK, SDAI, TOKEN_OVERLAY, USDCE, WETH, sectionPayload,
} from "../devFixture";
import { resolveColumnPolicy } from "../model/columns";
import { fmtAmountWithOverlay, fmtPriceWithOverlay } from "../model/format";
import {
  collectVisibleTokens, hashAddresses, overlayCoverage, overlayScopeKey, overlayWarningCodes,
  resolveDecimals, resolveTokenLabel, type TokenOverlay,
} from "../model/tokenOverlay";
import { useTokenOverlayLoader } from "../state/useTokenOverlayLoader";
import { DATASET_COLUMNS } from "../types";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OVERLAY: TokenOverlay = TOKEN_OVERLAY as TokenOverlay;
/** A token no source knows: absent from the overlay, unknown to the indexer. */
const UNKNOWN = CRC;

const roots: Array<{ root: Root; host: HTMLElement }> = [];
afterEach(() => {
  for (const { root, host } of roots.splice(0)) {
    act(() => root.unmount());
    host.remove();
  }
});

function render(node: React.ReactElement): HTMLElement {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => root.render(node));
  roots.push({ root, host });
  return host;
}

/** Render one cell of a real dataset through the live column policy. */
function cellFor(
  datasetKey: keyof typeof DATASET_COLUMNS,
  column: string,
  row: Record<string, unknown>,
  ctxExtra: Partial<CellContext> = {},
): string {
  const columns = [...DATASET_COLUMNS[datasetKey]] as string[];
  const policy = resolveColumnPolicy(datasetKey, columns);
  const values = columns.map((name) => (name in row ? row[name] : null));
  const ctx: CellContext = {
    columnIndex: new Map(columns.map((name, index) => [name, index])),
    ...ctxExtra,
  };
  const node = renderPlxCell(policy.kinds[column], column, values[columns.indexOf(column)], values, ctx);
  return node === undefined ? "" : renderToStaticMarkup(<>{node}</>);
}

// ---------------------------------------------------------------------------
// 1. Three-way precedence
// ---------------------------------------------------------------------------

describe("label precedence: indexer > overlay > address", () => {
  it("resolves each source in order and reports which one answered", () => {
    // The indexer knows USDCE as "USDC.e"; the chain returns "USDCe".
    expect(resolveTokenLabel(USDCE, "USDC.e", OVERLAY))
      .toEqual({ text: "USDC.e", source: "indexer", blockNumber: null });
    // CRC2: the indexer has nothing, so the overlay answers — and says where.
    expect(resolveTokenLabel(CRC2, null, OVERLAY))
      .toEqual({ text: "CRC", source: "overlay", blockNumber: DEV_OVERLAY_BLOCK });
    // Neither: the address IS the identity, so it stays the label.
    expect(resolveTokenLabel(UNKNOWN, null, OVERLAY).source).toBe("none");
    expect(resolveTokenLabel(UNKNOWN, null, OVERLAY).text).toContain("…");
  });

  it("is case-insensitive on the address and never resolves a blank one", () => {
    expect(resolveTokenLabel(CRC2.toUpperCase(), null, OVERLAY).source).toBe("overlay");
    expect(resolveTokenLabel("", null, OVERLAY).source).toBe("none");
  });

  it("renders the three states through TokenLabel", () => {
    const indexer = renderToStaticMarkup(<TokenLabel address={USDCE} symbol="USDC.e" overlay={OVERLAY} />);
    expect(indexer).toContain(">USDC.e<");
    expect(indexer).not.toContain("USDCe");
    expect(indexer).not.toContain("plx-chainmark");

    const chain = renderToStaticMarkup(<TokenLabel address={CRC2} symbol={null} overlay={OVERLAY} />);
    expect(chain).toContain(">CRC<");
    expect(chain).toContain("plx-chainmark");

    const address = renderToStaticMarkup(<TokenLabel address={UNKNOWN} symbol={null} overlay={OVERLAY} />);
    expect(address).toContain("0x3ab2…6a7b");
    expect(address).not.toContain("plx-chainmark");
  });

  it("falls back to the overlay symbol only when the indexer symbol is unusable", () => {
    // An all-control-character "symbol" sanitizes to "" — that is not a name,
    // so the overlay may answer.
    const lure = renderToStaticMarkup(<TokenLabel address={CRC2} symbol={"​‮"} overlay={OVERLAY} />);
    expect(lure).toContain(">CRC<");
    expect(lure).toContain("plx-chainmark");
  });
});

// ---------------------------------------------------------------------------
// 2. The overlay never overwrites an indexer value
// ---------------------------------------------------------------------------

describe("the indexer always wins", () => {
  it("keeps the indexer symbol even when the overlay carries a different one", () => {
    expect(OVERLAY[USDCE].symbol).toBe("USDCe");
    expect(resolveTokenLabel(USDCE, "USDC.e", OVERLAY).text).toBe("USDC.e");
    const html = renderToStaticMarkup(<TokenLabel address={USDCE} symbol="USDC.e" overlay={OVERLAY} />);
    expect(html).toContain("USDC.e");
    expect(html).not.toContain("plx-chainmark");
  });

  it("keeps the indexer decimals even when the overlay carries different ones", () => {
    const conflicting: TokenOverlay = {
      [USDCE]: { ...OVERLAY[USDCE], decimals: 18, symbol: "USDCe" },
    };
    expect(resolveDecimals(6, USDCE, conflicting))
      .toEqual({ decimals: 6, source: "indexer", blockNumber: null });
    // 0 is a REAL decimals value, not a missing one.
    expect(resolveDecimals(0, USDCE, conflicting).source).toBe("indexer");
    expect(resolveDecimals(0, USDCE, conflicting).decimals).toBe(0);
  });

  it("does not re-scale a price the server already adjusted", () => {
    const conflicting: TokenOverlay = { [USDCE]: { ...OVERLAY[USDCE], decimals: 18 } };
    const price = fmtPriceWithOverlay(1e12, 1.0007, 6, 18, 18, null);
    expect(price).toMatchObject({ text: "1.0007", raw: false });
    expect(price.chain).toBeFalsy();
    const html = cellFor("pool_directory", "price_raw", {
      token0: USDCE, token1: SDAI, token0_decimals: 6, token1_decimals: 18,
      price_raw: 1e12, price_adjusted: 1.0007,
    }, { overlay: conflicting });
    expect(html).toContain("1.0007");
    expect(html).not.toContain("plx-price--chain");
    expect(html).not.toContain("chain</sup>");
  });

  it("does not re-scale an amount the server already expressed in units", () => {
    const conflicting: TokenOverlay = { [WETH]: { ...OVERLAY[CRC2], decimals: 6, symbol: "WETH" } };
    const amount = fmtAmountWithOverlay("412500000000000000000", 18, 412.5, 6);
    expect(amount).toEqual({ text: "412.5", rawUnits: false });
    const html = cellFor("pool_directory", "reserve0_units", {
      token0: WETH, token0_decimals: 18,
      reserve0_raw: "412500000000000000000", reserve0_units: 412.5,
    }, { overlay: conflicting });
    expect(html).toContain("412.5");
    expect(html).not.toContain("plx-amount--chain");
  });

  it("never opens the overlay path for a token whose decimals the indexer knows", () => {
    // Indexer decimals known, so the overlay is not consulted at all — even
    // though it holds a (different) value for this token. The cell renders the
    // dash it always did rather than a figure scaled by the wrong exponent.
    const conflicting: TokenOverlay = { [WETH]: { ...OVERLAY[CRC2], decimals: 6, symbol: "WETH" } };
    expect(resolveDecimals(18, WETH, conflicting).source).toBe("indexer");
    expect(cellFor("pool_directory", "reserve0_units", {
      token0: WETH, token0_decimals: 18,
      reserve0_raw: "412500000000000000000", reserve0_units: null,
    }, { overlay: conflicting })).toBe("");
  });

  it("marks a figure as chain-adjusted only when the overlay actually supplied a decimals", () => {
    // Both decimals known but no server-computed twin: still not "from chain
    // state" — marking it would be a lie in the other direction.
    const price = fmtPriceWithOverlay(1e12, null, 6, 18, 6, 18);
    expect(price.raw).toBe(true);
    expect(price.chain).toBeFalsy();
  });
});

// ---------------------------------------------------------------------------
// 3. An absent token still renders a short address
// ---------------------------------------------------------------------------

describe("a token nothing can read", () => {
  it("is absent from the overlay rather than present with a null symbol", () => {
    expect(UNKNOWN in OVERLAY).toBe(false);
  });

  it("renders its short address and the existing unresolved badge, unchanged", () => {
    const html = renderToStaticMarkup(
      <TokenLabel address={UNKNOWN} symbol={null} resolved={false} overlay={OVERLAY} />,
    );
    expect(html).toContain("0x3ab2…6a7b");
    expect(html).toContain("plx-badge--unresolved");
    expect(html).toContain("ma-token__label--raw");
    expect(html).not.toContain("plx-chainmark");
  });

  it("keeps raw units for its amounts and prices", () => {
    // Nothing can scale this: the cell falls back to the table's own dash,
    // exactly as before the overlay existed. It is NOT invented as a number.
    expect(cellFor("pool_directory", "reserve0_units", {
      token0: UNKNOWN, token0_decimals: null, reserve0_raw: "8100000000000000000000", reserve0_units: null,
    }, { overlay: OVERLAY })).toBe("");
    expect(fmtAmountWithOverlay("8100000000000000000000", null, null, null))
      .toMatchObject({ rawUnits: true });

    const price = cellFor("pool_directory", "price_raw", {
      token0: UNKNOWN, token1: SDAI, token0_decimals: null, token1_decimals: 18,
      price_raw: 1e12, price_adjusted: null,
    }, { overlay: OVERLAY });
    expect(price).toContain("plx-price--raw");
    expect(price).toContain("<sup>raw</sup>");
  });

  it("renders a mixed pair: indexer name, chain name, bare address", () => {
    const html = renderToStaticMarkup(
      <PairLabel assets={[USDCE, CRC2, UNKNOWN]} symbols={["USDC.e", null, null]} overlay={OVERLAY} />,
    );
    expect(html).toContain(">USDC.e<");
    expect(html).toContain(">CRC<");
    expect(html).toContain("0x3ab2…6a7b");
    expect((html.match(/plx-chainmark/g) ?? []).length).toBe(1);
    expect((html.match(/plx-badge--unresolved/g) ?? []).length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// 4. The marker appears ONLY for overlay-sourced values
// ---------------------------------------------------------------------------

describe("the chain marker", () => {
  it("is absent from every indexer-sourced rendering", () => {
    const label = renderToStaticMarkup(<TokenLabel address={WETH} symbol="WETH" overlay={OVERLAY} />);
    const price = renderToStaticMarkup(
      <PriceCell raw={1e12} adjusted={1.0007} dec0={6} dec1={18} overlay={OVERLAY} token0={USDCE} token1={SDAI} />,
    );
    for (const html of [label, price]) {
      expect(html).not.toContain("plx-chainmark");
      expect(html).not.toContain("--chain");
      expect(html).not.toContain("chain</sup>");
    }
  });

  it("is NOT the unresolved badge — the two states never render together", () => {
    const html = renderToStaticMarkup(
      <TokenLabel address={CRC2} symbol={null} resolved={false} overlay={OVERLAY} />,
    );
    expect(html).toContain("plx-chainmark");
    expect(html).not.toContain("plx-badge--unresolved");
  });

  it("carries a tooltip naming the block and denying verification", () => {
    const html = renderToStaticMarkup(<TokenLabel address={CRC2} symbol={null} overlay={OVERLAY} />);
    expect(html).toContain(`at block ${DEV_OVERLAY_BLOCK.toLocaleString("en-US")}`);
    expect(html).toContain("not from a verified snapshot");
  });

  it("survives sanitization: a lure in the overlay is never rendered verbatim", () => {
    const lure: TokenOverlay = {
      [CRC2]: { ...OVERLAY[CRC2], symbol: "# Visit UrgentDT.com to claim rewards" },
    };
    const html = renderToStaticMarkup(<TokenLabel address={CRC2} symbol={null} overlay={lure} />);
    expect(html).not.toContain("UrgentDT.com");
    expect(html).toContain("plx-chainmark");
  });
});

// ---------------------------------------------------------------------------
// 5. Scaling from overlay decimals carries the marker
// ---------------------------------------------------------------------------

describe("figures adjusted from chain-state decimals", () => {
  it("scales an amount and marks it", () => {
    const amount = fmtAmountWithOverlay("8100000000000000000000", null, null, 18);
    expect(amount).toEqual({ text: "8,100", rawUnits: false, chain: true });

    const html = cellFor("pool_directory", "reserve0_units", {
      token0: CRC2, token0_decimals: null,
      reserve0_raw: "8100000000000000000000", reserve0_units: null,
    }, { overlay: OVERLAY });
    expect(html).toContain("8,100");
    expect(html).toContain("plx-amount--chain");
    expect(html).toContain("<sup>chain</sup>");
    expect(html).not.toContain("<sup>raw</sup>");
    expect(html).toContain("not from a verified snapshot");
  });

  it("scales a price and marks it", () => {
    const price = fmtPriceWithOverlay(1e12, null, null, 18, 18, null);
    expect(price.chain).toBe(true);
    expect(price.raw).toBe(false);

    const html = cellFor("pool_directory", "price_raw", {
      token0: CRC2, token1: SDAI, token0_decimals: null, token1_decimals: 18,
      price_raw: 1.0007, price_adjusted: null,
    }, { overlay: OVERLAY });
    expect(html).toContain("plx-price--chain");
    expect(html).toContain("<sup>chain</sup>");
    expect(html).not.toContain("plx-price--raw");
  });

  it("resolves a token_pools amount through the table's entity address", () => {
    // token_pools carries `token_decimals` but not the token's own address.
    const row = { reserve_token_raw: "8100000000000000000000", token_decimals: null, reserve_token_units: null };
    const withEntity = cellFor("token_pools", "reserve_token_units", row, {
      overlay: OVERLAY, entityAddress: CRC2,
    });
    expect(withEntity).toContain("plx-amount--chain");
    // Without it there is nothing to look up, so the cell stays a dash — the
    // decimals are never guessed from a neighbouring token.
    expect(cellFor("token_pools", "reserve_token_units", row, { overlay: OVERLAY })).toBe("");
  });

  it("refuses an implausible decimals rather than producing a plausible wrong number", () => {
    const absurd: TokenOverlay = { [CRC2]: { ...OVERLAY[CRC2], decimals: 400 } };
    expect(resolveDecimals(null, CRC2, absurd)).toEqual({ decimals: null, source: "none", blockNumber: null });
    const negative: TokenOverlay = { [CRC2]: { ...OVERLAY[CRC2], decimals: -1 } };
    expect(resolveDecimals(null, CRC2, negative).source).toBe("none");
  });
});

// ---------------------------------------------------------------------------
// 6. The two new warning codes
// ---------------------------------------------------------------------------

describe("token overlay warnings", () => {
  const coverage = { visible: 9, fromIndexer: 6, fromChain: 2, unlabelled: 1 };

  it("derives the codes from the stats the same way the server does", () => {
    expect(overlayWarningCodes({ requested: 9, resolved: 8 })).toEqual([]);
    expect(overlayWarningCodes({ truncated: true })).toEqual(["token_overlay_pending"]);
    expect(overlayWarningCodes({ error: "connection refused" })).toEqual(["token_rpc_unavailable"]);
    // An unreachable chain outranks "more pending" — nothing was read at all.
    expect(overlayWarningCodes({ error: "boom", truncated: true })).toEqual(["token_rpc_unavailable"]);
    expect(overlayWarningCodes(undefined)).toEqual([]);
  });

  it("token_rpc_unavailable: says labels could not be read and offers a retry", () => {
    const retries: number[] = [];
    const host = render(
      <TokenLabelCoverage
        coverage={coverage}
        stats={{ requested: 9, resolved: 0, error: "connection refused" }}
        onRetry={() => retries.push(1)}
      />,
    );
    const text = host.textContent ?? "";
    expect(text).toContain("chain state could not be read");
    expect(text).toContain("connection refused");
    expect(text).toContain("addresses");
    const retry = [...host.querySelectorAll("button")].find((button) => button.textContent === "Retry")!;
    act(() => retry.click());
    expect(retries).toEqual([1]);
  });

  it("token_overlay_pending: says more are pending and offers a retry", () => {
    const host = render(
      <TokenLabelCoverage coverage={coverage} stats={{ requested: 900, resolved: 600, truncated: true }} />,
    );
    expect(host.textContent).toContain("more tokens pending");
    expect(host.textContent).not.toContain("could not be read");
  });

  it("accepts codes carried on the tool payload as well as derived ones", () => {
    const host = render(<TokenLabelCoverage coverage={coverage} warnings={["token_rpc_unavailable"]} />);
    expect(host.textContent).toContain("chain state could not be read");
  });

  it("stays silent when the read was clean, and disappears entirely with no tokens in view", () => {
    const clean = render(<TokenLabelCoverage coverage={coverage} stats={{ requested: 9, resolved: 3 }} />);
    expect(clean.textContent).not.toContain("could not be read");
    expect(clean.textContent).not.toContain("pending");
    expect(clean.querySelector("button")).toBeNull();
    expect(clean.textContent).toContain("6 indexed");
    expect(clean.textContent).toContain("2 chain state");
    expect(clean.textContent).toContain("1 address-only");

    const none = render(
      <TokenLabelCoverage coverage={{ visible: 0, fromIndexer: 0, fromChain: 0, unlabelled: 0 }} />,
    );
    expect(none.textContent).toBe("");
  });
});

// ---------------------------------------------------------------------------
// 7. Coverage accounting over the real fixture
// ---------------------------------------------------------------------------

describe("visible-token accounting", () => {
  const datasetsOf = (section: "pools" | "tokens") => {
    const payload = sectionPayload(section);
    return Object.values(payload.datasets!).map((descriptor) => ({
      columns: descriptor.columns.map((column) => column.name),
      rows: descriptor.preview_rows,
    }));
  };

  it("collects the token columns of the pool directory and no pool addresses", () => {
    const visible = collectVisibleTokens(datasetsOf("pools"));
    expect(visible.addresses).toContain(USDCE);
    expect(visible.addresses).toContain(CRC2);
    expect(visible.addresses).toContain(UNKNOWN);
    // A pool address is not a token address.
    expect(visible.addresses).not.toContain("0x0cf44132a7df09ba82d5c4010e73e151d31a42ae");
    expect(visible.indexerLabelled.has(USDCE)).toBe(true);
    expect(visible.indexerLabelled.has(CRC2)).toBe(false);
  });

  it("counts indexer labels, chain labels and address-only tokens separately", () => {
    const visible = collectVisibleTokens(datasetsOf("tokens"));
    const coverage = overlayCoverage(visible, OVERLAY);
    expect(coverage.visible).toBe(visible.addresses.length);
    expect(coverage.fromIndexer + coverage.fromChain + coverage.unlabelled).toBe(coverage.visible);
    // CRC2 is the overlay's own contribution; CRC is readable by nobody.
    expect(coverage.fromChain).toBe(1);
    expect(coverage.unlabelled).toBe(1);
    // USDCE is in the overlay too, but the indexer names it — so it counts as
    // an indexer label, never as a chain one.
    expect(overlayCoverage({ addresses: [USDCE], indexerLabelled: new Set([USDCE]) }, OVERLAY))
      .toEqual({ visible: 1, fromIndexer: 1, fromChain: 0, unlabelled: 0 });
  });

  it("ignores datasets with no token column at all", () => {
    expect(collectVisibleTokens([
      { columns: ["bucket", "pools_live_cl"], rows: [["2026-09-16", 1237]] },
    ]).addresses).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 8. The auto-loader fires once per scope, not once per render
// ---------------------------------------------------------------------------

describe("useTokenOverlayLoader", () => {
  function Probe({ callTool, scopeKey, enabled = true }: {
    callTool: (name: string, args: Record<string, unknown>) => Promise<unknown>;
    scopeKey: string;
    enabled?: boolean;
  }) {
    const loader = useTokenOverlayLoader(callTool, "view-1", scopeKey, enabled);
    return <button type="button" onClick={loader.reload}>reload</button>;
  }

  const spyTool = () => {
    const calls: Array<Record<string, unknown>> = [];
    const callTool = vi.fn(async (_name: string, args: Record<string, unknown>) => {
      calls.push(args);
      return { type: "PATCH_VIEW_STATE", warnings: [] };
    });
    return { calls, callTool };
  };

  /** A root plus an async render that flushes the tool promise inside act(). */
  function mountProbe() {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    roots.push({ root, host });
    return {
      host,
      draw: async (node: React.ReactElement) => {
        await act(async () => {
          root.render(node);
        });
      },
    };
  }

  it("calls the tool once per scope even across many re-renders", async () => {
    const { calls, callTool } = spyTool();
    const { draw } = mountProbe();
    for (let i = 0; i < 6; i += 1) {
      await draw(<Probe callTool={callTool} scopeKey="scope-a" />);
    }
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({ view_id: "view-1", request_id: 0 });

    // A new scope (section switch / entity change / page append) DOES re-call.
    await draw(<Probe callTool={callTool} scopeKey="scope-b" />);
    await draw(<Probe callTool={callTool} scopeKey="scope-b" />);
    expect(calls).toHaveLength(2);
  });

  it("does not call while the section core has not landed, then calls once when it has", async () => {
    const { calls, callTool } = spyTool();
    const { draw } = mountProbe();
    await draw(<Probe callTool={callTool} scopeKey="scope-a" enabled={false} />);
    expect(calls).toHaveLength(0);
    await draw(<Probe callTool={callTool} scopeKey="scope-a" enabled />);
    await draw(<Probe callTool={callTool} scopeKey="scope-a" enabled />);
    expect(calls).toHaveLength(1);
  });

  it("never fires for an empty scope key (no tokens in view)", async () => {
    const { calls, callTool } = spyTool();
    const { draw } = mountProbe();
    await draw(<Probe callTool={callTool} scopeKey="" />);
    expect(calls).toHaveLength(0);
  });

  it("picks up a scope that changed WHILE a call was in flight", async () => {
    const { calls, callTool } = spyTool();
    const { draw } = mountProbe();
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    roots.push({ root, host });
    // Render both scopes without letting the first promise settle in between.
    act(() => {
      root.render(<Probe callTool={callTool} scopeKey="scope-a" />);
    });
    act(() => {
      root.render(<Probe callTool={callTool} scopeKey="scope-b" />);
    });
    await act(async () => {
      root.render(<Probe callTool={callTool} scopeKey="scope-b" />);
    });
    expect(calls).toHaveLength(2);
    await draw(<span />);
  });

  it("forces a refresh on an explicit retry, bypassing the dedupe", async () => {
    const { calls, callTool } = spyTool();
    const { host, draw } = mountProbe();
    await draw(<Probe callTool={callTool} scopeKey="scope-a" />);
    expect(calls).toHaveLength(1);
    await act(async () => {
      host.querySelector("button")!.click();
    });
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual({ view_id: "view-1", request_id: 0, force_refresh: true });
  });

  it("reports token_rpc_unavailable when the call itself fails, and never throws", async () => {
    const failing = vi.fn(async () => {
      throw new Error("host unreachable");
    });
    const seen: string[][] = [];
    function Capture() {
      const loader = useTokenOverlayLoader(failing, "view-1", "scope-a", true);
      seen.push(loader.warnings);
      return <span>{loader.error}</span>;
    }
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    roots.push({ root, host });
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await act(async () => {
      root.render(<Capture />);
    });
    expect(host.textContent).toContain("host unreachable");
    expect(seen[seen.length - 1]).toEqual(["token_rpc_unavailable"]);
    error.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// 9. The dedupe key is derived from what is VISIBLE, never from the answer
// ---------------------------------------------------------------------------

describe("overlayScopeKey", () => {
  it("is stable for the same scope and token set, and moves when either does", () => {
    const a = overlayScopeKey("pools:1", [USDCE, CRC2], 0);
    expect(overlayScopeKey("pools:1", [USDCE, CRC2], 0)).toBe(a);
    expect(overlayScopeKey("pools:2", [USDCE, CRC2], 0)).not.toBe(a);
    expect(overlayScopeKey("pools:1", [USDCE, CRC2, UNKNOWN], 0)).not.toBe(a);
    // A "Load more" changes nothing in the descriptor preview, so the page
    // epoch is the only signal that new rows are on screen.
    expect(overlayScopeKey("pools:1", [USDCE, CRC2], 1)).not.toBe(a);
  });

  it("hashes distinct sets distinctly and equal sets identically", () => {
    expect(hashAddresses([USDCE, CRC2])).toBe(hashAddresses([USDCE, CRC2]));
    expect(hashAddresses([USDCE, CRC2])).not.toBe(hashAddresses([CRC2, USDCE]));
    expect(hashAddresses([])).toBe(hashAddresses([]));
  });
});

// ---------------------------------------------------------------------------
// 10. The fixture mirrors the real payload
// ---------------------------------------------------------------------------

describe("devFixture token overlay", () => {
  it("ships both keys on every payload, covering all three cases", () => {
    const state = sectionPayload("pools").view_state!;
    expect(state.token_overlay).toBeDefined();
    expect(state.token_overlay_stats).toBeDefined();
    // Indexer-known, overlay-only, and known to neither.
    expect(state.token_overlay![USDCE].symbol).toBe("USDCe");
    expect(state.token_overlay![CRC2]).toMatchObject({ symbol: "CRC", decimals: 18, source: "rpc" });
    expect(state.token_overlay![UNKNOWN]).toBeUndefined();
    expect(state.token_overlay_stats).toMatchObject({ requested: 3, resolved: 2, unreadable: 1, source: "rpc" });
  });

  it("reads at a block AHEAD of the publication anchor — current state, not a snapshot", () => {
    const payload = sectionPayload("pools");
    const columns = payload.datasets!.pool_directory.columns.map((column) => column.name);
    const anchor = payload.datasets!.pool_directory.preview_rows[0][columns.indexOf("anchor_block")];
    expect(payload.view_state!.token_overlay_stats!.block_number).toBe(DEV_OVERLAY_BLOCK);
    expect(payload.view_state!.token_overlay_stats!.block_number!).toBeGreaterThan(Number(anchor));
  });
});

// ---------------------------------------------------------------------------
// The metadata banner vs the overlay
// ---------------------------------------------------------------------------

describe("metadataWarning", () => {
  const coverage = (fromIndexer: number, fromChain: number, unlabelled: number) => ({
    visible: fromIndexer + fromChain + unlabelled,
    fromIndexer,
    fromChain,
    unlabelled,
  });

  it("drops the banner once the chain read has labelled everything", () => {
    // The server raises metadata_unresolved from what the INDEXER holds, so it
    // still fires after the overlay filled the gap. Left alone it claims
    // "prices and amounts are in raw units" directly above an adjusted,
    // chain-marked price.
    expect(metadataWarning(coverage(0, 2, 0))).toBeNull();
    expect(metadataWarning(coverage(1, 1, 0))).toBeNull();
  });

  it("says how many are still unlabelled when the overlay only got some", () => {
    const copy = metadataWarning(coverage(1, 5, 3));
    expect(copy).toContain("3 tokens are still unlabelled");
    expect(copy).toContain("read from chain state");
  });

  it("uses the singular for exactly one", () => {
    expect(metadataWarning(coverage(1, 5, 1))).toContain("1 token is still unlabelled");
  });

  it("keeps the original blanket copy when the overlay resolved nothing", () => {
    // RPC down, or genuinely unreadable tokens: the raw-units statement is
    // still true and must stay.
    const copy = metadataWarning(coverage(0, 0, 4));
    expect(copy).toContain("raw units");
  });
});
