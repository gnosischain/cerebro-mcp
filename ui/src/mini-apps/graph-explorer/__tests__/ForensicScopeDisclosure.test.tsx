// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { ForensicScopeDisclosure } from "../ForensicScopeDisclosure";
import type { ForensicScope } from "../types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const scope: ForensicScope = {
  scope_id: "flows:42",
  request_id: 42,
  status: "ready",
  window: { t0: "2026-01-01", t1: "2026-02-01", source: "money.applied" },
  data_horizon: "2026-02-01",
  sources: [
    {
      kind: "dbt_aggregate",
      name: "int_execution_transfers_whitelisted_daily",
      role: "primary",
      status: "ok",
      horizon: "2026-02-01",
      fetched_at: "2026-07-19T12:34:56Z",
    },
  ],
  coverage: {
    rows: { shown: 400, total: 2404 },
    nodes: { shown: 401, total: 2405 },
    edges: { shown: 400, total: 2404 },
    usd: { known: 91.2, total: 100, unknown_rows: 0 },
  },
  truncation: { truncated: true, rule: "USD-descending" },
  residuals: ["Native xDAI transfers are not represented."],
  warnings: ["Applied token universe contains 45 addresses."],
  verification: { status: "verified", method: "companion count" },
  token_universe: {
    addresses: [],
    count: 45,
    as_of: "2026-02-01",
    source: "dbt.token_universe",
    sha256: "token-hash",
  },
  app_commit: "app-commit",
  dbt_manifest_sha256: "manifest-hash",
  result_row_hash: "row-hash",
};

describe("ForensicScopeDisclosure", () => {
  it("renders only an evidence icon and opens provenance in the structural panel", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <ForensicScopeDisclosure
          scope={scope}
          datasets="Money Trail nodes and edges"
          summary="400/2,404 counterparties · 2,004 dropped · 91.2% measured USD retained"
          bound="400/hop · USD-descending · 45-token set"
        />,
      );
    });

    const trigger = host.querySelector<HTMLButtonElement>(".ge-evidence-trigger");
    expect(trigger?.getAttribute("aria-label")).toContain("Evidence: READY, 1 source");
    expect(trigger?.textContent).toBe("ⓘ");
    expect(host.querySelector(".ge-scope-disclosure")).toBeNull();
    expect(host.querySelector(".ge-task-accessory-panel")).toBeNull();

    await act(async () => {
      trigger?.click();
    });
    const detailsBody = host.querySelector<HTMLElement>(
      ".ge-evidence-panel__details",
    );
    expect(host.querySelector(".ge-task-accessory-panel")).not.toBeNull();
    expect(detailsBody?.textContent).toContain("400/2,404 counterparties");
    expect(detailsBody?.textContent).toContain("2,004 dropped");
    expect(detailsBody?.textContent).toContain("91.2% measured USD retained");
    expect(detailsBody?.textContent).toContain("400/hop · USD-descending · 45-token set");
    expect(detailsBody?.textContent).toContain("flows:42");
    expect(detailsBody?.textContent).toContain("int_execution_transfers_whitelisted_daily");
    expect(detailsBody?.textContent).toContain("primary");
    expect(detailsBody?.textContent).toContain("source watermark 2026-02-01");
    expect(detailsBody?.textContent).toContain("checked 2026-07-19T12:34:56Z");
    expect(detailsBody?.textContent).toContain("Applied token universe contains 45 addresses.");
    expect(detailsBody?.textContent).toContain("Native xDAI transfers are not represented.");
    expect(detailsBody?.textContent).toContain("rows 400 of 2,404");
    expect(detailsBody?.textContent).toContain("app-commit");
    expect(detailsBody?.textContent).toContain("manifest-hash");
    expect(detailsBody?.textContent).toContain("row-hash");

    // Detailed relation names and limitations remain available without being
    // duplicated into the compact, always-visible task summary.
    expect(trigger?.textContent).not.toContain("flows:42");
    expect(trigger?.textContent).not.toContain("int_execution_transfers_whitelisted_daily");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(host.querySelector(".ge-task-accessory-panel")).toBeNull();
    expect(document.activeElement).toBe(trigger);

    await act(async () => root.unmount());
    host.remove();
  });

  it("does not mislabel an untouched mode as an evidence failure", async () => {
    const host = document.createElement("div");
    const root = createRoot(host);
    await act(async () => {
      root.render(<ForensicScopeDisclosure scope={undefined} datasets="relationships" />);
    });
    expect(host.textContent).toBe("");
    expect(host.querySelector(".ge-scope-disclosure")).toBeNull();

    await act(async () => root.unmount());
  });
});
