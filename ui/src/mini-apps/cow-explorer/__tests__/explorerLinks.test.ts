import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { buildExternalExplorerUrl, EntityDetail } from "../detail/EntityDetail";
import { MOCK_PAYLOAD } from "../devFixture";
import type { CowExplorerViewState, ExplorerInfo } from "../types";

const BLOCKSCOUT_BASES = [
  "https://eth.blockscout.com",
  "https://gnosis.blockscout.com",
  "https://arbitrum.blockscout.com",
  "https://base.blockscout.com",
  "https://polygon.blockscout.com",
  "https://explorer.linea.build",
  "https://explorer.inkonchain.com",
  "https://eth-sepolia.blockscout.com",
];

function explorer(base: string, brand: string, provider: ExplorerInfo["provider"], tokenAsAddress = false): ExplorerInfo {
  return {
    provider, brand, base_url: base,
    transaction_url_template: `${base}/tx/{hash}`,
    address_url_template: `${base}/address/{address}`,
    token_url_template: `${base}/${tokenAsAddress ? "address" : "token"}/{address}`,
  };
}

function state(info: ExplorerInfo, entityType: "transaction" | "token" | "address"): CowExplorerViewState {
  const value = entityType === "transaction" ? `0x${"ab".repeat(32)}` : `0x${"cd".repeat(20)}`;
  return {
    ...structuredClone(MOCK_PAYLOAD.view_state!), explorer: info,
    selected_entity: { entity_type: entityType, identifier: value, chain_id: 1, chain_name: "Test" },
  };
}

describe("provider-specific external links", () => {
  it.each(BLOCKSCOUT_BASES)("uses EIP-3091 Blockscout paths for %s", (base) => {
    const info = explorer(base, "Blockscout", "blockscout");
    expect(buildExternalExplorerUrl(state(info, "transaction"))).toContain(`${base}/tx/0x`);
    expect(buildExternalExplorerUrl(state(info, "address"))).toContain(`${base}/address/0x`);
    expect(buildExternalExplorerUrl(state(info, "token"))).toContain(`${base}/token/0x`);
  });

  it("uses the documented BNB, Avalanche, and Plasma fallback routes", () => {
    const bnb = explorer("https://bscscan.com", "BscScan", "bscscan");
    const avalanche = explorer("https://subnets.avax.network/c-chain", "Avalanche Explorer", "avalanche", true);
    const plasma = explorer("https://plasmascan.to", "Plasmascan", "plasmascan");
    expect(buildExternalExplorerUrl(state(bnb, "token"))).toContain("bscscan.com/token/");
    expect(buildExternalExplorerUrl(state(avalanche, "token"))).toContain("subnets.avax.network/c-chain/address/");
    expect(buildExternalExplorerUrl(state(plasma, "transaction"))).toContain("plasmascan.to/tx/");
  });

  it("renders external explorer links as safe new-tab anchors", () => {
    const info = explorer("https://eth.blockscout.com", "Blockscout", "blockscout");
    const html = renderToStaticMarkup(createElement(EntityDetail, {
      state: state(info, "transaction"), descriptors: {}, viewId: "view",
      fetchRows: async () => null, onBack: () => undefined,
      onEntity: () => undefined, openExternal: () => undefined,
    }));
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain("Open in Blockscout");
  });
});
