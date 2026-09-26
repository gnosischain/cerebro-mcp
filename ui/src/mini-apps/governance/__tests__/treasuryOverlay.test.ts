// The CoinGecko overlay (icons + spot FALLBACK quotes) is requested wherever
// treasury tokens are on screen: the section AND its wallet / token pages. A
// cold link to a wallet page used to render without icons or a spot subtotal
// because the request fired only on `section === "treasury"`.

import { describe, expect, it } from "vitest";

import { isTreasuryContext, overlayRequestKey, shouldRequestOverlay } from "../state/overlay";
import type { GovernanceViewState } from "../types";

type OverlayState = Pick<GovernanceViewState, "section" | "selected_entity" | "dataset_revisions">;

function state(overrides: Partial<OverlayState> = {}): OverlayState {
  return { section: "treasury", selected_entity: null, dataset_revisions: { treasury_holdings: 1 }, ...overrides };
}

describe("shouldRequestOverlay", () => {
  it("fires on the treasury section once a dataset has landed", () => {
    expect(shouldRequestOverlay(state())).toBe(true);
    expect(shouldRequestOverlay(state({ dataset_revisions: {} }))).toBe(false);
  });

  it("fires on treasury wallet and token pages", () => {
    for (const entityType of ["treasury_wallet", "treasury_token"] as const) {
      const entity = state({
        section: "entity",
        selected_entity: { entity_type: entityType, identifier: "1:0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f", label: "" },
        dataset_revisions: { treasury_wallet_detail: 1 },
      });
      expect(shouldRequestOverlay(entity), entityType).toBe(true);
      expect(isTreasuryContext(entity)).toBe(true);
    }
  });

  it("never fires on other sections or entities", () => {
    expect(shouldRequestOverlay(state({ section: "proposals" }))).toBe(false);
    expect(shouldRequestOverlay(state({
      section: "entity",
      selected_entity: { entity_type: "proposal", identifier: "0xabc", label: "" },
    }))).toBe(false);
  });

  it("keys the de-dup on the view, the entity and the dataset revisions", () => {
    const a = overlayRequestKey("v1", state());
    expect(overlayRequestKey("v1", state())).toBe(a);
    expect(overlayRequestKey("v2", state())).not.toBe(a);
    expect(overlayRequestKey("v1", state({ dataset_revisions: { treasury_holdings: 2 } }))).not.toBe(a);
    const wallet1 = overlayRequestKey("v1", state({
      section: "entity",
      selected_entity: { entity_type: "treasury_wallet", identifier: "1:0xabc", label: "" },
    }));
    const wallet100 = overlayRequestKey("v1", state({
      section: "entity",
      selected_entity: { entity_type: "treasury_wallet", identifier: "100:0xabc", label: "" },
    }));
    expect(wallet1).not.toBe(wallet100);
  });
});
