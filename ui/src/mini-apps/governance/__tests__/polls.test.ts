// groupPollOptions contract: per-option rows pivot into one view per poll;
// voters is TAKEN from the group's first row (never summed); hidden polls
// (all-null scores) and zero-vote polls are distinct states; and no raw -1
// sentinel ever reaches the frontend contract (the SQL nullIfs it away).

import { describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { groupPollOptions } from "../model/polls";
import type { RowDataset } from "../../shared/rowDataset";

const COLUMNS = [
  "poll_id", "post_id", "post_number", "poll_name", "poll_type", "status",
  "results_visibility", "is_public", "close_at", "voters", "option_id",
  "option_label", "option_votes",
];

function ds(rows: unknown[][]): RowDataset {
  return { columns: COLUMNS, rows };
}

const fixtureTopicPolls: RowDataset = {
  columns: MOCK_PAYLOAD.datasets!.topic_polls.columns.map((c) => c.name),
  rows: MOCK_PAYLOAD.datasets!.topic_polls.preview_rows,
};

describe("groupPollOptions", () => {
  it("groups option rows into one view per poll, first-seen order", () => {
    const views = groupPollOptions(fixtureTopicPolls);
    expect(views.map((v) => v.pollId)).toEqual([234, 237, 238, 240, 241]);
    expect(views.find((v) => v.pollId === 240)?.entries).toHaveLength(3);
  });

  it("takes voters from the first row of the group — never summed", () => {
    const views = groupPollOptions(ds([
      [7, 1, 1, "poll", "regular", "open", "always", 1, null, 12, "a", "Yes", 8],
      [7, 1, 1, "poll", "regular", "open", "always", 1, null, 12, "b", "No", 4],
    ]));
    expect(views).toHaveLength(1);
    // 12 participants, NOT 24 (repeat) and NOT 12+12.
    expect(views[0].voters).toBe(12);
  });

  it("flags hidden polls (every score null) and keeps zero-vote distinct", () => {
    const views = groupPollOptions(fixtureTopicPolls);
    const hidden = views.find((v) => v.pollId === 234)!;
    expect(hidden.resultsHidden).toBe(true);
    expect(hidden.hasVotes).toBe(false);
    const zeroVote = views.find((v) => v.pollId === 237)!;
    expect(zeroVote.resultsHidden).toBe(false);
    expect(zeroVote.hasVotes).toBe(false);
    const voted = views.find((v) => v.pollId === 241)!;
    expect(voted.resultsHidden).toBe(false);
    expect(voted.hasVotes).toBe(true);
  });

  it("passes the poll-bearing post number through (null when unmapped/0)", () => {
    const views = groupPollOptions(fixtureTopicPolls);
    expect(views.find((v) => v.pollId === 238)?.postNumber).toBe(3);
    const unmapped = groupPollOptions(ds([
      [9, 1, 0, "poll", "regular", "open", "always", 1, null, 5, "a", "Yes", 5],
    ]));
    expect(unmapped[0].postNumber).toBeNull();
  });

  it("fixture rows carry null for hidden scores — a raw -1 must never appear", () => {
    const votesIndex = fixtureTopicPolls.columns.indexOf("option_votes");
    for (const row of fixtureTopicPolls.rows) {
      expect(row[votesIndex]).not.toBe(-1);
    }
    const listDescriptor = MOCK_PAYLOAD.datasets!.forum_polls;
    const leadingIndex = listDescriptor.columns.findIndex((c) => c.name === "leading_votes");
    for (const row of listDescriptor.preview_rows) {
      expect(row[leadingIndex]).not.toBe(-1);
    }
  });
});
