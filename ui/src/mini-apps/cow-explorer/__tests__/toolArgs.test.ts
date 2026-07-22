import { describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { buildSectionToolArgs } from "../state/toolArgs";

const DRAFT = {
  base: `0x${"11".repeat(20)}`,
  quote: `0x${"22".repeat(20)}`,
  status: "open",
  owner: `0x${"33".repeat(20)}`,
  token: `0x${"44".repeat(20)}`,
  solver: `0x${"55".repeat(20)}`,
};

describe("CoW section tool arguments", () => {
  it("passes the current chain through and lets the server default", () => {
    const args = buildSectionToolArgs("view-1", structuredClone(MOCK_PAYLOAD.view_state!), "trades", DRAFT);
    expect(args).toMatchObject({
      __tool: "load_cow_explorer_section", view_id: "view-1", section: "trades",
      environment_scope: "production", chain_id: 0, window_days: 30,
      owner: DRAFT.owner, token: DRAFT.token,
    });
    expect(args.base_token).toBe("");
  });

  it("carries pair and role-specific solver filters into Sankey loads", () => {
    const args = buildSectionToolArgs("view-1", structuredClone(MOCK_PAYLOAD.view_state!), "solvers", DRAFT, { window_days: 90 });
    expect(args).toMatchObject({ chain_id: 0, base_token: DRAFT.base, quote_token: DRAFT.quote, solver: DRAFT.solver, window_days: 90 });
  });

  it("keeps explicit chain overrides intact", () => {
    const args = buildSectionToolArgs("view-1", structuredClone(MOCK_PAYLOAD.view_state!), "live", DRAFT, { chain_id: 100 });
    expect(args.chain_id).toBe(100);
  });
});
