// Money Trail → Over time. The narrative table is the primary forensic
// surface; the legacy timeline graph remains available as a collapsible
// spatial overview. Cursor/playing/speed stay client-local.

import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { shortAddr } from "../../../utils/format";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { DetailsPanel } from "../DetailsPanel";
import { FilterDrawer } from "../FilterDrawer";
import { EvidencePanel, EvidenceTrigger } from "../ForensicScopeDisclosure";
import { GraphCanvas } from "../canvas/GraphCanvas";
import {
  buildGraphModel,
  parseEdgeRows,
  parseEvidenceRows,
  parseNodeRows,
} from "../model/parseRows";
import { dedupeTimelineEdges } from "../model/timelineIndex";
import { useTimelineFilter } from "../state/useTimelineFilter";
import type { GraphAction, GraphLocalState } from "../state/graphReducer";
import type {
  EvidenceExpectation,
  FlowDirection,
  ForensicScope,
  GraphExplorerViewState,
  TimelineGrain,
} from "../types";

export interface TimelineSettings {
  /** Legacy wire compatibility. Over time no longer presents profile controls. */
  profiles: string[];
  /** Applied Money Trail subjects. Supplying them makes a legacy Timeline
   * deep link self-contained even before a Trail dataset has been loaded. */
  seeds: string[];
  direction: FlowDirection;
  tokens: string[];
  minUsd: number;
  grain: TimelineGrain;
  rangeDays: number;
}

interface Props {
  server: GraphExplorerViewState;
  local: GraphLocalState;
  dispatch: (action: GraphAction) => void;
  timelineNodes: HydratedDataset | undefined;
  timelineEdges: HydratedDataset | undefined;
  /** Optional during rollout; GraphExplorerApp should pass timeline_narrative. */
  timelineNarrative?: HydratedDataset;
  nodeEvidence: HydratedDataset | undefined;
  edgeEvidence: HydratedDataset | undefined;
  evidenceExpectation: EvidenceExpectation | null;
  /** Serialized loader — latest requested settings win (owned by the app). */
  requestTimeline: (settings: Partial<TimelineSettings>) => void;
  loading: boolean;
  loadError: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
  /** Preferred callback for the empty state. */
  onBrowseMoneyTrail?: () => void;
  /** Legacy alias retained until GraphExplorerApp routes the callback to flows. */
  onBrowseInvestigate?: () => void;
  modeSwitch?: ReactNode;
}

const WINDOW_OPTIONS = [1, 2, 4, 8, 12];
const NARRATIVE_PAGE_SIZE = 100;
const NARRATIVE_CHANGES = new Set([
  "first_observed",
  "increased",
  "decreased",
  "not_observed",
  // Cached Revision 2 datasets remain readable after a hot bundle reload.
  "new",
  "disappeared",
]);

type UnknownRecord = Record<string, unknown>;
export type TimelineNarrativeChange =
  | "first_observed"
  | "increased"
  | "decreased"
  | "not_observed";

export interface TimelineNarrativeRow {
  id: string;
  bucket: string;
  direction: "in" | "out" | null;
  eventKind: "mint" | "burn" | "transfer";
  counterpartyId: string;
  counterpartyLabel: string | null;
  tokenAddress: string | null;
  tokenSymbol: string | null;
  profile: string | null;
  change: TimelineNarrativeChange;
  rawAmount: string | null;
  normalizedAmount: number | null;
  transferCount: number;
  previousTokenAmount: number | null;
  currentTokenAmount: number | null;
  deltaTokenAmount: number | null;
  previousKnownUsd: number | null;
  currentKnownUsd: number | null;
  deltaKnownUsd: number | null;
  priceCoverage: number | null;
  volumeDrivenUsdEffect: number | null;
  priceDrivenUsdEffect: number | null;
  scopeId: string | null;
}

function record(value: unknown): UnknownRecord | null {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function stringValue(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function finiteNumber(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function columnIndex(
  columns: string[] | undefined,
  names: string[],
  fallback: number,
): number {
  if (!columns?.length) return fallback;
  const normalized = columns.map((column) => column.toLowerCase());
  for (const name of names) {
    const found = normalized.indexOf(name);
    if (found >= 0) return found;
  }
  return fallback;
}

export function parseTimelineNarrativeRows(
  rows: unknown[][] | undefined,
  columns?: string[],
): TimelineNarrativeRow[] {
  const hasNamedColumns = Boolean(columns?.length);
  const richContract = Boolean(
    columns?.some((column) => column.toLowerCase() === "event_kind"),
  );

  return (rows ?? []).flatMap<TimelineNarrativeRow>((raw, index): TimelineNarrativeRow[] => {
    if (richContract || (!hasNamedColumns && raw.length >= 21)) {
      const at = (names: string[], fallback: number) =>
        raw[columnIndex(columns, names, fallback)];
      const bucket = stringValue(at(["bucket", "bucket_start"], 0));
      const counterpartyId = stringValue(at(["counterparty_id"], 3));
      const rawChange = String(at(["change"], 19) ?? "").toLowerCase();
      const change = rawChange === "new"
        ? "first_observed"
        : rawChange === "disappeared"
          ? "not_observed"
          : rawChange;
      if (
        !bucket ||
        !counterpartyId ||
        !["first_observed", "increased", "decreased", "not_observed"].includes(change)
      ) return [];
      const rawDirection = String(at(["direction"], 1) ?? "").toLowerCase();
      const rawEvent = String(at(["event_kind"], 2) ?? "transfer").toLowerCase();
      const rawAmountValue = at(["raw_amount"], 7);
      const tokenAddress = stringValue(at(["token_address", "token"], 5));
      return [{
        id: [bucket, rawDirection, rawEvent, counterpartyId, tokenAddress, change, index]
          .filter(Boolean)
          .join(":"),
        bucket,
        direction: rawDirection === "in" || rawDirection === "out"
          ? rawDirection
          : null,
        eventKind: rawEvent === "mint" || rawEvent === "burn"
          ? rawEvent
          : "transfer",
        counterpartyId,
        counterpartyLabel: stringValue(at(["counterparty_label"], 4)),
        tokenAddress,
        tokenSymbol: stringValue(at(["token_symbol", "symbol"], 6)),
        profile: null,
        change: change as TimelineNarrativeChange,
        rawAmount: rawAmountValue == null || rawAmountValue === ""
          ? null
          : String(rawAmountValue),
        normalizedAmount: finiteNumber(at(["normalized_amount"], 8)),
        transferCount: finiteNumber(at(["transfer_count"], 9)) ?? 0,
        previousTokenAmount: finiteNumber(at(["previous_token_amount"], 10)),
        currentTokenAmount: finiteNumber(at(["current_token_amount"], 11)),
        deltaTokenAmount: finiteNumber(at(["delta_token_amount"], 12)),
        previousKnownUsd: finiteNumber(at(["previous_known_usd"], 13)),
        currentKnownUsd: finiteNumber(at(["current_known_usd"], 14)),
        deltaKnownUsd: finiteNumber(at(["delta_known_usd"], 15)),
        priceCoverage: finiteNumber(at(["price_coverage"], 16)),
        volumeDrivenUsdEffect: finiteNumber(at(["volume_driven_usd_effect"], 17)),
        priceDrivenUsdEffect: finiteNumber(at(["price_driven_usd_effect"], 18)),
        scopeId: stringValue(at(["scope_id"], 20)),
      }];
    }

    // Revision 2 compatibility. These rows carried only USD values, so token
    // quantity and price-effect fields stay unknown rather than being inferred.
    const expandedFallback =
      !hasNamedColumns && raw.length >= 8 && NARRATIVE_CHANGES.has(String(raw[4]));
    const bucketIndex = columnIndex(columns, ["bucket", "bucket_start"], 0);
    const profileIndex = columnIndex(
      columns,
      ["profile", "series"],
      expandedFallback ? 1 : -1,
    );
    const counterpartyIndex = columnIndex(
      columns,
      ["counterparty_id", "counterparty"],
      expandedFallback ? 2 : 1,
    );
    const tokenIndex = columnIndex(
      columns,
      ["token_address", "token"],
      expandedFallback ? 3 : 2,
    );
    const changeIndex = columnIndex(
      columns,
      ["change"],
      expandedFallback ? 4 : 3,
    );
    const previousIndex = columnIndex(
      columns,
      ["previous_usd", "previous_value"],
      expandedFallback ? 5 : 4,
    );
    const currentIndex = columnIndex(
      columns,
      ["current_usd", "current_value"],
      expandedFallback ? 6 : 5,
    );
    const deltaIndex = columnIndex(
      columns,
      ["delta_usd", "delta_value"],
      expandedFallback ? 7 : 6,
    );
    const scopeIndex = columnIndex(
      columns,
      ["scope_id"],
      expandedFallback ? 8 : -1,
    );

    const bucket = stringValue(raw[bucketIndex]);
    const counterpartyId = stringValue(raw[counterpartyIndex]);
    const change = String(raw[changeIndex] ?? "").toLowerCase();
    if (!bucket || !counterpartyId || !NARRATIVE_CHANGES.has(change)) return [];

    const profile = profileIndex >= 0 ? stringValue(raw[profileIndex]) : null;
    const tokenAddress = stringValue(raw[tokenIndex]);
    const scopeId = scopeIndex >= 0 ? stringValue(raw[scopeIndex]) : null;
    const normalizedChange = change === "new"
      ? "first_observed"
      : change === "disappeared"
        ? "not_observed"
        : change;

    return [{
      id: [bucket, counterpartyId, tokenAddress, profile, normalizedChange, index]
        .filter(Boolean)
        .join(":"),
      bucket,
      direction: null,
      eventKind: "transfer",
      counterpartyId,
      counterpartyLabel: null,
      tokenAddress,
      tokenSymbol: null,
      profile,
      change: normalizedChange as TimelineNarrativeChange,
      rawAmount: null,
      normalizedAmount: null,
      transferCount: 0,
      previousTokenAmount: null,
      currentTokenAmount: null,
      deltaTokenAmount: null,
      previousKnownUsd: finiteNumber(raw[previousIndex]),
      currentKnownUsd: finiteNumber(raw[currentIndex]),
      deltaKnownUsd: finiteNumber(raw[deltaIndex]),
      priceCoverage: null,
      volumeDrivenUsdEffect: null,
      priceDrivenUsdEffect: null,
      scopeId,
    }];
  });
}

function formatNumber(value: number | null, signed = false): string {
  if (value == null) return "unknown";
  const sign = signed && value > 0 ? "+" : value < 0 ? "−" : "";
  const absolute = Math.abs(value);
  const amount = absolute.toLocaleString(undefined, {
    maximumFractionDigits: 6,
  });
  return `${sign}${amount}`;
}

function formatUsd(value: number | null, signed = false): string {
  if (value == null) return "unknown";
  const sign = signed && value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}$${Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function displayAddress(value: string): string {
  return value.startsWith("0x") && value.length > 16
    ? shortAddr(value, 8, 6)
    : value;
}

export function TimelineNarrativeTable({
  dataset,
  loading,
  onSelectCounterparty,
  parsedRows,
}: {
  dataset: HydratedDataset | undefined;
  loading: boolean;
  onSelectCounterparty: (id: string) => void;
  parsedRows?: TimelineNarrativeRow[];
}) {
  const datasetRows = useMemo(
    () => parseTimelineNarrativeRows(dataset?.rows, dataset?.columns),
    [dataset?.rows, dataset?.columns],
  );
  const rows = parsedRows ?? datasetRows;
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(rows.length / NARRATIVE_PAGE_SIZE));
  const visiblePage = Math.min(page, pageCount - 1);
  const visibleRows = rows.slice(
    visiblePage * NARRATIVE_PAGE_SIZE,
    (visiblePage + 1) * NARRATIVE_PAGE_SIZE,
  );
  useEffect(() => setPage(0), [rows[0]?.id, rows.length]);
  const phase = dataset?.phase ?? (loading ? "loading" : "idle");
  const error = dataset?.error;
  const expected = dataset?.rowsExpected;
  const hydrationCount = expected == null
    ? `${dataset?.rowsLoaded ?? rows.length} loaded`
    : `${dataset?.rowsLoaded ?? rows.length}/${expected} loaded`;

  return (
    <section className="ge-timeline-narrative" aria-labelledby="ge-over-time-title">
      <header className="ge-timeline-narrative__header">
        <div>
          <h2 id="ge-over-time-title">Direct activity over time</h2>
          <p>
            One-hop ERC-20 activity for the applied seed and window. No causal
            multi-hop claim. Mint and Burn are supply events, not counterparties.
            Aggregate trend direction remains withheld until SQL reconciliation.
          </p>
        </div>
        <span className="ge-timeline-narrative__count" aria-live="polite">
          {phase === "loading"
            ? `Loading · ${hydrationCount}`
            : `${rows.length.toLocaleString()} activity changes · page ${visiblePage + 1}/${pageCount}${
                dataset?.truncated ? " · capped" : ""
              }`}
        </span>
      </header>

      {phase === "failed" ? (
        <p className="ge-timeline-narrative__error" role="alert">
          Narrative hydration failed: {error || "unknown dataset error"}. Partial rows remain
          visible below.
        </p>
      ) : null}

      <div className="ge-timeline-narrative__scroll">
        <table>
          <caption className="sr-only">
            Changes from each previous time bucket for the applied Money Trail scope
          </caption>
          <thead>
            <tr>
              <th scope="col">Bucket</th>
              <th scope="col">Direction / event</th>
              <th scope="col">Activity state</th>
              <th scope="col">Counterparty</th>
              <th scope="col">Token</th>
              <th scope="col">Observed activity</th>
              <th scope="col">Token amount change</th>
              <th scope="col">Known USD change</th>
              <th scope="col">Price coverage</th>
              <th scope="col">USD effects</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={row.id} data-change={row.change}>
                <td>{row.bucket}</td>
                <td>
                  <span className="ge-timeline-direction">
                    {row.direction === "in" ? "← IN" : row.direction === "out" ? "OUT →" : "—"}
                  </span>{" "}
                  <span className={`ge-timeline-event ge-timeline-event--${row.eventKind}`}>
                    {row.eventKind}
                  </span>
                </td>
                <td>
                  <span className={`ge-timeline-change ge-timeline-change--${row.change}`}>
                    {row.change.replace(/_/g, " ")}
                  </span>
                </td>
                <td>
                  {row.eventKind === "transfer" ? (
                    <button
                      type="button"
                      className="ge-timeline-counterparty"
                      aria-label={`Inspect counterparty ${row.counterpartyId}`}
                      onClick={() => onSelectCounterparty(row.counterpartyId)}
                    >
                      {row.counterpartyLabel || displayAddress(row.counterpartyId)}
                    </button>
                  ) : (
                    <strong>{row.counterpartyLabel || `${row.eventKind} terminal`}</strong>
                  )}
                  <code className="ge-timeline-full-address">{row.counterpartyId}</code>
                </td>
                <td>
                  <strong>{row.tokenSymbol || row.profile || "Unknown token"}</strong>
                  <code className="ge-timeline-full-address">
                    {row.tokenAddress || "address unavailable"}
                  </code>
                </td>
                <td>
                  <span>{formatNumber(row.normalizedAmount)} normalized</span>
                  <small>{row.transferCount.toLocaleString()} transfer{row.transferCount === 1 ? "" : "s"}</small>
                  <code title="Exact aggregated base-unit amount">
                    raw {row.rawAmount ?? "unknown"}
                  </code>
                </td>
                <td>
                  <span>{formatNumber(row.previousTokenAmount)} → {formatNumber(row.currentTokenAmount)}</span>
                  <small>Δ {formatNumber(row.deltaTokenAmount, true)}</small>
                </td>
                <td>
                  <span>{formatUsd(row.previousKnownUsd)} → {formatUsd(row.currentKnownUsd)}</span>
                  <small>Δ {formatUsd(row.deltaKnownUsd, true)}</small>
                </td>
                <td>
                  {row.priceCoverage == null
                    ? "unknown"
                    : `${(row.priceCoverage * 100).toFixed(1)}%`}
                </td>
                <td>
                  <span>volume {formatUsd(row.volumeDrivenUsdEffect, true)}</span>
                  <small>price {formatUsd(row.priceDrivenUsdEffect, true)}</small>
                </td>
              </tr>
            ))}
            {!rows.length ? (
              <tr>
                <td colSpan={10} className="ge-timeline-narrative__empty">
                  {phase === "loading"
                    ? "Loading the applied scope’s narrative…"
                    : dataset
                      ? "No bucket-to-bucket changes were returned for this scope."
                      : "Narrative data has not been published for this scope."}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {rows.length > NARRATIVE_PAGE_SIZE ? (
        <footer className="ge-timeline-narrative__pagination">
          <button
            type="button"
            className="ge-btn"
            disabled={visiblePage === 0}
            onClick={() => setPage(Math.max(0, visiblePage - 1))}
          >
            Previous
          </button>
          <span>{visiblePage + 1} / {pageCount}</span>
          <button
            type="button"
            className="ge-btn"
            disabled={visiblePage >= pageCount - 1}
            onClick={() => setPage(Math.min(pageCount - 1, visiblePage + 1))}
          >
            Next
          </button>
        </footer>
      ) : null}
    </section>
  );
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(stringValue).filter((item): item is string => Boolean(item))
    : [];
}

function normalizedStrings(value: unknown): string[] {
  return stringList(value).map((item) => item.toLowerCase()).sort();
}

function sameStrings(left: unknown, right: unknown): boolean {
  const a = normalizedStrings(left);
  const b = normalizedStrings(right);
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

/** Whether published Over-time datasets still answer the currently applied
 * Money Trail scope. A new Money Trail load receives a new scope id, so node
 * budget changes invalidate here even before that budget becomes a public UI
 * control. */
export function timelineMatchesAppliedMoney(
  forensicScope: unknown,
  flows: GraphExplorerViewState["flows"],
): boolean {
  if (!flows?.seeds?.length) return true;
  const scope = record(forensicScope);
  const contract = record(scope?.money_contract);
  const flowScope = record(flows.scope);
  const flowScopeId = stringValue(flowScope?.scope_id);
  if (!contract) return false;
  return !(
    (flowScopeId && stringValue(contract.source_flow_scope_id) !== flowScopeId) ||
    !sameStrings(contract.seed_ids, flows.seeds) ||
    stringValue(contract.direction) !== flows.direction ||
    !sameStrings(contract.tokens, flows.tokens) ||
    finiteNumber(contract.min_usd) !== finiteNumber(flows.min_usd) ||
    stringValue(contract.t0) !== stringValue(flows.t0) ||
    stringValue(contract.t1) !== stringValue(flows.t1)
  );
}

export function TimelineScopeDisclosure({
  scope,
  open: controlledOpen,
  onOpen,
  onClose,
  openerRef,
}: {
  scope: unknown;
  open?: boolean;
  onOpen?: () => void;
  onClose?: () => void;
  openerRef?: RefObject<HTMLButtonElement | null>;
}) {
  const [localOpen, setLocalOpen] = useState(false);
  const value = record(scope);
  if (!value) return null;

  const verification = record(value.verification);
  const verified = stringValue(verification?.status)?.toLowerCase() === "verified";
  const coverage = record(value.coverage);
  const rows = record(coverage?.rows);
  const shown = finiteNumber(rows?.shown);
  const total = finiteNumber(rows?.total);
  const rowSummary = shown == null
    ? "changes unknown"
    : total == null
      ? `${shown.toLocaleString()} changes`
      : `${shown.toLocaleString()}/${total.toLocaleString()} changes`;
  const verificationSummary = verified
    ? `Verified · ${rowSummary} · trend interpretation is enabled`
    : `Trend claims withheld · ${rowSummary} · independent reconciliation pending`;

  const isOpen = controlledOpen ?? localOpen;
  const openPanel = onOpen ?? (() => setLocalOpen(true));
  const closePanel = onClose ?? (() => setLocalOpen(false));
  const localRef = useRef<HTMLButtonElement>(null);
  const triggerRef = openerRef ?? localRef;
  const typedScope = value as unknown as ForensicScope;
  return <>
    <EvidenceTrigger
      scope={typedScope}
      datasets="Over time universe, buckets, and narrative"
      open={isOpen}
      onOpen={openPanel}
      buttonRef={triggerRef}
    />
    {isOpen ? (
      <EvidencePanel
        scope={typedScope}
        datasets="Over time universe, buckets, and narrative"
        summary={verificationSummary}
        bound={stringValue(value.coverage_note)}
        onClose={closePanel}
        openerRef={triggerRef}
      />
    ) : null}
  </>;
}

export function TimelineView({
  server,
  local,
  dispatch,
  timelineNodes,
  timelineEdges,
  timelineNarrative,
  nodeEvidence,
  edgeEvidence,
  evidenceExpectation,
  requestTimeline,
  loading,
  loadError,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  onBrowseMoneyTrail,
  onBrowseInvestigate,
  modeSwitch,
}: Props) {
  const timeline = server.timeline;
  const anchorId = timeline?.anchor?.id ?? "";
  const anchorKind = timeline?.anchor?.kind ?? "";
  const timelineRecord = record(timeline);
  const forensicScope = timelineRecord?.forensic_scope ??
    (record(timelineRecord?.scope) ? timelineRecord?.scope : undefined);
  const forensicScopeRecord = record(forensicScope);
  const moneyContract = record(forensicScopeRecord?.money_contract);
  const appliedFlowScope = record(server.flows?.scope);
  const appliedFlowScopeId = stringValue(appliedFlowScope?.scope_id);
  const moneyScopeInvalidated = !timelineMatchesAppliedMoney(
    forensicScope,
    server.flows,
  );
  const browseMoneyTrail = onBrowseMoneyTrail ?? onBrowseInvestigate;
  const invalidationKey = moneyScopeInvalidated
    ? [
        appliedFlowScopeId,
        ...(server.flows?.seeds ?? []),
        server.flows?.direction,
        ...(server.flows?.tokens ?? []),
        server.flows?.min_usd,
        server.flows?.t0,
        server.flows?.t1,
      ].join("|")
    : "";
  const requestedInvalidationRef = useRef("");

  useEffect(() => {
    if (!moneyScopeInvalidated || loading || !invalidationKey) return;
    if (requestedInvalidationRef.current === invalidationKey) return;
    requestedInvalidationRef.current = invalidationKey;
    requestTimeline({ grain: local.timelineGrain });
  }, [
    invalidationKey,
    loading,
    local.timelineGrain,
    moneyScopeInvalidated,
    requestTimeline,
  ]);

  const [detailsOpen, setDetailsOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceTriggerRef = useRef<HTMLButtonElement>(null);
  const [canvasOpen, setCanvasOpen] = useState(false);

  const dedupedEdgeRows = useMemo(
    () => dedupeTimelineEdges(timelineEdges?.rows),
    [timelineEdges?.rows],
  );
  const model = useMemo(
    () => buildGraphModel(timelineNodes?.rows, dedupedEdgeRows, []),
    [timelineNodes?.rows, dedupedEdgeRows],
  );
  const parsedNodes = useMemo(
    () => parseNodeRows(timelineNodes?.rows),
    [timelineNodes?.rows],
  );
  const parsedEdges = useMemo(
    () => parseEdgeRows(dedupedEdgeRows),
    [dedupedEdgeRows],
  );

  const initialCursor = useMemo(() => {
    if (typeof window === "undefined") return undefined;
    const v = Number(new URLSearchParams(window.location.search).get("tcur"));
    return Number.isFinite(v) && v > 0 ? v : undefined;
  }, []);

  const filter = useTimelineFilter(
    timeline,
    timelineEdges?.rows,
    model,
    Boolean(timelineEdges?.hydrating || timelineNodes?.hydrating),
    local.timelineWindowBuckets,
    initialCursor,
  );

  useEffect(() => {
    if (filter.playing || typeof window === "undefined") return;
    const p = new URLSearchParams(window.location.search);
    if (filter.cursor > 0 && filter.cursor !== filter.maxCursor) {
      p.set("tcur", String(filter.cursor));
    } else {
      p.delete("tcur");
    }
    const qs = p.toString();
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (qs ? "?" + qs : "") + window.location.hash,
    );
  }, [filter.playing, filter.cursor, filter.maxCursor]);

  const hasGraph = model.n > 0 && !moneyScopeInvalidated;
  const narrativeRows = useMemo(
    () => moneyScopeInvalidated
      ? []
      : parseTimelineNarrativeRows(timelineNarrative?.rows, timelineNarrative?.columns),
    [moneyScopeInvalidated, timelineNarrative?.rows, timelineNarrative?.columns],
  );
  const controlsStale = Boolean(
    timeline && local.timelineGrain !== timeline.grain,
  );
  const canvasStale = loading || controlsStale || moneyScopeInvalidated;
  const appliedLabel = timeline
    ? `${timeline.grain}, ${stringValue(moneyContract?.t0) ?? "unknown"} → ${
        stringValue(moneyContract?.t1) ?? "unknown"
      }`
    : "no applied scope";
  const draftLabel = `${local.timelineGrain}, current Money Trail scope`;

  const toggleCanvas = () => {
    if (canvasOpen && filter.playing) filter.togglePlay();
    setCanvasOpen((open) => !open);
  };
  const inspectNarrativeCounterparty = (id: string) => {
    onSelectNode(id);
    setCanvasOpen(true);
    setEvidenceOpen(false);
    setDetailsOpen(true);
    // The narrative is the primary surface, but its Inspect action must reveal
    // the promised context immediately rather than updating invisible state
    // behind a collapsed graph.
    window.requestAnimationFrame(() => {
      document.getElementById("ge-timeline-overview")?.scrollIntoView({
        block: "nearest",
      });
    });
  };

  return (
    <div className="ge-mode ge-mode--timeline">
      <header className="ge-topbar">
        <div className="ge-seedcell">
          <span className="ge-seedline-label">
            Money Trail · Over time{anchorKind ? ` · ${anchorKind}` : ""}
          </span>
          <span className="ge-seedline-addr" title={anchorId}>
            {anchorId ? displayAddress(anchorId) : "no anchor"}
          </span>
        </div>
        <FilterDrawer>
          <div className="ge-segment" role="group" aria-label="Time grain">
            {(["day", "week", "month"] as const).map((grain) => (
              <button
                key={grain}
                type="button"
                className={local.timelineGrain === grain ? "active" : ""}
                onClick={() => {
                  dispatch({ type: "SET_TIMELINE_GRAIN", grain });
                  requestTimeline({ grain });
                }}
                title={`Bucket money movement by ${grain}`}
                aria-pressed={local.timelineGrain === grain}
              >
                {grain}
              </button>
            ))}
          </div>
          <span className="ge-pill" title="Over time always uses the applied Money Trail window">
            <span className="ge-pill-icon" aria-hidden>🕑</span>
            {server.flows?.t0 && server.flows?.t1
              ? `${server.flows.t0.slice(0, 10)} → ${server.flows.t1.slice(0, 10)}`
              : `${timeline?.range_days ?? local.timelineRangeDays}d compatibility window`}
          </span>
          <label className="ge-pill" title="Visible graph playback window">
            <span className="ge-pill-icon" aria-hidden>▭</span>
            <select
              aria-label="Graph playback window"
              value={local.timelineWindowBuckets}
              onChange={(event) =>
                dispatch({
                  type: "SET_TIMELINE_WINDOW",
                  buckets: Number(event.target.value),
                })
              }
            >
              {WINDOW_OPTIONS.map((window) => (
                <option key={window} value={window}>
                  {window} bucket{window === 1 ? "" : "s"}
                </option>
              ))}
            </select>
          </label>
        </FilterDrawer>
        <div className="ge-topbar-right">
          <button
            type="button"
            className={`ge-btn ${canvasOpen ? "active" : ""}`}
            onClick={toggleCanvas}
            disabled={!hasGraph}
            aria-expanded={canvasOpen}
            aria-controls="ge-timeline-overview"
          >
            {canvasOpen ? "Hide graph" : "Show graph"}
          </button>
          <TimelineScopeDisclosure
            scope={forensicScope}
            open={evidenceOpen}
            onOpen={() => {
              setDetailsOpen(false);
              setEvidenceOpen(true);
            }}
            onClose={() => setEvidenceOpen(false)}
            openerRef={evidenceTriggerRef}
          />
          <button
            type="button"
            className={`ge-icon-btn ${detailsOpen ? "active" : ""}`}
            onClick={() => {
              setEvidenceOpen(false);
              setDetailsOpen((open) => !open);
            }}
            title={detailsOpen ? "Hide details" : "Show details"}
            aria-pressed={detailsOpen}
            disabled={!canvasOpen}
          >
            ⓘ
          </button>
          {modeSwitch}
        </div>
      </header>

      {loadError ? (
        <div className="ge-timeline-load-error" role="alert">
          <span>Over time failed to load: {loadError}</span>
          <button type="button" className="ge-btn" onClick={() => requestTimeline({})}>
            Retry
          </button>
        </div>
      ) : null}

      {moneyScopeInvalidated ? (
        <div className="ge-timeline-stale" role="status">
          The applied Money Trail seed or filters changed. The prior Over time
          datasets are invalid and are not shown.
          <button
            type="button"
            className="ge-btn"
            disabled={loading}
            onClick={() => {
              requestedInvalidationRef.current = invalidationKey;
              requestTimeline({ grain: local.timelineGrain });
            }}
          >
            {loading ? "Loading matching scope…" : "Reload matching scope"}
          </button>
        </div>
      ) : null}

      {canvasStale ? (
        <div className="ge-timeline-stale" role="status">
          Showing results for <strong>{appliedLabel}</strong>.
          {loading
            ? ` Loading requested ${draftLabel}…`
            : ` Draft ${draftLabel} is not applied.`}
        </div>
      ) : null}

      <TimelineNarrativeTable
        dataset={moneyScopeInvalidated ? undefined : timelineNarrative}
        loading={loading}
        parsedRows={narrativeRows}
        onSelectCounterparty={inspectNarrativeCounterparty}
      />

      <section
        id="ge-timeline-overview"
        className="ge-timeline-overview"
        aria-labelledby="ge-timeline-overview-title"
      >
        <header className="ge-timeline-overview__header">
          <div>
            <h2 id="ge-timeline-overview-title">Graph overview</h2>
            <p>Secondary spatial view of the applied scope. The narrative above is primary.</p>
          </div>
        </header>

        {!hasGraph ? (
          <div className="ge-empty-investigate">
            <div className="ge-empty-card">
              <h3>No graph overview</h3>
              <p>
                {loading
                  ? "Loading the applied Money Trail scope…"
                  : narrativeRows.length
                    ? "The narrative is available, but no compatible graph rows were returned."
                    : "Open Money Trail, load a seed and window, then return to Over time."}
              </p>
              {!loading && !narrativeRows.length && browseMoneyTrail ? (
                <button type="button" className="ge-btn primary" onClick={browseMoneyTrail}>
                  Open Money Trail
                </button>
              ) : null}
            </div>
          </div>
        ) : canvasOpen ? (
          <div className={`ge-body ${detailsOpen ? "details-open" : "details-closed"}`}>
            <main
              className={`ge-canvas ge-timeline-canvas ${canvasStale ? "is-stale" : ""}`}
              aria-busy={canvasStale}
            >
              <GraphCanvas
                stateKey="money:over-time"
                model={model}
                selectedNodeId={local.selection.nodeId}
                selectedEdgeId={local.selection.edgeId}
                seedNodeId={anchorId}
                emptyHint="No money movement in the applied range."
                onSelectNode={onSelectNode}
                onSelectEdge={onSelectEdge}
                onExpandNode={() => undefined}
                onViewClick={onClearSelection}
                linkOverride={
                  filter.frame
                    ? { alpha: filter.frame.linkAlpha, width: filter.frame.linkWidth }
                    : undefined
                }
                pointAlphaOverride={filter.frame?.pointAlpha}
                simLabel={{ play: "▶ Layout", pause: "❚❚ Layout" }}
                stats={{
                  nodeCount: model.n,
                  edgeCount: model.edgeRows.length,
                  hopsUsed: server.investigate?.hops_used ?? 0,
                  maxHops: local.limits.max_hops,
                  activeProfileCount: timeline?.profiles?.length ?? 0,
                  catalogSize: server.catalog?.length ?? 0,
                }}
              >
                <div className="ge-scrubber" role="group" aria-label="Over time playback">
                  <span className="ge-scrubber-caption" aria-hidden>Window</span>
                  <button
                    type="button"
                    className={`ge-graph-btn ${filter.playing ? "active" : ""}`}
                    onClick={filter.togglePlay}
                    disabled={!filter.axis.length || canvasStale}
                    title={filter.playing ? "Pause playback" : "Play the window forward"}
                    aria-label={filter.playing ? "Pause playback" : "Play playback"}
                  >
                    {filter.playing ? "❚❚" : "▶"}
                  </button>
                  <input
                    type="range"
                    min={0}
                    max={filter.maxCursor}
                    value={filter.cursor}
                    onChange={(event) => filter.setCursor(Number(event.target.value))}
                    aria-label="Over time cursor"
                    disabled={canvasStale}
                  />
                  <span className="ge-scrubber-label" title="Visible time window">
                    {filter.windowLabel || "—"}
                  </span>
                  <select
                    className="ge-scrubber-speed"
                    value={filter.speed}
                    onChange={(event) => filter.setSpeed(Number(event.target.value))}
                    title="Playback speed"
                    aria-label="Playback speed"
                    disabled={canvasStale}
                  >
                    {[0.5, 1, 2, 4].map((speed) => (
                      <option key={speed} value={speed}>{speed}×</option>
                    ))}
                  </select>
                </div>
              </GraphCanvas>
            </main>
            <DetailsPanel
              nodes={parsedNodes}
              edges={parsedEdges}
              selectedNodeId={local.selection.nodeId}
              selectedEdgeId={local.selection.edgeId}
              seedNodeId={anchorId}
              nodeRoles={server.node_roles ?? {}}
              catalog={server.catalog ?? []}
              suggestions={[]}
              nodeEvidence={parseEvidenceRows(nodeEvidence?.rows)}
              edgeEvidence={parseEvidenceRows(edgeEvidence?.rows)}
              evidenceExpectation={evidenceExpectation}
              onApplyHop={() => undefined}
              onSelectNode={onSelectNode}
              onClose={() => setDetailsOpen(false)}
            />
          </div>
        ) : (
          <p className="ge-timeline-overview__collapsed">
            Graph overview collapsed. Use <strong>Show graph</strong> to inspect spatial
            relationships and playback.
          </p>
        )}
      </section>
    </div>
  );
}
