import { SegmentedControl } from "../../../shared/SegmentedControl";
import {
  AS_OF_SKEW_DAYS,
  chainName,
  chainsIn,
  daysBetween,
  snapshotAgeDays,
  STALE_AFTER_DAYS,
  type ChainFilter,
} from "../../model/treasuryChains";
import { fmtCount, fmtStamp } from "../../model/treasuryFormat";
import type { SummaryRow } from "../../model/treasuryRows";
import type { SpotSource } from "../../model/treasuryValue";
import type { TreasuryViewState } from "../../state/treasuryView";

// Section-level treasury controls. Every control is CLIENT-SIDE and instant:
// the datasets always carry both chains, every wallet and every token class,
// so switching chain, excluding Gnosis Ltd. or revealing hidden tokens is a
// re-derivation, never a round trip. Beside them, the provenance a figure
// needs to be read correctly: each chain's as-of date and block, staleness,
// and where prices come from.

type ChainChoice = "0" | "1" | "100";

export function TreasuryToolbar({
  view,
  onChange,
  summaries,
  hiddenCount,
  spot,
  now,
}: {
  view: TreasuryViewState;
  onChange: (patch: Partial<TreasuryViewState>) => void;
  summaries: SummaryRow[];
  /** Spam tokens held in the current chain scope. */
  hiddenCount: number;
  spot: SpotSource | null;
  now: number;
}) {
  const inScope = new Set<number>(chainsIn(view.chain));
  const shown = summaries.filter((row) => inScope.has(row.chainId));
  const asOfs = summaries.map((row) => row.asOf).filter(Boolean);
  const skew = asOfs.length > 1
    ? Math.max(...asOfs.map((a) => Math.max(...asOfs.map((b) => daysBetween(a, b) ?? 0))))
    : 0;
  const hubDates = shown.map((row) => row.hubLatestDate).filter(Boolean).sort();
  const hubDate = hubDates[0] ?? "";

  return (
    <div className="gov-trs-toolbar">
      <div className="gov-trs-toolbar__controls">
        {/* A div, not a label: a label wrapping the segmented buttons would
            forward a click on the word "Chain" to the first button (All). */}
        <div className="gov-trs-toolbar__field">
          <span>Chain</span>
          <SegmentedControl<ChainChoice>
            size="sm"
            ariaLabel="Chain"
            value={String(view.chain) as ChainChoice}
            options={[
              { value: "0", label: "All" },
              { value: "1", label: "Ethereum" },
              { value: "100", label: "Gnosis Chain" },
            ]}
            onChange={(next) => onChange({ chain: Number(next) as ChainFilter })}
          />
        </div>
        <label className="gov-trs-toggle">
          <input
            type="checkbox"
            checked={view.exLtd}
            onChange={(event) => onChange({ exLtd: event.target.checked })}
          />
          Exclude Gnosis Ltd.
        </label>
        <label
          className="gov-trs-toggle"
          title="Spam tokens (impersonations, lures, obfuscated symbols, mass airdrops) are hidden by default and never valued."
        >
          <input
            type="checkbox"
            checked={view.showHidden}
            onChange={(event) => onChange({ showHidden: event.target.checked })}
          />
          Show hidden tokens ({fmtCount(hiddenCount)})
        </label>
      </div>
      <div className="gov-trs-toolbar__meta">
        {shown.map((row) => {
          const age = snapshotAgeDays(row.asOf, now);
          const stale = age !== null && age > STALE_AFTER_DAYS;
          return (
            <span key={row.chainId} className="gov-trs-asof" title={`${chainName(row.chainId)} balances are read at a finalized block.`}>
              <strong>{chainName(row.chainId)}</strong>
              <span>as of {row.asOf || "unknown"}</span>
              {row.anchorBlock !== null ? <span>block {fmtCount(row.anchorBlock)}</span> : null}
              {stale ? <span className="gov-stale-badge">STALE {fmtCount(age)}d</span> : null}
              {row.asOfStatus === "partial" ? (
                <span className="gov-trs-chip gov-trs-chip--warn" title="Some tokens were not served on the as-of day and are carried from their latest served day (at most 7 days back).">
                  partial
                </span>
              ) : null}
              {row.asOfStatus === "no_served_snapshot" ? (
                <span className="gov-trs-chip gov-trs-chip--warn" title="Nothing was served for this chain in the last 21 days.">
                  no served snapshot
                </span>
              ) : null}
            </span>
          );
        })}
        {skew > AS_OF_SKEW_DAYS ? (
          <span
            className="gov-trs-chip gov-trs-chip--warn"
            title="The two chains' snapshots are more than a week apart: a combined figure mixes two moments."
          >
            as-of differs ({fmtCount(skew)} days)
          </span>
        ) : null}
        <span className="gov-trs-prov">
          Prices: dbt price hub{hubDate ? ` to ${hubDate}` : ""}
          {spot?.at ? ` · spot fallback captured ${fmtStamp(spot.at)}` : spot ? " · spot fallback (capture time unknown)" : ""}
        </span>
      </div>
    </div>
  );
}
