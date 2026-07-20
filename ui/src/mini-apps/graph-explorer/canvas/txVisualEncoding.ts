export interface TxVisualNode {
  role?: string;
  flags?: string[];
}

const TOKEN_PALETTE = [
  "#2563eb",
  "#7c3aed",
  "#db2777",
  "#dc2626",
  "#d97706",
  "#059669",
  "#0891b2",
  "#4f46e5",
] as const;

/** Stable across row ordering and browser sessions. */
export function colorForTxToken(tokenAddress: string | null | undefined): string {
  const value = (tokenAddress || "unknown-token").toLowerCase();
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return TOKEN_PALETTE[(hash >>> 0) % TOKEN_PALETTE.length];
}

export type TxNodeVisualRole = "participant" | "seed" | "token" | "burn";

export function txNodeVisualRole(node: TxVisualNode): TxNodeVisualRole {
  if (node.role === "burn" || node.flags?.includes("burn_address")) return "burn";
  if (node.role === "token" || node.flags?.includes("token_contract")) return "token";
  if (node.role === "seed" || node.flags?.includes("seed")) return "seed";
  return "participant";
}

export function colorForTxNode(node: TxVisualNode): string {
  switch (txNodeVisualRole(node)) {
    case "burn": return "#fee2e2";
    case "token": return "#ede9fe";
    case "seed": return "#dbeafe";
    default: return "#f8fafc";
  }
}

export const TX_NODE_ROLE_LABEL: Record<TxNodeVisualRole, string> = {
  participant: "Participant",
  seed: "Seed",
  token: "Token contract",
  burn: "Burn / structural terminal",
};

