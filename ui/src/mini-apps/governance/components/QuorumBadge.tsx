import { quorumStatus } from "../model/quorum";

/** Quorum attainment chip + ratio mini-bar. Vocabulary is frozen to
 * met / missed / unspecified — Snapshot quorum is a signaling threshold,
 * never "passed"/"failed". */
export function QuorumBadge({ scoresTotal, quorum }: {
  scoresTotal: number | null | undefined;
  quorum: number | null | undefined;
}) {
  const { status, ratio } = quorumStatus(scoresTotal, quorum);
  return (
    <span className="gov-quorum" title={ratio === null ? "No quorum specified" : `scores / quorum = ${ratio.toFixed(2)}`}>
      <span className={`gov-quorum-chip gov-quorum-chip--${status}`}>{status}</span>
      {ratio !== null && (
        <span className="gov-quorum-meter" aria-hidden>
          <span style={{ width: `${Math.min(1, ratio) * 100}%` }} />
        </span>
      )}
    </span>
  );
}
