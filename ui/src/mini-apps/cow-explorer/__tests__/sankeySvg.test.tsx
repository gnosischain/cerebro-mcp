// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { createRoot } from "react-dom/client";
import { act } from "react";
import { SankeySvg } from "../../shared/svg-flow/SankeySvg";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function render(node: React.ReactElement): HTMLElement {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => root.render(node));
  return host;
}

describe("SankeySvg", () => {
  const links = [
    { source: "A/B", target: "solver1", value: 655 },
    { source: "A/B", target: "solver2", value: 261 },
    { source: "C/D", target: "solver1", value: 100 },
  ];

  it("renders ribbons as CLOSED filled paths (cover the bar, not just the top)", () => {
    const host = render(<SankeySvg links={links} />);
    const paths = [...host.querySelectorAll("path.sfl__ribbon")];
    expect(paths.length).toBe(3);
    // Filled area ribbons close with Z; a stroked line would not.
    for (const path of paths) {
      expect(path.getAttribute("d")).toMatch(/Z\s*$/);
      // No stroke-width attr (fill-based, not stroke-based).
      expect(path.getAttribute("stroke-width")).toBeNull();
    }
    // Node bars are drawn.
    expect(host.querySelectorAll("rect").length).toBeGreaterThan(0);
  });

  it("returns null when there are no links", () => {
    const host = render(<SankeySvg links={[]} />);
    expect(host.querySelector("svg")).toBeNull();
  });

  it("labels executor nodes via nodeLabel and calls onNodeClick", () => {
    let clicked = "";
    const host = render(
      <SankeySvg
        links={links}
        nodeLabel={(id, side) => (side === "right" ? id.toUpperCase() : id)}
        onNodeClick={(id) => { clicked = id; }}
      />,
    );
    const labels = [...host.querySelectorAll("text.sfl__label")].map((t) => t.textContent);
    expect(labels).toContain("SOLVER1");
    const clickable = host.querySelector("g.sfl__node--clickable");
    act(() => clickable?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(clicked).not.toBe("");
  });
});
