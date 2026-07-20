// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { TxGraphLegend, buildTxLegendTokens } from "../canvas/TxGraphLegend";
import { colorForTxToken } from "../canvas/txVisualEncoding";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("Transaction graph legend", () => {
  it("deduplicates tokens and uses the exact SVG color encoding", () => {
    const tokens = buildTxLegendTokens([
      { id: "a", source: "0xa", target: "0xb", tokenAddress: "0xToken", symbol: "TOK" },
      { id: "b", source: "0xb", target: "0xc", tokenAddress: "0xtoken", symbol: "TOK" },
      { id: "c", source: "0xc", target: "0xd", tokenAddress: null, symbol: null },
    ]);
    expect(tokens).toHaveLength(2);
    expect(tokens.find((token) => token.symbol === "TOK")).toMatchObject({
      legCount: 2,
      color: colorForTxToken("0xtoken"),
    });
    expect(tokens.some((token) => token.tokenAddress == null)).toBe(true);
  });

  it("discloses direction, visible node roles, unknown tokens, and compact overflow", async () => {
    const host = document.createElement("div");
    const root = createRoot(host);
    const legs = Array.from({ length: 8 }, (_, index) => ({
      id: `leg-${index}`,
      source: index % 2 ? "0xseed" : "0xparticipant",
      target: index % 2 ? "0xparticipant" : "0xburn",
      tokenAddress: index === 7 ? null : `0xtoken${index}`,
      symbol: index === 7 ? null : `T${index}`,
    }));
    await act(async () => {
      root.render(
        <TxGraphLegend
          legs={legs}
          nodes={[
            { id: "0xseed", role: "seed" },
            { id: "0xparticipant", role: "address" },
            { id: "0xtoken", role: "token" },
            { id: "0xburn", role: "burn" },
          ]}
          decodedLogCount={8}
        />,
      );
    });
    expect(host.textContent).toContain("8 paths/ 8 decoded receipt logs");
    expect(host.textContent).toContain("transfer direction");
    expect(host.textContent).toContain("Participant");
    expect(host.textContent).toContain("Seed");
    expect(host.textContent).toContain("Token contract");
    expect(host.textContent).toContain("Burn / structural terminal");
    expect(host.textContent).toContain("All 8 tokens");
    expect(host.textContent).not.toContain("Unknown token");
    await act(async () =>
      [...host.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "All 8 tokens")
        ?.click(),
    );
    expect(host.textContent).toContain("Unknown token");
    await act(async () => root.unmount());
  });
});

