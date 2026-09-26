import { chainName } from "../../model/treasuryChains";
import { fmtCount, fmtMonth } from "../../model/treasuryFormat";
import type { ChainIssues, MonthIssue } from "../../model/treasuryHistory";

// Says, in words, which months a chart leaves blank and which it draws from a
// partial upstream snapshot — a dashed marker alone is easy to miss, a blank
// month misread is a fabricated drawdown, and a partial month read as complete
// hides which reviewed tokens are missing from it.

/** Collapse more than this many months per chain and kind into a range. */
const LIST_LIMIT = 4;

function plural(count: number, word: string): string {
  return `${fmtCount(count)} ${word}${count === 1 ? "" : "s"}`;
}

/** What a partial month is missing: registry symbols (trusted text) + counts. */
export function partialDetail(issue: MonthIssue): string {
  const coverage = issue.coverage;
  if (!coverage) return "partial upstream";
  const symbols = coverage.unservedRegistrySymbols;
  const registry = coverage.unservedRegistryTokens ?? symbols.length;
  const other = Math.max(0, (coverage.unservedTokens ?? 0) - registry);
  const parts: string[] = [];
  if (registry > 0) {
    parts.push(`${plural(registry, "registry token")} not served${symbols.length > 0 ? ` (${symbols.join(", ")})` : ""}`);
  }
  if (other > 0) parts.push(`${plural(other, "other token")} not served`);
  return parts.length > 0 ? `partial upstream: ${parts.join(", ")}` : "partial upstream";
}

function blankReason(issue: MonthIssue): string {
  if (issue.coverage?.status === "unpublished") return "no census published";
  if (issue.coverage?.status === "gap") return "nothing served upstream";
  return "no data";
}

function months(issues: MonthIssue[]): string {
  if (issues.length > LIST_LIMIT) {
    return `${issues.length} months (${fmtMonth(issues[0].bucket)} … ${fmtMonth(issues[issues.length - 1].bucket)})`;
  }
  return issues.map((issue) => fmtMonth(issue.bucket)).join(", ");
}

export function gapNoteLines(issues: ChainIssues, chains: readonly number[]): string[] {
  const lines: string[] = [];
  for (const chainId of chains) {
    const list = issues.get(chainId) ?? [];
    const blank = list.filter((issue) => issue.status === "missing");
    const partial = list.filter((issue) => issue.status === "incomplete");
    if (blank.length > 0) {
      const reasons = [...new Set(blank.map(blankReason))];
      lines.push(`${chainName(chainId)} ${months(blank)}: ${reasons.join(" / ")} — left blank, not a dip.`);
    }
    if (partial.length > LIST_LIMIT) {
      lines.push(`${chainName(chainId)} ${months(partial)}: partial upstream — drawn from what was served.`);
    } else {
      for (const issue of partial) {
        lines.push(`${chainName(chainId)} ${fmtMonth(issue.bucket)}: ${partialDetail(issue)} — drawn from what was served.`);
      }
    }
  }
  return lines;
}

/** Tooltip notes for partial months, by bucket (for valueStackOption). */
export function partialNotes(issues: ChainIssues, chains: readonly number[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const chainId of chains) {
    for (const issue of issues.get(chainId) ?? []) {
      if (issue.status !== "incomplete") continue;
      const note = `${chainName(chainId)}: ${partialDetail(issue)}`;
      out[issue.bucket] = out[issue.bucket] ? `${out[issue.bucket]}; ${note}` : note;
    }
  }
  return out;
}

export function GapNote({ issues, chains }: { issues: ChainIssues; chains: readonly number[] }) {
  const lines = gapNoteLines(issues, chains);
  if (lines.length === 0) return null;
  return (
    <div className="gov-trs-gapnote" role="note">
      {lines.map((line) => <p key={line}>{line}</p>)}
    </div>
  );
}
