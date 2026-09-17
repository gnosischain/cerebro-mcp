import type { ReactNode } from "react";

import { MaKpi, MaKpiGrid } from "../../shared/MiniAppChrome";

export interface KpiItem {
  label: string;
  value: string;
  delta?: string;
  deltaTone?: "positive" | "negative" | "neutral";
}

/** Section KPI header row with an optional trailing meta slot. */
export function KpiRow({ items, meta }: { items: KpiItem[]; meta?: ReactNode }) {
  return (
    <div className="plx-kpi-head">
      <MaKpiGrid>
        {items.map((item) => (
          <MaKpi key={item.label} label={item.label} value={item.value} delta={item.delta} deltaTone={item.deltaTone} />
        ))}
      </MaKpiGrid>
      {meta ? <div className="plx-kpi-head__meta">{meta}</div> : null}
    </div>
  );
}
