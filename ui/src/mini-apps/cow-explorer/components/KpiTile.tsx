// MaKpi-style stat card with an optional delta line, footnote, and inline
// sparkline (plain SVG — see SparkLine.tsx). Used by the Overview protocol
// KPI row and the Order-types class KPIs; visually consistent with the shared
// .ma-kpi cards but able to carry the extra affordances they cannot.

import type { ReactNode } from "react";
import { SparkLine } from "./SparkLine";

export interface KpiTileProps {
  label: string;
  value: string;
  delta?: string;
  deltaTone?: "positive" | "negative" | "neutral";
  /** Sparkline series; omitted/empty renders no spark. */
  spark?: number[];
  /** Small muted footnote (e.g. estimate disclosures). */
  note?: ReactNode;
}

export function KpiTile({ label, value, delta, deltaTone = "neutral", spark, note }: KpiTileProps) {
  return (
    <div className="ma-kpi cow-kpi-tile">
      <div className="ma-kpi-label">{label}</div>
      <div className="ma-kpi-value">{value}</div>
      {delta && <div className={`ma-kpi-delta ma-kpi-delta--${deltaTone}`}>{delta}</div>}
      {spark && spark.length > 0 && (
        <div className="cow-kpi-tile__spark">
          <SparkLine values={spark} />
        </div>
      )}
      {note && <div className="cow-kpi-tile__note">{note}</div>}
    </div>
  );
}
