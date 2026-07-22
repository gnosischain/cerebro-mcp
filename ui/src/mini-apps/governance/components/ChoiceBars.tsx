import { leadingChoice, type ChoiceEntry } from "../model/choices";

// Per-choice horizontal bars with share %, leading-choice highlight, and a
// quorum marker under the bars: the track shows scores_total against the
// quorum threshold (marker at 100% of quorum). Vocabulary: "leading", never
// "winner"/"passed".

function fmtVp(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export function ChoiceBars({ entries, quorum, scoresTotal, rankedNote }: {
  entries: ChoiceEntry[];
  quorum: number | null;
  scoresTotal: number | null;
  /** Extra explanatory note (ranked-choice proposals). */
  rankedNote?: string;
}) {
  if (entries.length === 0) {
    return <div className="gov-empty">No choices recorded for this proposal.</div>;
  }
  const leading = leadingChoice(entries);
  const total = entries.reduce((sum, entry) => sum + (entry.score ?? 0), 0);
  const hasQuorum = quorum !== null && quorum > 0;
  const castTotal = scoresTotal ?? total;
  // The track spans max(scores_total, quorum) so both the fill and the
  // quorum marker always fit inside it.
  const trackMax = hasQuorum ? Math.max(castTotal, quorum) : castTotal;
  return (
    <div>
      <div className="gov-choices">
        {entries.map((entry) => {
          const isLeading = leading !== null && entry.index === leading.index && !leading.tie;
          const share = total > 0 && entry.score !== null ? entry.score / total : 0;
          return (
            <div key={entry.index} className="gov-choice">
              <span className={`gov-choice__label${isLeading ? " is-leading" : ""}`} title={entry.label}>
                {entry.label}
              </span>
              <span className="gov-choice__bar">
                <span className={isLeading ? "is-leading" : ""} style={{ width: `${share * 100}%` }} />
              </span>
              <span className="gov-choice__value">
                {entry.score === null ? "pending" : `${fmtVp(entry.score)} · ${(share * 100).toFixed(1)}%`}
              </span>
            </div>
          );
        })}
      </div>
      {leading?.tie && <p className="gov-caption">Two or more choices share the top score — no single leading choice.</p>}
      {rankedNote && <p className="gov-caption">{rankedNote}</p>}
      {trackMax > 0 && (
        <>
          <div className="gov-quorum-track" role="img" aria-label={hasQuorum ? `Voting power cast ${fmtVp(castTotal)} against a quorum of ${fmtVp(quorum)}` : `Voting power cast ${fmtVp(castTotal)}; no quorum specified`}>
            <span style={{ width: `${Math.min(1, castTotal / trackMax) * 100}%` }} />
            {hasQuorum && (
              <span className="gov-quorum-marker" style={{ left: `${Math.min(1, quorum / trackMax) * 100}%` }} />
            )}
          </div>
          <p className="gov-quorum-caption">
            {fmtVp(castTotal)} VP cast
            {hasQuorum ? ` · quorum threshold ${fmtVp(quorum)} (marker)` : " · no quorum specified"}
          </p>
        </>
      )}
    </div>
  );
}
