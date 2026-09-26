// Shared GovViewContext builder for the treasury render tests.

import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import { MOCK_PAYLOAD } from "../devFixture";
import { treasuryEntityDatasets } from "../devFixtureTreasury";
import type { GovViewContext } from "../sections/common";
import { EMPTY_DRAFT } from "../state/toolArgs";
import { DEFAULT_TREASURY_VIEW, type TreasuryViewState } from "../state/treasuryView";
import type { GovernanceViewState } from "../types";

/** 2026-09-25, the day after the fixture's as-of. */
export const NOW = Date.UTC(2026, 8, 25);

export function failedDescriptor(key: string, columns: string[]): DatasetDescriptor {
  return {
    key,
    title: key,
    sql: "",
    database: "governance_db",
    columns: columns.map((name) => ({ name, type: "Unknown" })),
    stats: { row_count: 0, rows_returned: 0, mode: "exact_capped", source_rows: 0, row_cap: 10000, truncated: false, warnings: [] },
    preview_rows: [],
    provenance: { coverage: { error: "Code: 241. Memory limit exceeded", warning_codes: ["query_failed"] } },
  };
}

export function treasuryCtx(opts: {
  view?: Partial<TreasuryViewState>;
  datasets?: Record<string, DatasetDescriptor>;
  entity?: { type: "treasury_wallet" | "treasury_token"; identifier: string };
  overlay?: boolean;
  onEntity?: (type: string, identifier: string) => void;
} = {}): GovViewContext {
  const base = MOCK_PAYLOAD.view_state!;
  const entity = opts.entity;
  const state: GovernanceViewState = {
    ...base,
    section: entity ? "entity" : "treasury",
    selected_entity: entity ? { entity_type: entity.type, identifier: entity.identifier, label: entity.identifier } : null,
    loaded_groups: Object.fromEntries(
      Object.entries(base.loaded_groups ?? {}).map(([key, loaded]) => [key, key.startsWith("treasury.") ? true : loaded]),
    ),
    ...(opts.overlay === false ? { price_overlay: {}, price_overlay_at: "", icon_overlay: {} } : {}),
  };
  const descriptors = {
    ...MOCK_PAYLOAD.datasets!,
    ...(entity ? treasuryEntityDatasets(entity.type, entity.identifier) : {}),
    ...(opts.datasets ?? {}),
  };
  return {
    state,
    descriptors,
    hydrated: {},
    viewId: "test",
    fetchRows: async () => null,
    draft: { ...EMPTY_DRAFT },
    setDraft: () => {},
    apply: () => {},
    loading: false,
    onEntity: (type, identifier) => opts.onEntity?.(type, identifier),
    failedGroups: [],
    retryGroup: () => {},
    openLink: () => {},
    sendMessage: async () => true,
    aggregates: {},
    treasury: { view: { ...DEFAULT_TREASURY_VIEW, ...(opts.view ?? {}) }, update: () => {}, now: NOW },
  };
}

/** Rendered HTML with entities decoded, so copy assertions read naturally. */
export function decode(html: string): string {
  return html
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, "\"")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}
