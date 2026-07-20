// Canvas color constants — moved out of CosmosGraph so the WebGL renderer,
// legend, and details panel share one palette.

export const COLOR_BY_KIND: Record<string, string> = {
  address: "#6ee7b7",
  safe: "#a78bfa",
  gpay_wallet: "#fbbf24",
  circles_avatar: "#60a5fa",
  circles_wrapper: "#38bdf8",
  token: "#f472b6",
  pool: "#c084fc",
  validator: "#f97316",
  bridge: "#facc15",
  project_label: "#94a3b8",
  // Transactions-mode participant roles. `burn` and `token` must NOT read as
  // ordinary counterparties: a leg ending at either is a mint/burn/reserve
  // payout, not a payment to someone.
  burn: "#64748b",
  seed: "#fbbf24",
  // Flows-mode sector kinds (address attribution from data labels).
  bridges: "#fb923c",
  dex: "#22d3ee",
  privacy: "#f87171",
  payments: "#4ade80",
  lending: "#c084fc",
  staking: "#f97316",
  cex: "#e879f9",
};

export const FALLBACK_COLOR = "#9ca3af";

/** Bright gold — the seed must pop out of the cloud. */
export const SEED_COLOR = "#fde047";

// A stable palette for edge-by-profile coloring.
export const PROFILE_PALETTE = [
  "#4f8fc9", "#2f9d7e", "#c58b35", "#7f6bb2", "#c8656a",
  "#4f9ca8", "#9a78a8", "#70859b", "#6f9f62", "#b2764f",
  "#5d7fb8", "#3f8d8a", "#a56f86", "#7d8793",
];

/** Deterministic FNV-1a hash for relationship/profile identifiers.
 *
 * `Math.imul` keeps every multiplication in unsigned 32-bit arithmetic, so
 * the result is identical across browsers and independent of dataset order.
 */
function stableHash(value: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/** A relationship keeps the same color across filtering, ordering and reloads. */
export function colorForRelationship(id: string): string {
  return PROFILE_PALETTE[stableHash(id) % PROFILE_PALETTE.length];
}

/** "#rrggbb" → [r,g,b,a] floats in 0..1 for Cosmos. */
export function hexToRgba(hex: string, alpha = 1): [number, number, number, number] {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  return [r, g, b, alpha];
}
