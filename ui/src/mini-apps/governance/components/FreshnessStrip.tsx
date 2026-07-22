import type { GovFreshness, GovSourceFreshness } from "../types";

// Two independent freshness clocks per source: ingestion (the daily ingester
// run) and activity (latest created/posted timestamp in the data). Snapshot
// activity legitimately lags forum activity — they are separate sources.

function shortTs(value: string | null | undefined): string {
  if (!value) return "unknown";
  return value.replace("T", " ").replace(/:\d{2}(\.\d+)?Z?$/, "").trim();
}

function Chip({ name, clock }: { name: string; clock: GovSourceFreshness }) {
  return (
    <span className="gov-fresh-chip">
      <strong>{name}</strong>
      <span>ingested {shortTs(clock.latest_ingested_at)}</span>
      <span>· activity {shortTs(clock.latest_activity_at)}</span>
      {clock.stale && <span className="gov-stale-badge">STALE</span>}
    </span>
  );
}

function Expanded({ name, clock }: { name: string; clock: GovSourceFreshness }) {
  return (
    <div className="gov-fresh-expanded">
      <strong>
        {name}
        {clock.stale && <> <span className="gov-stale-badge">STALE &gt; 24h</span></>}
      </strong>
      <span>Latest ingestion: {shortTs(clock.latest_ingested_at)} UTC</span>
      <span>Latest activity: {shortTs(clock.latest_activity_at)} UTC</span>
    </div>
  );
}

export function FreshnessStrip({ freshness, expanded = false }: {
  freshness: GovFreshness;
  expanded?: boolean;
}) {
  if (expanded) {
    return (
      <div className="gov-freshness gov-freshness--expanded">
        <Expanded name="Snapshot (off-chain signaling)" clock={freshness.snapshot} />
        <Expanded name="Forum (forum.gnosis.io)" clock={freshness.forum} />
      </div>
    );
  }
  return (
    <div className="gov-freshness">
      <Chip name="Snapshot" clock={freshness.snapshot} />
      <Chip name="Forum" clock={freshness.forum} />
    </div>
  );
}
