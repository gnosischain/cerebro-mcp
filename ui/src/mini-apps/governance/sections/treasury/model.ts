import { useMemo } from "react";

import { datasetError } from "../../../shared/datasetError";
import type { DatasetDescriptor } from "../../../shared/miniAppTypes";
import type { HydrationPhase } from "../../../shared/useHydratedDatasets";
import { chainsIn, type TreasuryChainId } from "../../model/treasuryChains";
import {
  chainDataBuckets,
  historyFrame,
  sparkIndex,
  type HistoryFrame,
} from "../../model/treasuryHistory";
import {
  parseCoverage,
  parseHistory,
  parseHoldings,
  parseSummary,
  parseWallets,
  type CoverageRow,
  type HistoryRow,
  type HoldingRow,
  type SummaryRow,
  type WalletRow,
} from "../../model/treasuryRows";
import {
  assetRows,
  spotSourceFrom,
  totalsOf,
  valuationMap,
  type AssetRow,
  type SpotSource,
  type TreasuryTotals,
  type Valuation,
} from "../../model/treasuryValue";
import { walletGroups, type WalletGrouping } from "../../model/treasuryWallets";
import type { TreasuryViewState } from "../../state/treasuryView";
import { useDataset, type GovViewContext } from "../common";

// The treasury section's single derivation. Each dataset is parsed ONCE per
// load (not per tab, not per render), and every tab reads the same scope, so
// no two panels can disagree about what "All chains, excluding Gnosis Ltd."
// means.

export interface DatasetState<T> {
  rows: T;
  /** "complete" only once every page is in: charts wait for it, because the
   * hydration hook publishes partial pages and half a history reads as a
   * collapse. */
  phase: HydrationPhase;
  error: string | null;
  descriptor: DatasetDescriptor | undefined;
  /** The descriptor exists and did not fail. */
  available: boolean;
}

/** Hydration phase of a dataset, falling back to the descriptor itself when
 * nothing hydrates it (tests, dev fixtures): a descriptor whose preview holds
 * every row is complete. */
export function datasetPhase(ctx: Pick<GovViewContext, "descriptors" | "hydrated">, key: string): HydrationPhase {
  const descriptor = ctx.descriptors[key];
  if (!descriptor) return "loading";
  if (datasetError(descriptor)) return "failed";
  const hydrated = ctx.hydrated[key];
  if (hydrated && hydrated.phase !== "idle") return hydrated.phase;
  const expected = descriptor.stats?.row_count ?? descriptor.preview_rows.length;
  return !descriptor.page_token || descriptor.preview_rows.length >= expected ? "complete" : "loading";
}

function useParsed<T>(
  ctx: GovViewContext,
  key: string,
  parse: (ds: ReturnType<typeof useDataset>) => T,
): DatasetState<T> {
  const ds = useDataset(ctx, key);
  const rows = useMemo(() => parse(ds), [ds, parse]);
  const phase = datasetPhase(ctx, key);
  const descriptor = ctx.descriptors[key];
  return {
    rows,
    phase,
    error: ctx.hydrated[key]?.error ?? (descriptor ? datasetError(descriptor) || null : null),
    descriptor,
    available: Boolean(descriptor) && phase !== "failed",
  };
}

export interface TreasuryModel {
  summary: DatasetState<SummaryRow[]>;
  holdings: DatasetState<HoldingRow[]>;
  wallets: DatasetState<WalletRow[]>;
  history: DatasetState<HistoryRow[]>;
  coverage: DatasetState<CoverageRow[]>;
  spot: SpotSource | null;
  iconFor: (chainId: number, token: string) => string;
}

export function useSpotSource(ctx: GovViewContext): SpotSource | null {
  return useMemo(
    () => spotSourceFrom(ctx.state.price_overlay, ctx.state.price_overlay_at),
    [ctx.state.price_overlay, ctx.state.price_overlay_at],
  );
}

export function useIconFor(ctx: GovViewContext): (chainId: number, token: string) => string {
  const icons = ctx.state.icon_overlay;
  return useMemo(
    () => (chainId: number, token: string) => icons?.[String(chainId)]?.[token.toLowerCase()] ?? "",
    [icons],
  );
}

export function useTreasuryModel(ctx: GovViewContext): TreasuryModel {
  return {
    summary: useParsed(ctx, "treasury_summary", parseSummary),
    holdings: useParsed(ctx, "treasury_holdings", parseHoldings),
    wallets: useParsed(ctx, "treasury_by_wallet", parseWallets),
    history: useParsed(ctx, "treasury_history", parseHistory),
    coverage: useParsed(ctx, "treasury_history_coverage", parseCoverage),
    spot: useSpotSource(ctx),
    iconFor: useIconFor(ctx),
  };
}

export interface TreasuryScope {
  chains: TreasuryChainId[];
  summaries: SummaryRow[];
  /** Every holding on the chains in scope (all classes). */
  holdings: HoldingRow[];
  valuations: Map<string, Valuation>;
  totals: TreasuryTotals;
  /** Hub NAV from the summary (ex-Ltd companion when excluded); null until it
   * loads. Authoritative over any client-side sum. */
  hubNav: number | null;
  hubNavByChain: Map<number, number | null>;
  gnoUnits: number | null;
  gnoUnitsExLtd: number | null;
  /** Spam tokens on the chains in scope. */
  hiddenCount: number;
  /** Tokens held only by Gnosis Ltd. (hidden while it is excluded). */
  ltdOnly: number;
  /** Asset rows, merged across chains by asset key / one per chain. */
  assetsMerged: AssetRow[];
  assetsByChain: AssetRow[];
  /** What the Assets tab lists by default (merged when all chains are in
   * scope), and its counts by how each asset is valued. The tab badge, the
   * Overview tile and the table's "All" chip all read these. */
  assetCounts: { listed: number; hub: number; spot: number; unpriced: number; retired: number };
  wallets: WalletGrouping;
  /** Month axis + statuses over the FULL history of the chains in scope. */
  frame: HistoryFrame;
  coverageKnown: boolean;
  spark: Map<string, number[]>;
}

function sumOrNull(values: Array<number | null>): number | null {
  let total: number | null = null;
  for (const value of values) {
    if (value === null) continue;
    total = (total ?? 0) + value;
  }
  return total;
}

/** Apply the client-side view (chain, Gnosis Ltd., hidden tokens) to the
 * parsed datasets. Value stacks never depend on `showHidden`: spam has no
 * value in any grain. */
export function computeTreasuryScope(model: TreasuryModel, view: TreasuryViewState): TreasuryScope {
  const chains = chainsIn(view.chain);
  const inScope = new Set<number>(chains);
  const summaries = model.summary.rows.filter((row) => inScope.has(row.chainId));
  const scoped = model.holdings.rows.filter((row) => inScope.has(row.chainId));
  // Excluding Gnosis Ltd. drops tokens ONLY Ltd. holds (nothing left to show).
  const ltdOnlyRows = view.exLtd
    ? scoped.filter((row) => row.hasExLtd && row.unitsExLtd === 0 && (row.units ?? 0) > 0)
    : [];
  const ltdOnlyKeys = new Set(ltdOnlyRows.map((row) => row.key));
  const holdings = scoped.filter((row) => !ltdOnlyKeys.has(row.key));
  const valuations = valuationMap(holdings, model.spot, view.exLtd);
  const totals = totalsOf(holdings, valuations, view.chain);
  const hubNavByChain = new Map<number, number | null>();
  for (const row of summaries) hubNavByChain.set(row.chainId, view.exLtd ? row.navUsdExLtd : row.navUsd);
  const hubNav = summaries.length > 0 ? sumOrNull([...hubNavByChain.values()]) : null;
  const coverageKnown = model.coverage.available;
  const frame = historyFrame({
    chains,
    coverage: coverageKnown ? model.coverage.rows : null,
    dataBuckets: chainDataBuckets(model.history.rows),
  });
  const assetsMerged = assetRows(holdings, valuations, { merge: true, exLtd: view.exLtd });
  const assetsByChain = assetRows(holdings, valuations, { merge: false, exLtd: view.exLtd });
  const listed = (view.chain === 0 ? assetsMerged : assetsByChain).filter((asset) => !asset.hidden);
  const count = (...kinds: string[]) => listed.filter((asset) => kinds.includes(asset.kind)).length;
  return {
    chains,
    summaries,
    holdings,
    valuations,
    totals,
    hubNav,
    hubNavByChain,
    gnoUnits: sumOrNull(summaries.map((row) => row.gnoUnits)),
    gnoUnitsExLtd: sumOrNull(summaries.map((row) => row.gnoUnitsExLtd)),
    hiddenCount: scoped.filter((row) => row.tokenClass === "spam").length,
    ltdOnly: ltdOnlyRows.length,
    assetsMerged,
    assetsByChain,
    assetCounts: {
      listed: listed.length,
      hub: count("hub", "mixed"),
      spot: count("spot"),
      unpriced: count("unpriced", "refused"),
      retired: count("retired"),
    },
    wallets: walletGroups(model.wallets.rows, { chain: view.chain, exLtd: view.exLtd }),
    frame,
    coverageKnown,
    spark: sparkIndex(model.history.rows, frame, { chain: view.chain, exLtd: view.exLtd }),
  };
}

export function useTreasuryScope(model: TreasuryModel, view: TreasuryViewState): TreasuryScope {
  return useMemo(
    () => computeTreasuryScope(model, view),
    // Every input that can change the scope; the model's row arrays are
    // memoized per dataset load, so this recomputes on data or view changes
    // only — never on an unrelated render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      model.summary.rows, model.holdings.rows, model.wallets.rows, model.history.rows,
      model.coverage.rows, model.coverage.available, model.spot,
      view.chain, view.exLtd,
    ],
  );
}

/** Props every treasury tab receives from the section shell. */
export interface TreasuryTabProps {
  ctx: GovViewContext;
  model: TreasuryModel;
  scope: TreasuryScope;
  view: TreasuryViewState;
  update: (patch: Partial<TreasuryViewState>) => void;
  openToken: (chainId: number, token: string) => void;
  openWallet: (chainId: number, wallet: string) => void;
}

/** The asset row behind a merged key (`asset:<key>`), a bare asset key (a
 * history band) or a `chain:token` key. */
export function findAsset(scope: Pick<TreasuryScope, "assetsMerged">, key: string): AssetRow | null {
  const candidates = key.startsWith("asset:") || key.includes(":") ? [key] : [`asset:${key}`, key];
  for (const candidate of candidates) {
    const hit = scope.assetsMerged.find((row) => row.key === candidate);
    if (hit) return hit;
  }
  return null;
}

/** Open an asset: its member with the largest value (members are sorted by
 * value), i.e. the chain where most of it sits. */
export function openAsset(
  scope: Pick<TreasuryScope, "assetsMerged">,
  key: string,
  openToken: (chainId: number, token: string) => void,
): boolean {
  const asset = findAsset(scope, key);
  const lead = asset?.members[0]?.holding;
  if (!lead) return false;
  openToken(lead.chainId, lead.token);
  return true;
}
