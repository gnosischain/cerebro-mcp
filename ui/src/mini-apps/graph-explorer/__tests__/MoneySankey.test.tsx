import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { MoneySankey } from "../canvas/MoneySankey";

const SEED = "0x1111000000000000000000000000000000000001";
const A = "0x2222000000000000000000000000000000000002";
const B = "0x3333000000000000000000000000000000000003";
const TOKEN = "0xaaaa000000000000000000000000000000000001";

describe("MoneySankey", () => {
  it("renders the forensic qualifier, accessible ribbons, and valueless expansion connector", () => {
    const html = renderToStaticMarkup(
      <MoneySankey
        nodes={[
          [SEED, "Seed", "", "", 0, 0, 500, "", "", []],
          [A, "Intermediary", "", "", 1, 500, 300, "", "", []],
          [B, "Recipient", "", "", 2, 300, 0, "", "", []],
        ].map((row) => ({
          id: String(row[0]),
          label: String(row[1]),
          sector: String(row[2]),
          project: String(row[3]),
          hopRank: Number(row[4]),
          inUsd: Number(row[5]),
          outUsd: Number(row[6]),
          firstSeen: String(row[7]),
          lastSeen: String(row[8]),
          flags: row[9] as string[],
        }))}
        edges={[
          {
            id: "e1",
            source: SEED,
            target: A,
            edgeClass: "transfer",
            tokenAddress: TOKEN,
            symbol: "GNO",
            amount: 2,
            amountUsd: 500,
            transferCount: 1,
            firstSeen: "",
            lastSeen: "",
            unknownUsdRows: 0,
          },
          {
            id: "e2",
            source: A,
            target: B,
            edgeClass: "transfer",
            tokenAddress: TOKEN,
            symbol: "GNO",
            amount: 1,
            amountUsd: 300,
            transferCount: 1,
            firstSeen: "",
            lastSeen: "",
            unknownUsdRows: 0,
          },
        ]}
        seeds={[SEED]}
        selectedNodeId=""
        selectedEdgeId=""
        onSelectNode={vi.fn()}
        onSelectEdge={vi.fn()}
        onClearSelection={vi.fn()}
      />,
    );

    expect(html).toContain("Aggregated transfer adjacency — not transaction-matched custody");
    expect(html).toContain("not “the same funds continued.”");
    expect(html).toContain("analyst_expansion");
    expect(html).toContain("no custody continuity asserted");
    expect(html.match(/role="button"/g)?.length).toBeGreaterThanOrEqual(5);
    expect(html).toContain("tabindex=\"0\"");
  });
});
