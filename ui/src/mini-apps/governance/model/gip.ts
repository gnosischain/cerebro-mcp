// GIP-number extraction — the FROZEN shared pattern, verbatim across the
// stack. SQL side: (?i)\bGIP[\s-]?0*([0-9]+). No digit cap, no trailing
// boundary; `\b` before GIP rejects AGIP-5 etc. Display badges only — all
// cross-source linking happens server-side with the same pattern.

const GIP_RE = /\bGIP[\s-]?0*([0-9]+)/i;

export function extractGip(title: string): number | null {
  const match = GIP_RE.exec(title ?? "");
  if (!match) return null;
  const n = Number(match[1]);
  return Number.isFinite(n) ? n : null;
}
