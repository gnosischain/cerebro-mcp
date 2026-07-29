import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LiveNowPanel } from "../components/LiveNowPanel";
import type { RowDataset } from "../../shared/rowDataset";

function ds(columns: string[], rows: unknown[][]): RowDataset {
  return { columns, rows };
}

const VOTE_COLS = ["proposal_id", "title", "gip", "state", "start_at", "end_at",
  "hours_left", "votes_count", "scores_total", "quorum", "quorum_status", "quorum_ratio"];
const PIPE_COLS = ["topic_id", "title", "gip", "phase", "posts_count",
  "participant_count", "views", "created_at", "last_posted_at", "days_idle",
  "dormant_hidden"];

const noop = () => {};

describe("LiveNowPanel", () => {
  it("states that nothing is open rather than rendering a blank region", () => {
    // Every one of the 253 indexed proposals is closed, so this IS the normal
    // render. An unlabelled empty box reads as "still loading" or "broken".
    const html = renderToStaticMarkup(
      <LiveNowPanel votes={ds(VOTE_COLS, [])} pipeline={ds(PIPE_COLS, [])}
        onProposal={noop} onTopic={noop} />,
    );
    expect(html).toContain("No Snapshot vote is open right now");
    expect(html).toContain("not a loading state");
  });

  it("uses met/missed/unspecified quorum vocabulary, never pass/fail", () => {
    const html = renderToStaticMarkup(
      <LiveNowPanel
        votes={ds(VOTE_COLS, [["0xaa", "Should we?", 9, "active", "", "", 30, 12, 5, 1, "met", 5]])}
        pipeline={ds(PIPE_COLS, [])} onProposal={noop} onTopic={noop} />,
    );
    expect(html).toContain("quorum met");
    expect(html.toLowerCase()).not.toContain(">pass");
    expect(html.toLowerCase()).not.toContain("fail");
  });

  it("renders hours as hours under two days and as days beyond", () => {
    const at = (h: number) => renderToStaticMarkup(
      <LiveNowPanel votes={ds(VOTE_COLS, [["0xaa", "T", null, "active", "", "", h, 0, 0, 0, "unspecified", null]])}
        pipeline={ds(PIPE_COLS, [])} onProposal={noop} onTopic={noop} />);
    expect(at(30)).toContain("ends in 30h");
    expect(at(72)).toContain("ends in 3d");
    // A negative "ends in" would mean the row should not be on this list at all.
    expect(at(-5)).toContain("closing now");
  });

  it("shows the forum's own phase tags", () => {
    const html = renderToStaticMarkup(
      <LiveNowPanel votes={ds(VOTE_COLS, [])}
        pipeline={ds(PIPE_COLS, [
          [1, "GIP-151: something", 151, "phase-2", 26, 15, 2600, "", "2026-07-18T14:47:36Z", 3, 0],
          [2, "An idea", null, "phase-1", 7, 5, 480, "", "2026-06-27T16:58:53Z", 40, 0],
        ])}
        onProposal={noop} onTopic={noop} />);
    expect(html).toContain("phase-2");
    expect(html).toContain("phase-1");
    expect(html).toContain("GIP-151");
  });

  it("states how many pending topics are dormant instead of hiding them", () => {
    // 88 of 157 open phase-1/2 topics have not been touched in six months. A
    // list that silently dropped them would imply a pipeline that is not there.
    const html = renderToStaticMarkup(
      <LiveNowPanel votes={ds(VOTE_COLS, [])}
        pipeline={ds(PIPE_COLS, [[1, "T", 1, "phase-2", 5, 2, 30, "", "2026-07-01T00:00:00Z", 3, 88]])}
        onProposal={noop} onTopic={noop} />);
    expect(html).toContain("88");
    expect(html).toContain("dormant");
    expect(html).toContain("not shown");
  });

  it("marks a stale thread as idle, but not a fresh one", () => {
    const at = (days: number) => renderToStaticMarkup(
      <LiveNowPanel votes={ds(VOTE_COLS, [])}
        pipeline={ds(PIPE_COLS, [[1, "T", 1, "phase-2", 5, 2, 30, "", "2026-07-01T00:00:00Z", days, 0]])}
        onProposal={noop} onTopic={noop} />);
    expect(at(1)).not.toContain("idle");
    expect(at(40)).toContain("idle 40d");
    expect(at(146)).toContain("idle 4mo");
  });

  it("never claims a listed topic already reached a vote — those are excluded upstream", () => {
    const html = renderToStaticMarkup(
      <LiveNowPanel votes={ds(VOTE_COLS, [])}
        pipeline={ds(PIPE_COLS, [[1, "T", 1, "phase-2", 5, 2, 30, "", "2026-07-01T00:00:00Z", 3, 0]])}
        onProposal={noop} onTopic={noop} />);
    expect(html).not.toContain("reached a vote</span>");
  });

  it("renders no GIP badge for a topic whose title carries no number", () => {
    const html = renderToStaticMarkup(
      <LiveNowPanel votes={ds(VOTE_COLS, [])}
        pipeline={ds(PIPE_COLS, [[2, "[REVIEW] GIP-XXX: draft", null, "phase-2", 16, 9, 1200, "", "2026-07-17T00:49:11Z", 12, 0]])}
        onProposal={noop} onTopic={noop} />);
    expect(html).not.toContain("gov-gip");
    expect(html).toContain("[REVIEW] GIP-XXX: draft");
  });

  it("counts only when there is something to count", () => {
    const empty = renderToStaticMarkup(
      <LiveNowPanel votes={ds(VOTE_COLS, [])} pipeline={ds(PIPE_COLS, [])}
        onProposal={noop} onTopic={noop} />);
    expect(empty).not.toContain("gov-livenow__count");
  });
});
