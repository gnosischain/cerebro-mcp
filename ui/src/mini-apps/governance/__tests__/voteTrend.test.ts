// The vote trend answers the two questions the Snapshot proposal page answers:
// which way the vote went, and whether it cleared quorum. Before this it drew a
// single votes/cumulative-VP pair, which answered neither.
//
// The behaviours worth pinning are the ones that are wrong in a plausible-looking
// way: a quorum line drawn at zero for the 106 proposals that never had one, a
// cumulative series that drops to zero in a bucket where a choice got no votes,
// and the ranked-choice votes quietly disappearing instead of being disclosed.

import { describe, expect, it } from "vitest";

import { UNSUPPORTED_CHOICE, voteTrendOption } from "../model/chartOptions";

type Series = {
  name: string;
  data: number[];
  type: string;
  step?: string;
  lineStyle?: Record<string, unknown>;
  itemStyle?: Record<string, unknown>;
  markLine?: { data: Array<{ yAxis: number }>; label?: { formatter?: string } };
};

const seriesOf = (o: ReturnType<typeof voteTrendOption>) =>
  o.series as unknown as Series[];

function row(bucket: string, choice: string, cumulativeVp: number, extra = {}) {
  return {
    bucket, choice, bucket_unit: "hour" as const,
    cumulative_vp: cumulativeVp, votes: 1, vp: cumulativeVp,
    cumulative_votes: 1, quorum_vp: 75000, ...extra,
  };
}

describe("voteTrendOption", () => {
  const ROWS = [
    row("2026-05-01T10:00:00", "For", 20000),
    row("2026-05-01T10:00:00", "Against", 2000),
    row("2026-05-01T12:00:00", "For", 90000),
    // NOTE: no "Against" row in the 12:00 bucket.
  ];

  it("draws one line per choice", () => {
    const s = seriesOf(voteTrendOption(ROWS, { quorumVp: 75000 }));
    expect(s.filter((x) => x.name === "For")).toHaveLength(1);
    expect(s.filter((x) => x.name === "Against")).toHaveLength(1);
  });

  it("carries a choice's cumulative value across buckets where it got no votes", () => {
    // The bug this prevents: `Against` has no row at 12:00, so a naive lookup
    // yields undefined -> 0 and the line plunges to the axis, reading as though
    // 2,000 VP of opposition were withdrawn.
    const against = seriesOf(voteTrendOption(ROWS, { quorumVp: 75000 }))
      .find((x) => x.name === "Against")!;
    expect(against.data).toEqual([2000, 2000]);
  });

  it("steps rather than smooths — cumulative VP holds flat between votes", () => {
    const forSeries = seriesOf(voteTrendOption(ROWS, {})).find((x) => x.name === "For")!;
    expect(forSeries.step).toBe("end");
  });

  it("draws the quorum threshold when one is configured", () => {
    const quorumSeries = seriesOf(voteTrendOption(ROWS, { quorumVp: 75000 }))
      .find((x) => x.name === "Quorum");
    expect(quorumSeries).toBeDefined();
    expect(quorumSeries!.markLine!.data).toEqual([{ yAxis: 75000 }]);
    expect(quorumSeries!.markLine!.label!.formatter).toContain("75,000");
  });

  it("draws NO quorum line when the proposal never had one", () => {
    // 106 of 253 proposals carry quorum = 0 — everything before 2024-01-19. The
    // SQL sends NULL for those; a line at 0 would assert a bar that every
    // proposal trivially clears.
    for (const q of [null, undefined, 0, Number.NaN]) {
      const s = seriesOf(voteTrendOption(ROWS, { quorumVp: q as number | null }));
      expect(s.find((x) => x.name === "Quorum"), `quorum=${String(q)}`).toBeUndefined();
    }
  });

  it("keeps unsupported-shape votes as their own visible series, last and dashed", () => {
    const rows = [...ROWS, row("2026-05-01T12:00:00", UNSUPPORTED_CHOICE, 5000)];
    const s = seriesOf(voteTrendOption(rows, { quorumVp: 75000 }))
      .filter((x) => x.name !== "Quorum");
    expect(s.map((x) => x.name)).toContain(UNSUPPORTED_CHOICE);
    // Last, so it reads as a footnote rather than a peer choice.
    expect(s[s.length - 1].name).toBe(UNSUPPORTED_CHOICE);
    expect(s[s.length - 1].lineStyle!.type).toBe("dashed");
    // …and it must still carry its voting power, not be zeroed.
    const last = s[s.length - 1].data;
    expect(last[last.length - 1]).toBe(5000);
  });

  it("gives For and Against fixed, distinct hues", () => {
    // Direction IS the information; if the hue moved with legend order you would
    // need the legend to answer "which way did it go".
    const s = seriesOf(voteTrendOption(ROWS, {}));
    const forColor = s.find((x) => x.name === "For")!.itemStyle!.color;
    const againstColor = s.find((x) => x.name === "Against")!.itemStyle!.color;
    expect(forColor).toBeTruthy();
    expect(againstColor).toBeTruthy();
    expect(forColor).not.toBe(againstColor);
  });

  it("survives an empty dataset without inventing a chart", () => {
    const s = seriesOf(voteTrendOption([], { quorumVp: 75000 }));
    expect(s.filter((x) => x.name !== "Quorum")).toHaveLength(0);
  });

  it("sets exactly one lineStyle per series", () => {
    // An earlier version set a base `lineStyle` and then spread a second one for
    // the unsupported case, which replaced rather than merged — the width was
    // silently dropped.
    const rows = [...ROWS, row("2026-05-01T12:00:00", UNSUPPORTED_CHOICE, 5000)];
    for (const s of seriesOf(voteTrendOption(rows, {}))) {
      if (s.name === "Quorum") continue;
      expect(s.lineStyle!.width, s.name).toBe(2);
    }
  });
});
