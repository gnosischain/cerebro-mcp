// Ask Cerebro: prompt content + the pure delivery helper (fakes only — no
// @testing-library, no DOM).

import { describe, expect, it } from "vitest";

import { deliverAskPrompt } from "../components/AskCerebroButton";
import { buildAskPrompt, SIGNALING_DISCLAIMER } from "../model/contextPrompt";
import type { GovernanceViewState } from "../types";

function makeState(overrides: Partial<GovernanceViewState> = {}): GovernanceViewState {
  return {
    section: "proposals",
    date_range: { kind: "relative", anchor: "now", window_days: 90, start_at: "90d", end_at: "" },
    filters: {
      query: "treasury",
      proposal_state: "closed",
      proposal_type: "",
      quorum_status: "met",
      category_id: 0,
      forum_status: "",
      sort_by: "",
    },
    selected_entity: null,
    breadcrumbs: [],
    search: { query: "", candidates: [] },
    applied_request_id: 3,
    scope_id: "scope-1",
    coverage: {},
    coverage_warnings: [],
    warnings: [],
    dataset_revisions: {},
    freshness: {
      snapshot: { latest_ingested_at: "2026-07-22T05:00:00Z", latest_activity_at: "2026-06-25T12:00:00Z", stale: false },
      forum: { latest_ingested_at: "2026-07-21T05:00:00Z", latest_activity_at: "2026-07-22T03:40:00Z", stale: true },
    },
    ...overrides,
  };
}

describe("buildAskPrompt content", () => {
  it("carries section, filters, date range, BOTH freshness clocks, aggregates, and the signaling disclaimer", () => {
    const prompt = buildAskPrompt(makeState(), { proposals: 253, votes: 48136 });
    expect(prompt).toContain("Section: proposals");
    expect(prompt).toContain('text="treasury"');
    expect(prompt).toContain("proposal state=closed");
    expect(prompt).toContain("quorum=met");
    expect(prompt).toContain("last 90 days");
    expect(prompt).toContain("Snapshot: ingested 2026-07-22T05:00:00Z");
    expect(prompt).toContain("latest activity 2026-06-25T12:00:00Z");
    expect(prompt).toContain("Forum: ingested 2026-07-21T05:00:00Z");
    expect(prompt).toContain("[STALE — last ingestion older than 24h]");
    expect(prompt).toContain("- proposals: 253");
    expect(prompt).toContain("- votes: 48136");
    expect(prompt).toContain(SIGNALING_DISCLAIMER);
  });

  it("carries the selected entity when one is loaded", () => {
    const prompt = buildAskPrompt(
      makeState({
        section: "entity",
        selected_entity: { entity_type: "proposal", identifier: "0xabc", label: "GIP-149" },
      }),
      {},
    );
    expect(prompt).toContain("Selected entity: proposal 0xabc");
    expect(prompt).toContain('"GIP-149"');
  });
});

describe("deliverAskPrompt — pure delivery helper", () => {
  it("sendMessage success -> 'sent' (no fallback), prompt passed verbatim", async () => {
    const sent: string[] = [];
    const send = async (text: string) => {
      sent.push(text);
      return true;
    };
    const result = await deliverAskPrompt(send, "PROMPT TEXT");
    expect(result).toBe("sent");
    expect(sent).toEqual(["PROMPT TEXT"]);
  });

  it("sendMessage false (no ext-apps host) -> 'fallback' opens the copyable path", async () => {
    const result = await deliverAskPrompt(async () => false, "PROMPT");
    expect(result).toBe("fallback");
  });

  it("sendMessage throwing -> 'fallback', never an unhandled rejection", async () => {
    const result = await deliverAskPrompt(async () => {
      throw new Error("host went away");
    }, "PROMPT");
    expect(result).toBe("fallback");
  });
});
