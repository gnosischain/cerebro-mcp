// TS mirror of the backend quorum SQL contract:
//   QUORUM_STATUS_SQL = multiIf(quorum <= 0, 'unspecified',
//                               scores_total >= quorum, 'met', 'missed')
//   QUORUM_RATIO_SQL  = scores_total / nullIf(quorum, 0)
// Snapshot quorum is a signaling threshold — the vocabulary is
// met / missed / unspecified, NEVER passed / failed.

export type QuorumStatusKind = "met" | "missed" | "unspecified";

export interface QuorumStatus {
  status: QuorumStatusKind;
  /** scores_total / quorum; null when quorum is 0/negative/absent. */
  ratio: number | null;
}

export function quorumStatus(
  scoresTotal: number | null | undefined,
  quorum: number | null | undefined,
): QuorumStatus {
  const q = typeof quorum === "number" && Number.isFinite(quorum) ? quorum : 0;
  if (q <= 0) return { status: "unspecified", ratio: null };
  const total = typeof scoresTotal === "number" && Number.isFinite(scoresTotal) ? scoresTotal : 0;
  return { status: total >= q ? "met" : "missed", ratio: total / q };
}
