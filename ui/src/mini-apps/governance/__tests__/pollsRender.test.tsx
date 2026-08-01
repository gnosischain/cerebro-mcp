// @vitest-environment jsdom

// Render tests for the poll/likes surfaces: ChoiceBars unit vocabulary, the
// TopicDetail poll states (hidden / no-votes / multiple-choice / Post #N),
// and the ForumSection visible attribution disclosures — the QuerySpec.basis
// strings are metadata the UI never renders, so the disclosure must be
// user-visible copy and these tests pin it.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChoiceBars } from "../components/ChoiceBars";
import { ProposalDetail } from "../detail/ProposalDetail";
import { TopicDetail } from "../detail/TopicDetail";
import { ForumSection } from "../sections/ForumSection";
import type { GovViewContext } from "../sections/common";
import { MOCK_PAYLOAD } from "../devFixture";
import { EMPTY_DRAFT } from "../state/toolArgs";
import type { GovernanceViewState } from "../types";

function ctxFor(
  section: GovernanceViewState["section"],
  entity: GovernanceViewState["selected_entity"] = { entity_type: "forum_topic", identifier: "12131", label: "GIP-149 topic" },
): GovViewContext {
  const base = MOCK_PAYLOAD.view_state!;
  const state: GovernanceViewState = {
    ...base,
    section,
    selected_entity: section === "entity" ? entity : null,
    loaded_groups: Object.fromEntries(
      Object.entries(base.loaded_groups ?? {}).map(([key, loaded]) => [
        key,
        key.startsWith("forum.") ? true : loaded,
      ]),
    ),
  };
  return {
    state,
    descriptors: MOCK_PAYLOAD.datasets!,
    hydrated: {},
    viewId: "test",
    fetchRows: async () => null,
    draft: { ...EMPTY_DRAFT },
    setDraft: () => {},
    apply: () => {},
    loading: false,
    onEntity: () => {},
    failedGroups: [],
    retryGroup: () => {},
    openLink: () => {},
    sendMessage: async () => true,
    aggregates: {},
  };
}

describe("ChoiceBars unit vocabulary", () => {
  const entries = [
    { index: 1, label: "In Favour", score: 14 },
    { index: 2, label: "Against", score: 4 },
  ];

  it("defaults to the historical VP wording (proposals untouched)", () => {
    const html = renderToStaticMarkup(
      <ChoiceBars entries={entries} quorum={null} scoresTotal={18} />,
    );
    expect(html).toContain("VP cast");
    expect(html).toContain("Voting power cast");
  });

  it("renders the poll unit when unitLabel is passed", () => {
    const html = renderToStaticMarkup(
      <ChoiceBars entries={entries} quorum={null} scoresTotal={18} unitLabel="votes" />,
    );
    expect(html).toContain("votes cast");
    expect(html).not.toContain("VP cast");
    expect(html).toContain("Votes cast 18");
  });
});

describe("TopicDetail polls panel", () => {
  const html = renderToStaticMarkup(<TopicDetail ctx={ctxFor("entity")} />);

  it("renders one block per poll with the poll-bearing post number", () => {
    expect(html).toContain("Polls");
    // Poll 238 lives in a REPLY (post #3), not the opening post.
    expect(html).toContain("Post #3");
  });

  it("says why hidden results show no numbers", () => {
    expect(html).toContain("Results hidden by this poll");
  });

  it("labels a zero-vote poll instead of showing an empty leader", () => {
    expect(html).toContain("No votes yet.");
  });

  it("explains multiple-choice share arithmetic", () => {
    expect(html).toContain("Multiple-choice poll");
    expect(html).toContain("can exceed the voter count");
  });

  it("uses vote vocabulary, never VP, for poll bars", () => {
    expect(html).toContain("votes cast");
  });

  it("renders the per-post likes timeline panel", () => {
    expect(html).toContain("Likes over time");
    expect(html).toContain("stacked by which post received them");
  });

  it("collapses the thread by default so the metrics lead the page", () => {
    expect(html).toContain("Show thread");
    // The fixture's post bodies must NOT render while collapsed.
    expect(html).not.toContain("We should fund it.");
    // A collapsed panel still announces itself and can be expanded.
    expect(html).toContain("Expand");
  });

  it("surfaces the linked Snapshot vote in the identity row, not a panel", () => {
    // Fixture carries a discussion link + one extra GIP match -> "(+1)".
    expect(html).toContain("Snapshot vote (+1)");
    expect(html).not.toContain("Linked Snapshot proposals");
  });

  it("keeps the topic brief visible above the collapsed thread", () => {
    expect(html).toContain("gov-topic-brief");
    expect(html).toContain("#149");
    // Opener author, GIP phase tag (dash rendered as a space), type tag,
    // long-form created date.
    expect(html).toContain("alice");
    expect(html).toContain("phase 2");
    expect(html).toContain("funding");
    expect(html).toContain("April 28, 2026");
  });
});

describe("ProposalDetail votes table", () => {
  const html = renderToStaticMarkup(
    <ProposalDetail ctx={ctxFor("entity", { entity_type: "proposal", identifier: `0x${"a1".repeat(32)}`, label: "GIP-149" })} />,
  );

  it("renders each voter's share of cast power as a bar + percentage", () => {
    expect(html).toContain("VP share");
    expect(html).toContain("gov-share-cell");
    // 0.4104 in the fixture -> 41.0%.
    expect(html).toContain("41.0%");
  });

  it("renders an em-dash, never a fake 0%, while scores are pending", () => {
    // The fixture's pending-vp row carries vp_share null.
    expect(html).toContain("—");
    expect(html).not.toContain("0.00%");
  });
});

describe("ForumSection disclosures", () => {
  const html = renderToStaticMarkup(<ForumSection ctx={ctxFor("forum")} />);

  it("shows BOTH windowed like KPIs beside the lifetime counter", () => {
    expect(html).toContain("Attributed likes (range)");
    expect(html).toContain("Likers (range)");
    expect(html).toContain("Likes (lifetime)");
  });

  it("renders the LIVE attribution figure with the window caveat", () => {
    // 0.723 in the fixture — the copy interpolates the live figure, it is
    // not hard-coded.
    expect(html).toContain("72%");
    expect(html).toContain("coverage within the selected window is unknown");
  });

  it("counts exclusions visibly when nonzero", () => {
    expect(html).toContain("19 unmapped likes excluded");
  });

  it("carries the disclosure on the leaderboard panel outside the gate", () => {
    expect(html).toContain("Likes received/given (range) are attributed likes only");
  });

  it("says poll activity buckets by poll creation, not vote time", () => {
    expect(html).toContain("poll-bearing post");
    expect(html).toContain("not vote time");
  });

  it("renders Tie / No votes / Hidden leading-option cells", () => {
    expect(html).toContain("Tie");
    expect(html).toContain("No votes");
    expect(html).toContain("Hidden");
  });
});
