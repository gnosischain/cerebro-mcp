import { AS_OF_SKEW_DAYS, chainName, daysBetween } from "../../model/treasuryChains";
import { SPAM_REASON_COPY } from "../../model/treasuryCopy";
import { fmtCount, fmtMonth, fmtStamp, fmtUsd } from "../../model/treasuryFormat";
import type { ChainIssues } from "../../model/treasuryHistory";
import type { HoldingRow, SpamReason, SummaryRow } from "../../model/treasuryRows";
import type { TreasuryTotals } from "../../model/treasuryValue";

// What the figures on this page leave out, as COUNTS. Never token names: the
// excluded set is where the spoofs and lures live, and naming them here would
// hand them the attention the hiding exists to deny.

export interface DataNote {
  key: string;
  text: string;
  tone: "info" | "warn";
}

export function dataNotes(args: {
  chains: readonly number[];
  issues: ChainIssues;
  totals: TreasuryTotals;
  holdings: HoldingRow[];
  summaries: SummaryRow[];
  spotAt: string;
  ltdOnlyHidden: number;
}): DataNote[] {
  const notes: DataNote[] = [];
  const partsFor = (status: "missing" | "incomplete") => args.chains.flatMap((chainId) => {
    const months = (args.issues.get(chainId) ?? []).filter((issue) => issue.status === status);
    if (months.length === 0) return [];
    const shown = months.length <= 3
      ? ` (${months.map((month) => fmtMonth(month.bucket)).join(", ")})`
      : "";
    return [`${chainName(chainId)} ${months.length}${shown}`];
  });
  const blank = partsFor("missing");
  if (blank.length > 0) {
    notes.push({
      key: "gaps",
      tone: "warn",
      text: `Blank months — ${blank.join(", ")}: nothing served or published upstream; left blank in the charts, never drawn as dips.`,
    });
  }
  const partial = partsFor("incomplete");
  if (partial.length > 0) {
    notes.push({
      key: "partial",
      tone: "info",
      text: `Partial months — ${partial.join(", ")}: drawn from what was served; the missing registry tokens are named under each chart.`,
    });
  }
  const { counts } = args.totals;
  if (counts.unpriced > 0 || counts.refused > 0) {
    const refused = counts.refused > 0
      ? ` ${fmtCount(counts.refused)} spot quote${counts.refused === 1 ? " was" : "s were"} refused as implausible.`
      : "";
    notes.push({
      key: "unpriced",
      tone: "info",
      text: `${fmtCount(counts.unpriced + counts.refused)} visible token${counts.unpriced + counts.refused === 1 ? " has" : "s have"} no hub price and no usable spot quote — value unknown, not zero.${refused}`,
    });
  }
  const reasons = new Map<SpamReason, number>();
  for (const holding of args.holdings) {
    if (holding.tokenClass !== "spam") continue;
    reasons.set(holding.spamReason, (reasons.get(holding.spamReason) ?? 0) + 1);
  }
  if (counts.hidden > 0) {
    const parts = [...reasons.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([reason, count]) => `${fmtCount(count)} ${reason ? SPAM_REASON_COPY[reason].label.toLowerCase() : "flagged"}`);
    notes.push({
      key: "hidden",
      tone: "info",
      text: `Hidden and never valued: ${parts.join(" · ")}.`,
    });
  }
  if (counts.retired > 0) {
    notes.push({
      key: "retired",
      tone: "info",
      text: `${fmtCount(counts.retired)} retired mirror${counts.retired === 1 ? "" : "s"} (EURe/GBPe v1 after the 2024-08-25 migration) excluded from totals to avoid double counting.`,
    });
  }
  if (args.totals.spotUsd > 0 && args.totals.totalUsd > 0) {
    const share = args.totals.spotUsd / args.totals.totalUsd;
    notes.push({
      key: "spot",
      tone: "info",
      text: `${fmtUsd(args.totals.spotUsd)} (${(share * 100).toFixed(1)}%) of the total is valued at CoinGecko spot${args.spotAt ? ` captured ${fmtStamp(args.spotAt)}` : ""}, today only — never in history.`,
    });
  }
  if (args.ltdOnlyHidden > 0) {
    notes.push({
      key: "ltd",
      tone: "info",
      text: `${fmtCount(args.ltdOnlyHidden)} token${args.ltdOnlyHidden === 1 ? " is" : "s are"} held only by Gnosis Ltd. and hidden by the exclusion.`,
    });
  }
  const dated = args.summaries.filter((row) => row.asOf && args.chains.includes(row.chainId));
  if (dated.length > 1) {
    const skew = daysBetween(dated[0].asOf, dated[dated.length - 1].asOf) ?? 0;
    if (skew > AS_OF_SKEW_DAYS) {
      notes.push({
        key: "skew",
        tone: "warn",
        text: `The chains' snapshots are ${fmtCount(skew)} days apart (${dated.map((row) => `${chainName(row.chainId)} ${row.asOf}`).join(", ")}): combined figures mix two moments.`,
      });
    }
  }
  return notes;
}

export function DataNotes({ notes }: { notes: DataNote[] }) {
  if (notes.length === 0) {
    return <p className="gov-caption">Nothing is excluded from these figures beyond the scope note above.</p>;
  }
  return (
    <ul className="gov-trs-notes">
      {notes.map((note) => (
        <li key={note.key} className={note.tone === "warn" ? "gov-trs-notes__item is-warn" : "gov-trs-notes__item"}>
          {note.text}
        </li>
      ))}
    </ul>
  );
}
