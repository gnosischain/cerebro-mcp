import { describe, expect, it } from "vitest";

import {
  GIP_STAGE_COLOR,
  drawableEdges,
  gipDegrees,
  gipGraphOption,
  gipTimelineOption,
  type GipEdge,
  type GipNode,
} from "../model/chartOptions";

function node(gip: number, over: Partial<GipNode> = {}): GipNode {
  return {
    gip, label: `GIP-${gip}`, stage: "voted",
    posts: 10, participants: 4, views: 100, votes: 5,
    quorumStatus: "met", author: "0xaa", proposalState: "closed",
    firstSeen: `2024-01-${String((gip % 27) + 1).padStart(2, "0")} 00:00:00`,
    lastActivity: "2026-01-01 00:00:00",
    topicId: 100 + gip, proposalId: `0x${gip}`,
    ...over,
  };
}

function edge(src: number, dst: number, weight = 1): GipEdge {
  return { src, dst, weight, topics: 1,
           firstMention: "2025-01-01 00:00:00", lastMention: "2025-06-01 00:00:00" };
}

type Series = { data: Array<Record<string, unknown>>; links: Array<Record<string, unknown>> };
/** The timeline is two series — arcs (`lines`) then nodes (`scatter`) — because
 * a `graph` series on a cartesian system emits no click events. These helpers
 * keep the assertions readable across that split. */
const nodeSeries = (o: ReturnType<typeof gipTimelineOption>) =>
  (o.series as unknown as Series[])[1];
const linkSeries = (o: ReturnType<typeof gipTimelineOption>) =>
  (o.series as unknown as Series[])[0];
/** The force view is still a single graph series. */
const series = (o: ReturnType<typeof gipGraphOption>) => (o.series as unknown as Series[])[0];

describe("drawableEdges", () => {
  it("drops an edge whose endpoint is not a node", () => {
    // ECharts silently INVENTS a node for an unknown link endpoint, which would
    // render as a real GIP that merely has no data. GIP numbers appear in post
    // bodies far more often than they exist as a topic or proposal.
    expect(drawableEdges([node(1), node(2)], [edge(1, 2), edge(1, 999)])).toHaveLength(1);
  });
});

describe("gipDegrees", () => {
  it("separates citations received from citations made", () => {
    const d = gipDegrees([edge(2, 1, 5), edge(3, 1, 2), edge(1, 9, 1)]);
    expect(d.get(1)).toEqual({ inbound: 2, outbound: 1, weight: 8 });
    expect(d.get(2)).toEqual({ inbound: 0, outbound: 1, weight: 5 });
  });
});

describe("gipTimelineOption", () => {
  it("emits nodes as a scatter series and arcs as a lines series", () => {
    // NOT a `graph` series: ECharts does not fire click events for a graph on a
    // cartesian coordinate system, which left every node inert. Scatter and
    // Lines both emit clicks natively, so the interaction needs no workaround.
    const o = gipTimelineOption([node(1), node(2)], [edge(1, 2)]);
    const types = (o.series as unknown as Array<{ type: string }>).map((x) => x.type);
    expect(types).toEqual(["lines", "scatter"]);
  });

  it("gives each arc real endpoint coordinates so it can be drawn and hit", () => {
    // edge(20, 10) is "GIP-20 cites GIP-10", so the arc runs FROM 20 TO 10 and
    // coords[0] is the citing node.
    const o = gipTimelineOption([node(10), node(20)], [edge(20, 10)]);
    const coords = linkSeries(o).data[0].coords as unknown[][];
    expect(coords).toHaveLength(2);
    expect(coords[0][0]).toBe("2024-01-21T00:00:00");
    expect(coords[1][0]).toBe("2024-01-11T00:00:00");
  });

  it("places nodes on a real time axis, not on GIP number", () => {
    // GIP numbers are only 89% monotone with date (17 inversions across 148
    // pairs), so the number is a label and the date is the chronology.
    const o = gipTimelineOption([node(1, { firstSeen: "2021-03-04 10:00:00" })], []);
    expect((o.xAxis as { type: string }).type).toBe("time");
    expect((nodeSeries(o).data[0].value as unknown[])[0]).toBe("2021-03-04T10:00:00");
  });

  it("puts influence on the y-axis, not lifecycle stage", () => {
    // Stage was tried on y first and failed: 121 of 149 GIPs are 'voted', so
    // the lanes collapsed into one line and every arc went flat. Stage keeps
    // the colour channel; y carries something that actually varies.
    const o = gipTimelineOption([node(1), node(2), node(3)], [edge(2, 1), edge(3, 1)]);
    const byGip = new Map(nodeSeries(o).data.map((d) => [d.gip, (d.value as unknown[])[1]]));
    expect(byGip.get(1)).toBe(2);
    expect(byGip.get(2)).toBe(0);
    expect((o.yAxis as { type: string }).type).toBe("value");
  });

  it("colours by stage and tolerates a stage it has never seen", () => {
    const o = gipTimelineOption(
      [node(1, { stage: "voted" }), node(2, { stage: "something-new" })], []);
    const colors = nodeSeries(o).data.map(
      (d) => (d.itemStyle as Record<string, unknown>).color);
    expect(colors[0]).toBe(GIP_STAGE_COLOR("voted"));
    expect(colors[1]).toBe(GIP_STAGE_COLOR("unstaged"));
  });

  it("arcs a forward citation to the opposite side and tints it", () => {
    // 141 of 156 citations point backward (newer cites older). The 15 forward
    // ones only happen when a thread was edited after the fact, so they must
    // not blend in with the normal case.
    const o = gipTimelineOption([node(10), node(20)], [edge(20, 10), edge(10, 20)]);
    const [backward, forward] = linkSeries(o).data.map(
      (l) => l.lineStyle as Record<string, unknown>);
    expect(Number(backward.curveness)).toBeGreaterThan(0);
    expect(Number(forward.curveness)).toBeLessThan(0);
    // Both explicit: an undefined lineStyle.color on a `lines` series falls
    // back to the PALETTE, which painted every arc the same green as a voted
    // node and made the chart read as one connected blob.
    expect(backward.color).toBeTruthy();
    expect(forward.color).toBeTruthy();
    expect(backward.color).not.toBe(forward.color);
  });

  it("sizes a node by how much was SAID — influence already has the y-axis", () => {
    // Spending two channels on one measure would leave discussion volume
    // unrepresented, and they answer different questions.
    const o = gipTimelineOption([node(1, { posts: 1 }), node(2, { posts: 900 })], []);
    const byGip = new Map(nodeSeries(o).data.map((d) => [d.gip, Number(d.symbolSize)]));
    expect(byGip.get(2)!).toBeGreaterThan(byGip.get(1)!);
  });

  it("keeps a zero-post node clickable rather than sizing it to nothing", () => {
    const o = gipTimelineOption([node(1, { posts: 0 }), node(2, { posts: null })], []);
    for (const d of nodeSeries(o).data) expect(Number(d.symbolSize)).toBeGreaterThanOrEqual(7);
  });

  it("can hide the isolated nodes, which are 57 of 149 in the real graph", () => {
    const nodes = [node(1), node(2), node(3)];
    const edges = [edge(1, 2)];
    expect(nodeSeries(gipTimelineOption(nodes, edges)).data).toHaveLength(3);
    expect(nodeSeries(gipTimelineOption(nodes, edges, { hideIsolated: true })).data).toHaveLength(2);
  });

  it("filters by stage without stranding an edge on a hidden node", () => {
    const nodes = [node(1, { stage: "voted" }), node(2, { stage: "phase-1" })];
    const o = gipTimelineOption(nodes, [edge(1, 2)], { stages: ["voted"] });
    expect(nodeSeries(o).data).toHaveLength(1);
    expect(linkSeries(o).data).toHaveLength(0);
  });

  it("dims everything except the pinned node and its own citations", () => {
    const o = gipTimelineOption([node(1), node(2), node(3)],
      [edge(1, 2), edge(3, 2)], { focus: 1 });
    const opacities = nodeSeries(o).data.map(
      (d) => Number((d.itemStyle as Record<string, unknown>).opacity));
    // Relative, not absolute: what matters is that the pinned node reads as
    // foreground and the rest recede, whatever the base opacity happens to be.
    expect(opacities[0]).toBeGreaterThan(opacities[1]);
    const linkOpacity = linkSeries(o).data.map(
      (l) => Number((l.lineStyle as Record<string, unknown>).opacity));
    expect(linkOpacity[0]).toBeGreaterThan(linkOpacity[1]);
  });

  it("carries the drill-down identifiers and the enriched fields onto each node", () => {
    const o = gipTimelineOption([node(7, { topicId: 4242, proposalId: "0xabc", participants: 19 })], []);
    const d = nodeSeries(o).data[0];
    expect(d.topicId).toBe(4242);
    expect(d.proposalId).toBe("0xabc");
    expect(d.participants).toBe(19);
    expect(d.quorumStatus).toBe("met");
  });
});

describe("gipGraphOption (force view)", () => {
  it("still drops phantom endpoints and honours the same filters", () => {
    const nodes = [node(1), node(2), node(3)];
    const o = gipGraphOption(nodes, [edge(1, 2), edge(1, 999)], { hideIsolated: true });
    expect(series(o).links).toHaveLength(1);
    expect(series(o).data).toHaveLength(2);
  });

  it("draws no arrowheads — a citation is not a direction of authority", () => {
    const o = gipGraphOption([node(1), node(2)], [edge(1, 2)]);
    expect((series(o) as unknown as Record<string, unknown>).edgeSymbol).toBeUndefined();
  });

  it("bounds line width so one heavy edge cannot dominate", () => {
    const o = gipGraphOption([node(1), node(2), node(3)], [edge(1, 2, 1), edge(1, 3, 5000)]);
    const widths = series(o).links.map((l) => Number((l.lineStyle as { width: number }).width));
    expect(widths[1]).toBeGreaterThan(widths[0]);
    expect(widths[1]).toBeLessThanOrEqual(5);
  });
});
