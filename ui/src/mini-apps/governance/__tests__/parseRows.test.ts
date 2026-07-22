import { describe, expect, it } from "vitest";

import { rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import {
  parseActivity,
  parseConcentration,
  parseLinks,
  parseSpaceSummary,
} from "../model/parseRows";

/** Shuffle columns (with rows re-ordered to match) — parsers must not care. */
function shuffled(dataset: RowDataset): RowDataset {
  const order = dataset.columns.map((_, i) => i).reverse();
  return {
    columns: order.map((i) => dataset.columns[i]),
    rows: dataset.rows.map((row) => order.map((i) => row[i])),
  };
}

const FRESHNESS: RowDataset = {
  columns: ["source", "latest_ingested_at", "latest_activity_at"],
  rows: [
    ["snapshot", "2026-07-22T05:00:00Z", "2026-06-25T12:00:00Z"],
    ["forum", "2026-07-22T05:00:00Z", "2026-07-22T03:40:00Z"],
  ],
};

const ACTIVITY: RowDataset = {
  columns: ["bucket", "bucket_unit", "proposals_created", "votes_cast"],
  rows: [
    ["2026-06-29", "week", 2, 1450],
    ["2026-07-06", "week", "1", "610"], // stringly numbers must coerce
    ["2026-07-13", "week", null, "not-a-number"], // non-finite dropped, row kept
  ],
};

describe("rowsToObjects", () => {
  it("keys cells by column name so column order never matters", () => {
    const objects = rowsToObjects(FRESHNESS);
    const shuffledObjects = rowsToObjects(shuffled(FRESHNESS));
    expect(shuffledObjects).toEqual(objects);
  });

  it("returns [] for a missing dataset", () => {
    expect(rowsToObjects(undefined)).toEqual([]);
  });
});

describe("typed parsers", () => {
  it("parseActivity keeps bucket_unit, coerces numbers, and never throws on junk", () => {
    const parsed = parseActivity(ACTIVITY);
    expect(parseActivity(shuffled(ACTIVITY))).toEqual(parsed);
    expect(parsed).toHaveLength(3);
    expect(parsed[0]).toMatchObject({ bucket: "2026-06-29", bucket_unit: "week", proposals_created: 2 });
    expect(parsed[1]).toMatchObject({ proposals_created: 1, votes_cast: 610 });
    expect(parsed[2].proposals_created).toBeUndefined();
  });

  it("parseActivity pivots LONG (bucket, metric, metric_value) rows to wide", () => {
    // Mirrors governance_activity / proposal_activity / forum_activity SQL:
    // one row per (bucket, metric), ORDER BY bucket, metric.
    const long: RowDataset = {
      columns: ["bucket", "metric", "metric_value", "bucket_unit"],
      rows: [
        ["2026-06-29", "proposals_created", 2, "week"],
        ["2026-06-29", "votes_cast", 1450, "week"],
        ["2026-07-06", "proposals_created", "1", "week"], // stringly numbers coerce
        ["2026-07-06", "votes_cast", 610, "week"],
        ["2026-07-13", "votes_cast", 99, "week"], // proposals_created missing
      ],
    };
    const parsed = parseActivity(long);
    expect(parsed).toHaveLength(3);
    expect(parsed.map((row) => row.bucket)).toEqual(["2026-06-29", "2026-07-06", "2026-07-13"]);
    expect(parsed[0]).toMatchObject({ bucket_unit: "week", proposals_created: 2, votes_cast: 1450 });
    expect(parsed[1]).toMatchObject({ proposals_created: 1, votes_cast: 610 });
    // Missing metric on a bucket stays absent — charts apply `?? 0`.
    expect(parsed[2].proposals_created).toBeUndefined();
    expect(parsed[2].votes_cast).toBe(99);
    // Column order must never matter.
    expect(parseActivity(shuffled(long))).toEqual(parsed);
  });

  it("parseSpaceSummary reads the single KPI row regardless of column order", () => {
    const dataset: RowDataset = {
      columns: ["proposal_count", "vote_count", "voter_count", "follower_count", "topic_count", "post_count", "forum_user_count"],
      rows: [[253, 48136, 6341, 12229, 882, 6836, 2665]],
    };
    const expected = {
      proposal_count: 253, vote_count: 48136, voter_count: 6341, follower_count: 12229,
      topic_count: 882, post_count: 6836, forum_user_count: 2665,
    };
    expect(parseSpaceSummary(dataset)).toEqual(expected);
    expect(parseSpaceSummary(shuffled(dataset))).toEqual(expected);
    expect(parseSpaceSummary({ columns: dataset.columns, rows: [] })).toBeNull();
  });

  it("parseConcentration keeps only valid tier/metric rows", () => {
    const dataset: RowDataset = {
      columns: ["tier", "metric", "share"],
      rows: [[10, "vp", 0.61], [20, "votes", 0.34], ["x", "vp", 0.5], [50, "bogus", 0.1]],
    };
    const parsed = parseConcentration(dataset);
    expect(parsed).toEqual([
      { tier: 10, metric: "vp", share: 0.61 },
      { tier: 20, metric: "votes", share: 0.34 },
    ]);
    expect(parseConcentration(shuffled(dataset))).toEqual(parsed);
  });

  it("parseLinks reads the real proposal_forum_links columns and rejects unknown tiers", () => {
    // Columns mirror the backend SQL exactly:
    // SELECT linked_type, linked_id, linked_title, link_source, activity_count, activity_at
    const dataset: RowDataset = {
      columns: ["linked_type", "linked_id", "linked_title", "link_source", "activity_count", "activity_at"],
      rows: [
        ["forum_topic", "12131", "GIP-149 topic", "discussion", 34, "2026-05-10T09:00:00Z"],
        ["proposal", `0x${"a1".repeat(32)}`, "Temperature check", "gip", 88, "2026-04-20T00:00:00Z"],
        ["forum_topic", "1", "never", "fuzzy", 0, ""],
      ],
    };
    const parsed = parseLinks(dataset);
    expect(parsed.map((l) => l.link_source)).toEqual(["discussion", "gip"]);
    expect(parsed[0]).toMatchObject({
      linked_type: "forum_topic", linked_id: "12131",
      linked_title: "GIP-149 topic", activity_count: 34,
    });
    expect(parsed[1].linked_type).toBe("proposal");
    expect(parseLinks(shuffled(dataset))).toEqual(parsed);
  });

  it("parseLinks treats topic_proposal_links rows (no linked_type) as proposals", () => {
    // Columns mirror the backend SQL exactly:
    // SELECT linked_id, linked_title, state, link_source, votes_count, created_at
    const dataset: RowDataset = {
      columns: ["linked_id", "linked_title", "state", "link_source", "votes_count", "created_at"],
      rows: [
        [`0x${"b2".repeat(32)}`, "GIP-152 proposal", "closed", "discussion", 412, "2026-05-01T09:00:00Z"],
        [`0x${"c3".repeat(32)}`, "GIP-152 re-run", "active", "gip", 12, "2026-06-01T09:00:00Z"],
      ],
    };
    const parsed = parseLinks(dataset);
    expect(parsed).toHaveLength(2);
    expect(parsed.every((l) => l.linked_type === "proposal")).toBe(true);
    expect(parsed[0]).toMatchObject({ state: "closed", votes_count: 412 });
    expect(parseLinks(shuffled(dataset))).toEqual(parsed);
  });
});
