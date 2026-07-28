// Relationship-profile metadata formatters. Extracted verbatim from the old
// AtlasView so the preview card and the scope strip can share them without
// importing a mode component.

import type { ForensicCoverageCount, ForensicScope } from "../types";

export function relationshipWeightUnit(column: string | null | undefined): string {
  const normalized = String(column ?? "").trim().toLowerCase();
  if (!normalized) return "Unweighted relationship (edge count)";
  if (normalized.includes("usd")) return "USD value";
  if (normalized.includes("count")) return "Count";
  if (normalized.includes("percent") || normalized.includes("ratio")) return "Ratio";
  if (normalized.includes("score")) return "Score";
  if (normalized.includes("amount") || normalized.includes("balance")) {
    return "Token amount (native units)";
  }
  return normalized
    .split("_")
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

export function relationshipTemporalSupport(
  semantics: string | undefined,
  windowDays: number,
): string {
  switch (semantics) {
    case "event":
      return `Events in the applied ${windowDays}-day window`;
    case "state_at":
      return "State established on or before retrieval time";
    case "interval":
      return `Validity interval overlaps the applied ${windowDays}-day window`;
    case "current_snapshot":
      return "Current at retrieval; historical state unavailable";
    default:
      return "Temporal contract not declared";
  }
}

export function coverageLabel(
  label: string,
  value: ForensicCoverageCount | undefined,
): string {
  if (!value || value.shown == null) return `${label}: unknown`;
  return value.total == null
    ? `${label}: ${value.shown.toLocaleString()} shown · total unknown`
    : `${label}: ${value.shown.toLocaleString()} of ${value.total.toLocaleString()}`;
}

export function scopeHorizon(scope: ForensicScope | undefined): string {
  if (!scope) return "awaiting preview";
  if (scope.data_horizon != null && String(scope.data_horizon)) {
    return String(scope.data_horizon);
  }
  const sourceHorizons = (scope.sources ?? [])
    .map((source) => source.horizon)
    .filter((horizon) => horizon != null && String(horizon));
  return sourceHorizons.length
    ? [...new Set(sourceHorizons.map(String))].join(", ")
    : "not reported (use fetched time)";
}
