import { fmtInt } from "../model/format";
import {
  overlayWarningCodes, type OverlayCoverage, type TokenOverlayStats,
} from "../model/tokenOverlay";

// Provenance accounting for the token labels on screen: how many come from a
// verified indexer snapshot, how many were read from current chain state, and
// how many are still just addresses. Unobtrusive by design — it lives in the
// status line, not a banner — but it is never silent: the state indexer has
// metadata for 68 of ~3,400 tokens, so "which of these names is verified" is a
// question the app must be able to answer on sight.

export interface TokenLabelCoverageProps {
  coverage: OverlayCoverage;
  stats?: TokenOverlayStats;
  /** Warning codes carried by the last tool payload, merged with the codes
   * derived from `stats` (they agree by construction; both are read so a
   * backend that stops deriving one still surfaces it). */
  warnings?: string[];
  loading?: boolean;
  onRetry?: () => void;
}

export function TokenLabelCoverage({ coverage, stats, warnings, loading, onRetry }: TokenLabelCoverageProps) {
  if (coverage.visible === 0) return null;
  const codes = new Set([...(warnings ?? []), ...overlayWarningCodes(stats)]);
  const unavailable = codes.has("token_rpc_unavailable");
  const pending = codes.has("token_overlay_pending");
  const block = stats?.block_number ?? null;
  const detail = [
    `${fmtInt(coverage.visible)} token(s) in view`,
    `${fmtInt(coverage.fromIndexer)} labelled from the verified indexer snapshot`,
    `${fmtInt(coverage.fromChain)} from chain state${block ? ` at block ${fmtInt(block)}` : ""} (marked, not publication-verified)`,
    `${fmtInt(coverage.unlabelled)} with no readable name — shown as addresses`,
  ].join(" · ");
  return (
    <span className="plx-labelcov" title={detail}>
      <span>
        labels {fmtInt(coverage.fromIndexer)} indexed
        {coverage.fromChain > 0 && (
          <>
            {" · "}
            <span className="plx-labelcov__chain">{fmtInt(coverage.fromChain)} chain state</span>
          </>
        )}
        {coverage.unlabelled > 0 && ` · ${fmtInt(coverage.unlabelled)} address-only`}
      </span>
      {loading && <span className="plx-labelcov__note">reading chain…</span>}
      {!loading && unavailable && (
        <span className="plx-labelcov__note">
          chain state could not be read{stats?.error ? ` (${stats.error})` : ""} — unnamed tokens stay as addresses
        </span>
      )}
      {!loading && !unavailable && pending && (
        <span className="plx-labelcov__note">more tokens pending</span>
      )}
      {!loading && onRetry && (unavailable || pending) && (
        <button type="button" className="plx-labelcov__retry" onClick={onRetry}>Retry</button>
      )}
    </span>
  );
}
