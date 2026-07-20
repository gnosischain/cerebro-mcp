import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { DetailsPanel } from "../DetailsPanel";

describe("DetailsPanel selection context", () => {
  it("puts an explicitly selected edge before seed-node and neighbour context", () => {
    const html = renderToStaticMarkup(
      <DetailsPanel
        nodes={[
          { id: "0xa", kind: "address", label: "A", profiles: ["p1"] },
          { id: "0xb", kind: "safe", label: "B", profiles: ["p1"] },
        ]}
        edges={[
          {
            id: "edge-2",
            source: "0xa",
            target: "0xb",
            profile: "p1",
            weight: 12,
            edge_count: 1,
            directed: true,
          },
        ]}
        selectedNodeId=""
        selectedEdgeId="edge-2"
        seedNodeId="0xa"
        nodeRoles={{}}
        catalog={[]}
        suggestions={[]}
        nodeEvidence={[]}
        edgeEvidence={[]}
        evidenceExpectation={{
          subjectKind: "edge",
          subjectId: "edge-2",
          requestId: 4,
        }}
        onApplyHop={vi.fn()}
        onSelectNode={vi.fn()}
      />,
    );

    const edge = html.indexOf('data-inspector-context="edge"');
    const node = html.indexOf('data-inspector-context="node"');
    const neighbours = html.indexOf("Neighbors");
    expect(edge).toBeGreaterThanOrEqual(0);
    expect(node).toBeGreaterThan(edge);
    expect(neighbours).toBeGreaterThan(node);
  });
});
