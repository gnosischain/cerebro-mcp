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
};

export const FALLBACK_COLOR = "#9ca3af";

/** Bright gold — the seed must pop out of the cloud. */
export const SEED_COLOR = "#fde047";

// A stable palette for edge-by-profile coloring. Profiles are assigned a slot
// in first-seen order so the legend stays consistent within a session.
export const PROFILE_PALETTE = [
  "#60a5fa", "#f472b6", "#34d399", "#fbbf24", "#a78bfa",
  "#fb7185", "#38bdf8", "#facc15", "#c084fc", "#4ade80",
  "#f97316", "#22d3ee", "#e879f9", "#94a3b8",
];

/** "#rrggbb" → [r,g,b,a] floats in 0..1 for Cosmos. */
export function hexToRgba(hex: string, alpha = 1): [number, number, number, number] {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  return [r, g, b, alpha];
}
