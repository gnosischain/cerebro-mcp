// Renders the REAL timeline option through ECharts and measures where each arc
// actually lands, instead of asserting on the option object.
//
// The bug this exists for: every citation arc bowed DOWNWARD, `inbound` is a
// count so the y axis floors at 0, and most GIPs sit exactly on it — so an arc
// between two of them apexed below the grid and a cartesian series clipped it
// away. Reported as "there are edges that cross the 0 level and we never see
// them".
//
// Three earlier guards all passed while this was broken, which is why this one
// measures pixels:
//   * `gipGraph.test.ts` asserted the two curveness values had OPPOSITE SIGNS.
//     True, and irrelevant — `coords` is [src, dst] and `backward` is defined by
//     that same ordering, so both flips cancelled and both families bowed the
//     same way. The assertion described an intent, not the render.
//   * nothing asserted the arcs were inside the grid.
//   * nothing asserted `clip`.
//
// ECharts' SSR renderer gives real path data with no browser, so the geometry is
// checkable in a unit test: the quadratic's apex is at t=0.5, which for
// `M x1 y1 Q cx cy x2 y2` is 0.25*y1 + 0.5*cy + 0.25*y2.

import * as echarts from "echarts";
import { describe, expect, it } from "vitest";

import { gipTimelineOption, type GipEdge, type GipNode } from "../model/chartOptions";

const WIDTH = 600;
const HEIGHT = 400;
/** Must track `grid` in gipTimelineOption. Asserted below so it cannot drift. */
const GRID = { top: 28, bottom: 44 };

function node(gip: number, firstSeen: string): GipNode {
  return {
    gip, label: `GIP-${gip}`, stage: "voted", posts: 10, participants: 4,
    views: 100, votes: 5, quorumStatus: "met", author: "0xaa",
    proposalState: "closed", firstSeen, lastActivity: "2026-01-01 00:00:00",
    topicId: gip, proposalId: `0x${gip}`,
  };
}

function edge(src: number, dst: number): GipEdge {
  return {
    src, dst, weight: 1, topics: 1,
    firstMention: "2025-01-01 00:00:00", lastMention: "2025-06-01 00:00:00",
  };
}

interface Arc {
  startY: number;
  endY: number;
  apexY: number;
}

function renderArcs(nodes: GipNode[], edges: GipEdge[]): Arc[] {
  const chart = echarts.init(null, null, {
    renderer: "svg", ssr: true, width: WIDTH, height: HEIGHT,
  });
  chart.setOption({ ...gipTimelineOption(nodes, edges), animation: false });
  const svg = chart.renderToSVGString();
  chart.dispose();
  return [
    ...svg.matchAll(/d="M([-\d.]+) ([-\d.]+)Q([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"/g),
  ].map((m) => {
    const [, , y1, , cy, , y2] = m.map(Number);
    return { startY: y1, endY: y2, apexY: 0.25 * y1 + 0.5 * cy + 0.25 * y2 };
  });
}

describe("timeline citation arcs stay on screen", () => {
  // The reported case, built to the real geometry rather than to intuition.
  //
  // An arc can never have BOTH endpoints at inbound=0: being the destination of
  // an edge is what inbound counts, so a cited node is at 1 at minimum. What the
  // screenshot shows is the 0/1 band — the floor — against an axis whose max is
  // set by a handful of hubs (12 in production). A two-node fixture instead
  // collapses the axis to [0,1] and puts those nodes at the TOP, which is the
  // opposite of the case under test.
  //
  // So: one low pair (10 -> 20, decades apart on x, y=0 and y=1) plus a hub that
  // pushes the axis max to 12 and holds the low pair down at the floor.
  const HUB = 99;
  const CITERS = [31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42];
  const FLOOR_NODES = [
    node(10, "2022-01-01 00:00:00"),
    node(20, "2025-01-01 00:00:00"),
    node(HUB, "2023-06-01 00:00:00"),
    ...CITERS.map((g, i) => node(g, `2023-0${(i % 9) + 1}-01 00:00:00`)),
  ];
  const HUB_EDGES = CITERS.map((g) => edge(g, HUB));
  const BOTH_WAYS = [edge(20, 10), edge(10, 20), ...HUB_EDGES];

  it("keeps the grid constants in sync with the option", () => {
    const grid = gipTimelineOption(FLOOR_NODES, BOTH_WAYS).grid as Record<string, number>;
    expect(grid.top).toBe(GRID.top);
    expect(grid.bottom).toBe(GRID.bottom);
  });

  /** The wide, near-horizontal arcs across the floor band — the ones at risk.
   * The hub's own arcs span y=356 to y=28 and are steep by construction, so
   * they are not what this measures. */
  const floorBand = (arcs: Arc[]) =>
    arcs.filter((a) => a.startY > 300 && a.endY > 300);

  it("bows an arc across the floor band UP, inside the plot", () => {
    // y grows downward in screen space, so the floor is the LARGEST y and
    // "inside the plot" means a SMALLER number.
    const floorY = HEIGHT - GRID.bottom;
    const arcs = floorBand(renderArcs(FLOOR_NODES, BOTH_WAYS));
    expect(arcs.length).toBe(2);
    for (const arc of arcs) {
      // Measured: y=328.7 for the pair, apex 226.7 and 287.9 after the fix.
      // Before it, the same arcs apexed BELOW floorY=356 — drawn outside the
      // grid and clipped to nothing, which is what made them invisible.
      expect(arc.apexY).toBeLessThan(arc.startY);
      expect(arc.apexY).toBeLessThan(floorY);
      // And far enough in to read as an arc rather than a flat line.
      expect(arc.startY - arc.apexY).toBeGreaterThan(10);
    }
  });

  it("bows up regardless of which endpoint is earlier in time", () => {
    // The sign has to come from the chord. A forward citation (older GIP citing
    // a newer one) has the opposite chord direction, and taking the sign from
    // the citation direction instead is exactly what cancelled out before.
    //
    // HUB_EDGES stay in both renders: without them the axis collapses to [0,1]
    // and the pair moves to the TOP of the grid, which is not the case at issue.
    for (const pair of [edge(20, 10), edge(10, 20)]) {
      const arcs = floorBand(renderArcs(FLOOR_NODES, [pair, ...HUB_EDGES]));
      expect(arcs.length).toBe(1);
      expect(arcs[0].apexY).toBeLessThan(arcs[0].startY);
    }
  });

  it("does not clip the arc series at all", () => {
    // An arc between two nodes near the axis MAX would leave the grid upward and
    // vanish the same way. An edge that silently disappears is the failure being
    // fixed, so nothing is clipped in either direction.
    const series = gipTimelineOption(FLOOR_NODES, BOTH_WAYS)
      .series as unknown as Array<Record<string, unknown>>;
    expect(series[0].type).toBe("lines");
    expect(series[0].clip).toBe(false);
  });
});
