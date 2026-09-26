// Ask-Cerebro prompt + host model-context lines. Pure string builders so the
// exact wording is unit-testable without a host.

import { isTreasuryContext } from "../state/overlay";
import { describeTreasuryView, type TreasuryViewState } from "../state/treasuryView";
import type { GovernanceViewState, GovSourceFreshness } from "../types";
import { PROVENANCE_LINE } from "./treasuryCopy";

export const SIGNALING_DISCLAIMER =
  "Note: this data covers Snapshot off-chain signaling and forum activity only — "
  + "it is not binding on-chain execution.";

const DEFAULT_QUESTION =
  "What stands out in this governance data, and what should I look into next?";

/** Headline numbers the caller extracts from the currently loaded datasets
 * (space_summary / section summaries). Keys are human labels. */
export type GovAggregates = Record<string, string | number>;

function rangeLabel(state: GovernanceViewState): string {
  const range = state.date_range;
  if (range.kind === "absolute" && range.start_at && range.end_at) {
    return `${range.start_at} to ${range.end_at} (UTC)`;
  }
  if (range.kind === "relative" && range.window_days) {
    return range.window_days === 365 ? "last 1 year" : `last ${range.window_days} days`;
  }
  return "all history";
}

function activeFilters(state: GovernanceViewState): string[] {
  const f = state.filters;
  const parts: string[] = [];
  if (f.query) parts.push(`text="${f.query}"`);
  if (f.proposal_state) parts.push(`proposal state=${f.proposal_state}`);
  if (f.proposal_type) parts.push(`proposal type=${f.proposal_type}`);
  if (f.quorum_status) parts.push(`quorum=${f.quorum_status}`);
  if (f.category_id) parts.push(`forum category=${f.category_id}`);
  if (f.forum_status) parts.push(`forum status=${f.forum_status}`);
  if (f.sort_by) parts.push(`sort=${f.sort_by}`);
  return parts;
}

function freshnessLine(name: string, clock: GovSourceFreshness): string {
  const ingested = clock.latest_ingested_at ?? "unknown";
  const activity = clock.latest_activity_at ?? "unknown";
  const stale = clock.stale ? " [STALE — last ingestion older than 24h]" : "";
  return `${name}: ingested ${ingested}, latest activity ${activity}${stale}`;
}

function entityLine(state: GovernanceViewState): string {
  const entity = state.selected_entity;
  if (!entity) return "";
  return `Selected entity: ${entity.entity_type} ${entity.identifier}`
    + (entity.label ? ` ("${entity.label}")` : "");
}

/** The treasury view line, or "" outside treasury contexts. The treasury
 * filters are client-side, so the host would otherwise never learn which
 * chain / Ltd. / hidden-token view the user is actually looking at. */
export function treasuryViewLine(
  state: GovernanceViewState,
  treasuryView?: TreasuryViewState | null,
): string {
  if (!treasuryView || !isTreasuryContext(state)) return "";
  return describeTreasuryView(treasuryView);
}

/** The full prompt handed to `sendMessage` by the Ask Cerebro button. */
export function buildAskPrompt(
  state: GovernanceViewState,
  aggregates: GovAggregates,
  treasuryView?: TreasuryViewState | null,
): string {
  const lines: string[] = [
    "I am looking at the Gnosis DAO Governance Explorer.",
    `Section: ${state.section}`,
  ];
  const entity = entityLine(state);
  if (entity) lines.push(entity);
  const treasury = treasuryViewLine(state, treasuryView);
  if (treasury) {
    lines.push(`Treasury view: ${treasury}`);
    lines.push(`Treasury data: ${PROVENANCE_LINE}`);
  }
  const filters = activeFilters(state);
  lines.push(`Filters: ${filters.length > 0 ? filters.join(", ") : "none"}`);
  lines.push(`Date range: ${rangeLabel(state)}`);
  lines.push("Data freshness:");
  lines.push(`- ${freshnessLine("Snapshot", state.freshness.snapshot)}`);
  lines.push(`- ${freshnessLine("Forum", state.freshness.forum)}`);
  const aggregateEntries = Object.entries(aggregates);
  if (aggregateEntries.length > 0) {
    lines.push("Headline aggregates:");
    for (const [label, value] of aggregateEntries) lines.push(`- ${label}: ${value}`);
  }
  lines.push(SIGNALING_DISCLAIMER);
  lines.push("");
  lines.push(DEFAULT_QUESTION);
  return lines.join("\n");
}

/** Compact key/value lines for host `updateModelContext`. */
export function buildModelContextLines(
  state: GovernanceViewState,
  aggregates: GovAggregates,
  treasuryView?: TreasuryViewState | null,
): Record<string, unknown> {
  const lines: Record<string, unknown> = {
    app: "Governance Explorer (Snapshot signaling + forum activity; not binding execution)",
    section: state.section,
    date_range: rangeLabel(state),
    filters: activeFilters(state).join(", ") || "none",
    snapshot_freshness: freshnessLine("Snapshot", state.freshness.snapshot),
    forum_freshness: freshnessLine("Forum", state.freshness.forum),
  };
  const entity = entityLine(state);
  if (entity) lines.selected_entity = entity;
  const treasury = treasuryViewLine(state, treasuryView);
  if (treasury) {
    lines.treasury_view = treasury;
    lines.treasury_provenance = PROVENANCE_LINE;
  }
  for (const [label, value] of Object.entries(aggregates)) lines[`aggregate: ${label}`] = value;
  return lines;
}
