// The two chains the treasury census covers, and everything chain-shaped the
// views need: display names, snapshot age, explorer links.

export const TREASURY_CHAIN_IDS = [1, 100] as const;
export type TreasuryChainId = (typeof TREASURY_CHAIN_IDS)[number];

/** A chain filter: 0 = all chains. */
export type ChainFilter = 0 | TreasuryChainId;

export function isTreasuryChain(value: unknown): value is TreasuryChainId {
  return value === 1 || value === 100;
}

/** Chains a filter selects. */
export function chainsIn(filter: ChainFilter): TreasuryChainId[] {
  return filter === 0 ? [...TREASURY_CHAIN_IDS] : [filter];
}

/** Display name. Deliberately not `chainShortName()` from shared/chainIcons:
 * that map is sized for a 16px badge ("Gnosis") and falls back to a bare
 * number, which as a heading reads as a figure with no label. */
export function chainName(chainId: unknown): string {
  const key = String(chainId ?? "").trim();
  if (key === "1") return "Ethereum";
  if (key === "100") return "Gnosis Chain";
  return key === "" || key === "0" ? "All chains" : `Chain ${key}`;
}

/** Snapshot age past which a chain is flagged STALE. The census publishes far
 * more often than monthly, so 30 days without a snapshot means it stopped. */
export const STALE_AFTER_DAYS = 30;
/** As-of dates further apart than this are called out: figures summed across
 * the two chains then describe two different moments. */
export const AS_OF_SKEW_DAYS = 7;

const DAY_MS = 86_400_000;

/** Whole days between a snapshot's calendar day and `now`, or null when
 * unparseable. Only the YYYY-MM-DD prefix is read, as UTC: `Date.parse` on a
 * ClickHouse "2022-11-01 00:00:00" is implementation-defined and V8 reads it
 * as LOCAL time, so the same row would age differently for two viewers. */
export function snapshotAgeDays(asOf: unknown, now: number): number | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(asOf ?? "").trim());
  if (!parts) return null;
  const stamp = Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
  // A snapshot dated in the future is clock skew, not negative staleness.
  return Math.max(0, Math.floor((now - stamp) / DAY_MS));
}

function utcDay(value: string): number | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return parts ? Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])) : null;
}

/** Whole days between two calendar days, or null when either is unparseable. */
export function daysBetween(a: string, b: string): number | null {
  const left = utcDay(a);
  const right = utcDay(b);
  if (left === null || right === null) return null;
  return Math.round(Math.abs(left - right) / DAY_MS);
}

const EXPLORERS: Record<TreasuryChainId, string> = {
  1: "https://etherscan.io",
  100: "https://gnosisscan.io",
};

/** Block-explorer page for an address or token, or "" for an unknown chain
 * or a value that is not an address (never a link built from untrusted text). */
export function explorerUrl(chainId: number, kind: "address" | "token", value: string): string {
  const base = isTreasuryChain(chainId) ? EXPLORERS[chainId] : "";
  const address = String(value ?? "").trim().toLowerCase();
  if (!base || !/^0x[0-9a-f]{40}$/.test(address)) return "";
  return `${base}/${kind}/${address}`;
}
