// Order-types facet (client-side view over the `orders` server section;
// groups types / programmatic / class_quality — each individually gated).
//
// Everything here describes the OBSERVED orderbook subset (~78K orders,
// limit-heavy, skewed recent), never all CoW orders — the per-dataset docs
// carry the full caveat. 'unknown' / 'unresolved' / 'untagged' classes are
// ALWAYS rendered, never dropped: hiding the residual bucket would overstate
// classification coverage.

import { useMemo, useState } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { rowsToObjects } from "../../shared/rowDataset";
import { stackedSeriesOption } from "../model/chartOptions";
import { InfoPopover } from "../components/InfoPopover";
import { KpiTile } from "../components/KpiTile";
import {
  ChartSection,
  CoverageInfo,
  GroupGate,
  SegmentedToggle,
  Table,
  formatNumber,
  toDataset,
  type SectionProps,
} from "./SectionViews";

type Row = Record<string, unknown>;

export interface ClassSummary {
  orderClass: string;
  orders: number;
  owners: number;
  fulfilled: number;
  openNow: number;
  partiallyFillable: number;
  /** Share of all observed orders (0..1). */
  share: number;
  /** fulfilled / orders (0..1); null when no orders. */
  fillRate: number | null;
}

/** Aggregate order_type_summary rows (per chain x class) over chains into
 * one row per class, orders-descending. Every class present in the data is
 * kept — including 'unknown'-style residuals. */
export function classSummaries(rows: Row[]): ClassSummary[] {
  const byClass = new Map<string, { orders: number; owners: number; fulfilled: number; openNow: number; partiallyFillable: number }>();
  let total = 0;
  for (const row of rows) {
    const orderClass = String(row.order_class ?? "") || "unknown";
    const entry = byClass.get(orderClass) ?? { orders: 0, owners: 0, fulfilled: 0, openNow: 0, partiallyFillable: 0 };
    const orders = Number(row.order_count ?? 0);
    entry.orders += orders;
    // owners are per-chain distincts; the cross-chain sum can overcount and
    // is presented as such (per-network owner sums) in the tiles.
    entry.owners += Number(row.owners ?? 0);
    entry.fulfilled += Number(row.fulfilled ?? 0);
    entry.openNow += Number(row.open_now ?? 0);
    entry.partiallyFillable += Number(row.partially_fillable_count ?? 0);
    total += orders;
    byClass.set(orderClass, entry);
  }
  return [...byClass.entries()]
    .map(([orderClass, entry]) => ({
      orderClass,
      ...entry,
      share: total > 0 ? entry.orders / total : 0,
      fillRate: entry.orders > 0 ? entry.fulfilled / entry.orders : null,
    }))
    .sort((a, b) => b.orders - a.orders || a.orderClass.localeCompare(b.orderClass));
}

/** Share of observed orders whose app-data document resolved to ANY class tag
 * bucket other than 'unresolved' (0..1) — the "Classified: X%" coverage chip.
 * 'untagged' counts as classified (the doc exists; it just carries no tag).
 * Null when there are no appdata rows to judge from. */
export function classifiedShare(rows: Row[]): number | null {
  let total = 0;
  let classified = 0;
  for (const row of rows) {
    const orders = Number(row.orders ?? 0);
    if (!Number.isFinite(orders)) continue;
    total += orders;
    if (String(row.order_class ?? "") !== "unresolved") classified += orders;
  }
  if (total <= 0) return null;
  return classified / total;
}

//: Semantic ordering for surplus bands — the server orders alphabetically,
//: which interleaves the negative/positive bands. Unknown always lands last.
const SURPLUS_BUCKET_ORDER = [
  "< -50 bps", "-50-0 bps", "0-10 bps", "10-50 bps", "50-200 bps", "> 200 bps", "unknown",
];

export function orderSurplusRows(rows: Row[]): Row[] {
  const rank = (row: Row) => {
    const index = SURPLUS_BUCKET_ORDER.indexOf(String(row.surplus_bucket ?? ""));
    return index === -1 ? SURPLUS_BUCKET_ORDER.length : index;
  };
  return [...rows].sort((a, b) => rank(a) - rank(b));
}

export function OrderTypesSection(props: SectionProps) {
  const [trendMode, setTrendMode] = useState<"absolute" | "share">("absolute");
  const allNetworks = props.state.chain_id === 0;

  const summaryHydrated = props.hydrated.order_type_summary;
  const summaryRows = useMemo(() => rowsToObjects(toDataset(summaryHydrated)), [summaryHydrated]);
  const classes = useMemo(() => classSummaries(summaryRows), [summaryRows]);

  const appdataHydrated = props.hydrated.appdata_order_classes;
  const appdataRows = useMemo(() => rowsToObjects(toDataset(appdataHydrated)), [appdataHydrated]);
  const classified = useMemo(() => classifiedShare(appdataRows), [appdataRows]);

  const trendHydrated = props.hydrated.order_type_trend;
  const trendRows = useMemo(() => rowsToObjects(toDataset(trendHydrated)), [trendHydrated]);
  const trendSpec = useMemo(
    () => stackedSeriesOption(trendRows, {
      xField: "bucket",
      valueField: "order_count",
      seriesField: "order_class",
      mode: trendMode,
      kind: "area",
    }),
    [trendRows, trendMode],
  );

  const conditionalHydrated = props.hydrated.conditional_order_activity;
  const conditionalRows = useMemo(() => rowsToObjects(toDataset(conditionalHydrated)), [conditionalHydrated]);
  const conditionalSpec = useMemo(
    () => stackedSeriesOption(conditionalRows, {
      xField: "bucket",
      valueField: "events",
      seriesField: "event_type",
      kind: "bar",
    }),
    [conditionalRows],
  );

  const surplusHydrated = props.hydrated.surplus_by_class;
  const surplusRows = useMemo(() => orderSurplusRows(rowsToObjects(toDataset(surplusHydrated))), [surplusHydrated]);
  const surplusSpec = useMemo(
    () => stackedSeriesOption(surplusRows, {
      xField: "surplus_bucket",
      valueField: "fills",
      seriesField: "order_class",
      kind: "bar",
    }),
    [surplusRows],
  );
  const surplusAvailable = Boolean(props.descriptors.surplus_by_class);

  return (
    <>
      <div className="cow-inline-info">
        <InfoPopover label="Coverage caveat — observed subset only">
          <strong>Partial orderbook subset.</strong> The orderbook sync covers
          ~78K observed orders (limit-heavy, skewed to recent years) out of
          12M+ settled fills — every class mix on this page describes the
          observed subset, never all CoW orders.
        </InfoPopover>
      </div>
      <GroupGate props={props} group="types">
        <div className="cow-kpi-head">
          <div className="cow-kpi-tiles">
            {classes.length === 0 && <KpiTile label="Observed orders" value="—" />}
            {classes.map((entry) => (
              <KpiTile
                key={entry.orderClass}
                label={`${entry.orderClass} orders`}
                value={formatNumber(entry.orders)}
                delta={`${(entry.share * 100).toFixed(1)}% of observed${entry.fillRate !== null ? ` · ${(entry.fillRate * 100).toFixed(1)}% filled` : ""}`}
              />
            ))}
          </div>
          <div className="cow-kpi-head__meta">
            {classified !== null && (
              <span
                className="cow-chip"
                title="Share of observed orders whose app-data document resolved (order_class != 'unresolved'). From appdata_order_classes."
              >
                Classified: {(classified * 100).toFixed(1)}%
              </span>
            )}
            <CoverageInfo descriptor={props.descriptors.order_type_summary} label="KPI methodology" />
          </div>
        </div>
        <ChartSection
          datasetKey="order_type_trend"
          title={trendMode === "share" ? "Order-class share over time" : "Orders created by class over time"}
          props={props}
          actions={(
            <SegmentedToggle
              value={trendMode}
              options={[{ value: "absolute", label: "Absolute" }, { value: "share", label: "Share %" }]}
              onChange={setTrendMode}
              label="Class trend mode"
            />
          )}
        >
          {trendRows.length > 0
            ? <ChartCard renderer="svg" chartId="cow-order-type-trend" hideId spec={trendSpec} />
            : <div className="cow-empty">No observed orders created in this indexed window.</div>}
        </ChartSection>
      </GroupGate>
      <GroupGate props={props} group="programmatic">
        <div className="cow-grid-2">
          <ChartSection datasetKey="conditional_order_activity" title="Programmatic order activity (ComposableCoW events)" props={props}>
            {conditionalRows.length > 0
              ? <ChartCard renderer="svg" chartId="cow-conditional-activity" hideId spec={conditionalSpec} />
              : <div className="cow-empty">No programmatic (ComposableCoW) lifecycle events in this indexed window.</div>}
          </ChartSection>
          <Table datasetKey="appdata_order_classes" title="App-data orderClass tags (incl. unresolved / untagged)" props={props} />
        </div>
      </GroupGate>
      <GroupGate props={props} group="class_quality">
        <div className="cow-grid-2">
          {surplusAvailable ? (
            <ChartSection datasetKey="surplus_by_class" title="Surplus vs limit (bps) by order class" props={props}>
              {surplusRows.length > 0
                ? <ChartCard renderer="svg" chartId="cow-surplus-by-class" hideId spec={surplusSpec} />
                : <div className="cow-empty">No fills with resolvable order classes in the analytical window.</div>}
            </ChartSection>
          ) : (
            <div className="cow-empty">
              {allNetworks
                ? "Surplus-by-class joins fills to orders per network — select a single network to load it."
                : "Surplus-by-class has not loaded for this network yet."}
            </div>
          )}
          <Table datasetKey="order_flavor_mix" title="Order flavor mix (kind × signing scheme × partial fill)" props={props} />
        </div>
        {surplusAvailable && (
          <div className="cow-note">
            Execution-quality joins are single-network and cover at most the
            last 90 indexed days (see each dataset&apos;s info popover).
          </div>
        )}
      </GroupGate>
      <GroupGate props={props} group="types">
        <Table datasetKey="order_type_summary" title="Order classes per network" props={props} />
      </GroupGate>
    </>
  );
}
