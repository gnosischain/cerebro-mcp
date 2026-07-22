/** Small GIP-number chip rendered next to titles. Uses the server-derived
 * `gip_number` column — display only; all cross-source linking is server-side. */
export function GipBadge({ gip }: { gip: number | null | undefined }) {
  if (gip === null || gip === undefined || !Number.isFinite(Number(gip)) || Number(gip) <= 0) {
    return null;
  }
  return <span className="gov-gip">GIP-{Number(gip)}</span>;
}
