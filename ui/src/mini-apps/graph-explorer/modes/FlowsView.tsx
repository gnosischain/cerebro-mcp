// Money Trail: directional, aggregate transfer-adjacency evidence. The table
// is authoritative; a segmented Sankey-style SVG keeps received and sent
// instances separate so expansion never reads as transaction-matched custody.
//
// Every filter change is a DATA operation (full re-trace through the
// serialized loader — newest settings win). Per-node "Trace in/out" buttons
// (and double-click) run MERGE loads that extend the graph while preserving
// hop ranks; tracing through a DEX/Bridge/Privacy node overrides its
// terminal status — pushing through a mixer is the analyst's call.

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { shortAddr } from "../../../utils/format";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { DetailsPanel } from "../DetailsPanel";
import { EvidencePanel, EvidenceTrigger } from "../ForensicScopeDisclosure";
import { TokenMenu } from "../TokenMenu";
import { MoneySankey } from "../canvas/MoneySankey";
import { buildFlowGraphModel, type FlowEdgeRow } from "../model/flowLayout";
import { parseEvidenceRows } from "../model/parseRows";
import type { GraphAction, GraphLocalState } from "../state/graphReducer";
import type {
  EvidenceExpectation,
  FlowDirection,
  GraphExplorerViewState,
} from "../types";

export interface FlowsSettings {
  seeds: string[];
  direction: FlowDirection;
  hops: number;
  rangeDays: number;
  minUsd: number;
  tokens: string[];
  includeBridges: boolean;
}

interface Props {
  server: GraphExplorerViewState;
  local: GraphLocalState;
  dispatch: (action: GraphAction) => void;
  flowNodes: HydratedDataset | undefined;
  flowEdges: HydratedDataset | undefined;
  nodeEvidence: HydratedDataset | undefined;
  edgeEvidence: HydratedDataset | undefined;
  evidenceExpectation: EvidenceExpectation | null;
  /** Serialized loader (full re-trace; latest settings win). */
  requestFlows: (settings: Partial<FlowsSettings>) => void;
  /** Merge-mode per-node trace (1 hop, view filters). */
  traceFlow: (nodeId: string, direction: "out" | "in") => void;
  loading: boolean;
  loadError: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
  onBrowseInvestigate: () => void;
  onOpenTransactions: (
    edge: FlowEdgeRow,
    appliedWindow: { t0: string; t1: string; rangeDays: number },
  ) => void;
  modeSwitch?: ReactNode;
}

const RANGE_OPTIONS = [7, 30, 90, 180, 365];
const MIN_USD_DEBOUNCE_MS = 600;
const MONEY_TABLE_PAGE_SIZE = 100;

type MovementLane = "all" | "measured" | "unpriced" | "supply" | "bridge";

const DIRECTION_OPTIONS: Array<{ value: FlowDirection; label: string; title: string }> = [
  { value: "in", label: "← In", title: "Trace upstream — who funded the seeds" },
  { value: "both", label: "Both", title: "Trace both directions" },
  { value: "out", label: "Out →", title: "Trace downstream — where the money went" },
];

function fmtUsd(v: number | null): string {
  if (v == null) return "unknown";
  if (!Number.isFinite(v) || v === 0) return "$0";
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}k`;
  return `$${v.toFixed(2)}`;
}

function fmtEdgeUsd(edge: FlowEdgeRow): string {
  if (edge.amountUsd == null) return "unknown";
  const value = fmtUsd(edge.amountUsd);
  return edge.unknownUsdRows > 0 ? `known ${value}` : value;
}

export function FlowsView({
  server,
  local,
  dispatch,
  flowNodes,
  flowEdges,
  nodeEvidence,
  edgeEvidence,
  evidenceExpectation,
  requestFlows,
  traceFlow,
  loading,
  loadError,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  onBrowseInvestigate,
  onOpenTransactions,
  modeSwitch,
}: Props) {
  const flows = server.flows;
  const seeds = flows?.seeds ?? [];
  const maxHops = Math.max(1, Number(local.limits.flows_max_hops) || 4);

  const [detailsOpen, setDetailsOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceTriggerRef = useRef<HTMLButtonElement>(null);
  const [seedsOpen, setSeedsOpen] = useState(false);
  const [seedInput, setSeedInput] = useState("");
  const [minUsdText, setMinUsdText] = useState<string | null>(null);
  const [movementLane, setMovementLane] = useState<MovementLane>("all");
  const [moneyPage, setMoneyPage] = useState(0);
  const [hoveredEdgeId, setHoveredEdgeId] = useState("");
  const [tableWidth, setTableWidth] = useState(52);
  const moneyBodyRef = useRef<HTMLDivElement | null>(null);
  const minUsdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectFlowNode = (id: string) => {
    onSelectNode(id);
    setEvidenceOpen(false);
    setDetailsOpen(true);
  };
  const selectFlowEdge = (id: string) => {
    onSelectEdge(id);
    setEvidenceOpen(false);
    setDetailsOpen(true);
  };

  // A failed draft must never remain painted as if it describes the graph.
  // Roll every server-backed control back to the last echoed scope; the
  // existing inline error then offers a retry of that applied snapshot.
  useEffect(() => {
    if (!loadError || !flows) return;
    dispatch({ type: "SET_FLOWS_DIRECTION", direction: flows.direction });
    dispatch({ type: "SET_FLOWS_HOPS", hops: Number(flows.hops) || 1 });
    dispatch({ type: "SET_FLOWS_RANGE", days: Number(flows.range_days) || 1 });
    dispatch({ type: "SET_FLOWS_MIN_USD", minUsd: Number(flows.min_usd) || 0 });
    dispatch({ type: "SET_FLOWS_TOKENS", tokens: [...(flows.tokens ?? [])] });
    dispatch({ type: "SET_FLOWS_BRIDGES", on: Boolean(flows.include_bridges) });
    setMinUsdText(null);
  }, [
    dispatch,
    flows,
    loadError,
  ]);

  const { model, nodes: parsedFlowNodes, edges: parsedFlowEdges } = useMemo(
    () => buildFlowGraphModel(flowNodes?.rows, flowEdges?.rows),
    [flowNodes?.rows, flowEdges?.rows],
  );
  const hasData = model.n > 0;
  const rankedFlowEdges = useMemo(() => {
    const matchesLane = (edge: FlowEdgeRow): boolean => {
      if (movementLane === "measured") return edge.amountUsd != null;
      if (movementLane === "unpriced") return edge.amountUsd == null;
      if (movementLane === "supply") {
        return edge.edgeClass === "mint" || edge.edgeClass === "burn";
      }
      if (movementLane === "bridge") {
        return edge.edgeClass === "bridge" || edge.edgeClass === "bridge_attributed";
      }
      return true;
    };
    return [...parsedFlowEdges]
      .filter(matchesLane)
      .sort(
        (a, b) =>
          (b.amountUsd ?? Number.NEGATIVE_INFINITY) -
            (a.amountUsd ?? Number.NEGATIVE_INFINITY) ||
          b.transferCount - a.transferCount ||
          a.id.localeCompare(b.id),
      );
  }, [movementLane, parsedFlowEdges]);
  const moneyPageCount = Math.max(
    1,
    Math.ceil(rankedFlowEdges.length / MONEY_TABLE_PAGE_SIZE),
  );
  const visibleMoneyPage = Math.min(moneyPage, moneyPageCount - 1);
  const pagedFlowEdges = rankedFlowEdges.slice(
    visibleMoneyPage * MONEY_TABLE_PAGE_SIZE,
    (visibleMoneyPage + 1) * MONEY_TABLE_PAGE_SIZE,
  );

  useEffect(() => {
    setMoneyPage(0);
  }, [movementLane, flowEdges?.rows]);

  const forensicScope = (flows as unknown as { scope?: Record<string, unknown> } | undefined)
    ?.scope;
  const truncationCoverage = (forensicScope?.truncation_coverage ?? {}) as Record<
    string,
    unknown
  >;
  const budgetPerHop = Number(truncationCoverage.budget_per_hop ?? 400);
  const shownCounterparties = Number(
    truncationCoverage.shown_counterparties ??
      parsedFlowNodes.filter(
        (node) =>
          !seeds.includes(node.id) &&
          !node.flags.includes("structural_terminal") &&
          !node.flags.includes("token_contract"),
      ).length,
  );
  const totalCounterparties =
    truncationCoverage.total_counterparties == null
      ? null
      : Number(truncationCoverage.total_counterparties);
  const droppedCounterparties =
    truncationCoverage.dropped_counterparties == null
      ? null
      : Number(truncationCoverage.dropped_counterparties);
  const retainedUsd =
    truncationCoverage.retained_usd_fraction == null
      ? null
      : Number(truncationCoverage.retained_usd_fraction);
  const shownSupplyEvents = Number(
    truncationCoverage.shown_supply_event_edges ?? 0,
  );
  const shownContractEndpoints = Number(
    truncationCoverage.shown_contract_endpoint_edges ?? 0,
  );
  const coverageBasis = String(truncationCoverage.counting_basis ?? "");
  const tokenUniverse = (
    forensicScope?.token_universe ?? forensicScope?.tokenUniverse ?? {}
  ) as Record<string, unknown>;
  const appliedTokenCount =
    tokenUniverse.count == null ? null : Number(tokenUniverse.count);
  const scopeWarnings = Array.isArray(forensicScope?.warnings)
    ? forensicScope.warnings.map(String)
    : [];
  const scopeLoadError =
    forensicScope?.status === "failed"
      ? scopeWarnings[0] || "The applied Money Trail scope failed without a usable dataset."
      : null;
  // Backend source-contract/query failures arrive as a successful tool
  // response carrying scope.status=failed, not as a rejected network promise.
  // Treat both paths as visible load failures, including on the first load.
  const visibleLoadError = loadError ?? scopeLoadError;
  const controlsStale = Boolean(
    flows &&
      (loading ||
        local.flowsDirection !== flows.direction ||
        local.flowsHops !== flows.hops ||
        local.flowsRangeDays !== flows.range_days ||
        local.flowsMinUsd !== flows.min_usd ||
        local.flowsIncludeBridges !== flows.include_bridges ||
        [...local.flowsTokens].sort().join("|") !==
          [...(flows.tokens ?? [])].sort().join("|")),
  );

  // Flow-specific detail rows for the shared DetailsPanel.
  const flowNodeById = useMemo(
    () => new Map(parsedFlowNodes.map((n) => [n.id, n])),
    [parsedFlowNodes],
  );
  const flowEdgeById = useMemo(
    () => new Map(parsedFlowEdges.map((e) => [e.id, e])),
    [parsedFlowEdges],
  );
  const selNode = flowNodeById.get(local.selection.nodeId);
  const inspectorNode = selNode ?? flowNodeById.get(seeds[0] ?? "");
  const inspectorNodeIsTerminal = Boolean(
    inspectorNode?.flags.includes("structural_terminal") ||
      inspectorNode?.flags.includes("token_contract") ||
      inspectorNode?.sector.toLowerCase() === "bridges",
  );
  const extraNodeRows: Array<[string, string]> = selNode
    ? [
        ...(selNode.flags.includes("structural_terminal")
          ? ([
              [
                "supply endpoint",
                "Mint/burn endpoint — retained as evidence, excluded from counterparties, and never expanded.",
              ],
            ] as Array<[string, string]>)
          : []),
        ...(selNode.flags.includes("token_contract")
          ? ([
              [
                "⚠ contract",
                "Contract endpoint — retained as a protocol leg and not traced through. The transfer alone does not establish deposit, redeem, or burn intent.",
              ],
            ] as Array<[string, string]>)
          : []),
        ["hop rank", String(selNode.hopRank)],
        ["in", fmtUsd(selNode.inUsd)],
        ["out", fmtUsd(selNode.outUsd)],
        ...(selNode.sector ? ([["sector", selNode.sector]] as Array<[string, string]>) : []),
        ...(selNode.flags.length
          ? ([["flags", selNode.flags.join(", ")]] as Array<[string, string]>)
          : []),
        ...(selNode.firstSeen
          ? ([["active", `${selNode.firstSeen} → ${selNode.lastSeen}`]] as Array<
              [string, string]
            >)
          : []),
      ]
    : [];
  const selEdge = flowEdgeById.get(local.selection.edgeId);
  const extraEdgeRows: Array<[string, string]> = selEdge
    ? [
        ["class", selEdge.edgeClass],
        ["token", `${selEdge.symbol || "?"} (${shortAddr(selEdge.tokenAddress, 6, 4)})`],
        [
          "amount",
          selEdge.amountUsd === null
            ? `${selEdge.amount ?? "raw only"} (USD unknown)`
            : `${selEdge.amount?.toFixed(4) ?? "—"} ≈ ${fmtEdgeUsd(selEdge)}`
              + (selEdge.unknownUsdRows > 0
                ? ` · ${selEdge.unknownUsdRows} unpriced source row(s)`
                : ""),
        ],
        ["transfers", String(selEdge.transferCount)],
        ...(selEdge.firstSeen
          ? ([["window", `${selEdge.firstSeen} → ${selEdge.lastSeen}`]] as Array<
              [string, string]
            >)
          : []),
      ]
    : [];

  const submitSeed = () => {
    const addr = seedInput.trim();
    if (!addr) return;
    setSeedInput("");
    setSeedsOpen(false);
    requestFlows({ seeds: Array.from(new Set([...seeds, addr])) });
  };
  const removeSeed = (addr: string) => {
    const next = seeds.filter((s) => s !== addr);
    if (!next.length) return; // a trace needs at least one seed
    requestFlows({ seeds: next });
  };

  const onMinUsdChange = (text: string) => {
    setMinUsdText(text);
    const v = Number(text);
    if (!Number.isFinite(v) || v < 0) return;
    dispatch({ type: "SET_FLOWS_MIN_USD", minUsd: v });
    if (minUsdTimer.current) clearTimeout(minUsdTimer.current);
    minUsdTimer.current = setTimeout(() => {
      setMinUsdText(null);
      requestFlows({ minUsd: v });
    }, MIN_USD_DEBOUNCE_MS);
  };

  const updateTableWidth = (clientX: number) => {
    const rect = moneyBodyRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    const next = ((clientX - rect.left) / rect.width) * 100;
    setTableWidth(Math.max(34, Math.min(68, Math.round(next))));
  };

  const beginTableResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    updateTableWidth(event.clientX);
    const onMove = (moveEvent: PointerEvent) => updateTableWidth(moveEvent.clientX);
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  };

  const investigateSeed = server.investigate?.seed?.id ?? "";
  const counterpartySummary = [
    totalCounterparties == null
      ? `${shownCounterparties.toLocaleString()} counterparties · total unknown`
      : `${shownCounterparties.toLocaleString()}/${totalCounterparties.toLocaleString()} counterparties`,
    droppedCounterparties != null && droppedCounterparties > 0
      ? `${droppedCounterparties.toLocaleString()} dropped`
      : null,
    retainedUsd == null
      ? null
      : `${(retainedUsd * 100).toFixed(1)}% measured USD retained`,
    shownSupplyEvents > 0
      ? `${shownSupplyEvents.toLocaleString()} mint/burn edge${shownSupplyEvents === 1 ? "" : "s"}`
      : null,
    shownContractEndpoints > 0
      ? `${shownContractEndpoints.toLocaleString()} contract endpoint edge${shownContractEndpoints === 1 ? "" : "s"}`
      : null,
  ].filter(Boolean).join(" · ");
  const evidenceBound = [
    `${budgetPerHop.toLocaleString()}/hop`,
    "USD-descending",
    appliedTokenCount != null
      ? `${appliedTokenCount.toLocaleString()} applied token${appliedTokenCount === 1 ? "" : "s"}`
      : "effective-dated token whitelist",
    coverageBasis === "sum_of_per_hop_unique_counterparties" &&
    Number(flows?.hops ?? 1) > 1
      ? "per-hop admissions"
      : null,
  ].filter(Boolean).join(" · ");

  return (
    <>
      <header className="ge-topbar">
        <div className="ge-seedcell ge-flow-seedcell">
          <span className="ge-seedline-label">Money Trail</span>
          <button
            type="button"
            className="ge-seedline-addr ge-flow-seeds-btn"
            onClick={() => setSeedsOpen((v) => !v)}
            title={seeds.join("\n") || "Add seed addresses to trace"}
            aria-expanded={seedsOpen}
          >
            {seeds.length
              ? `${shortAddr(seeds[0], 8, 6)}${seeds.length > 1 ? ` +${seeds.length - 1}` : ""}`
              : "no seeds"}
            <span aria-hidden> ▾</span>
          </button>
          {seedsOpen ? (
            <div className="ge-flow-seeds-panel" role="group" aria-label="Trace seeds">
              {seeds.map((s) => (
                <div key={s} className="ge-flow-seed-row">
                  <span title={s}>{shortAddr(s, 10, 8)}</span>
                  <button
                    type="button"
                    className="ge-icon-btn"
                    onClick={() => removeSeed(s)}
                    disabled={seeds.length <= 1}
                    title={seeds.length <= 1 ? "A trace needs at least one seed" : "Remove seed"}
                  >
                    ×
                  </button>
                </div>
              ))}
              <div className="ge-flow-seed-add">
                <input
                  type="text"
                  value={seedInput}
                  placeholder="0x… add seed"
                  onChange={(e) => setSeedInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && submitSeed()}
                  aria-label="Add seed address"
                />
                <button type="button" className="ge-btn" onClick={submitSeed}>
                  Add
                </button>
              </div>
            </div>
          ) : null}
        </div>
        <details className="ge-filter-drawer">
          <summary>Filters</summary>
          <div className="ge-topbar-filters ge-flow-filters">
          <div className="ge-segment" role="tablist" aria-label="Trace direction">
            {DIRECTION_OPTIONS.map((d) => (
              <button
                key={d.value}
                type="button"
                className={local.flowsDirection === d.value ? "active" : ""}
                onClick={() => {
                  dispatch({ type: "SET_FLOWS_DIRECTION", direction: d.value });
                  requestFlows({ direction: d.value });
                }}
                title={d.title}
              >
                {d.label}
              </button>
            ))}
          </div>
          <div className="ge-stepper ge-pill" role="group" aria-label="Trace depth">
            <button
              type="button"
              onClick={() => {
                const hops = Math.max(1, local.flowsHops - 1);
                dispatch({ type: "SET_FLOWS_HOPS", hops });
                requestFlows({ hops });
              }}
              disabled={local.flowsHops <= 1}
              title="One hop shallower"
            >
              −
            </button>
            <span title={`Trace depth in hops (max ${maxHops})`}>
              {local.flowsHops} hop{local.flowsHops === 1 ? "" : "s"}
            </span>
            <button
              type="button"
              onClick={() => {
                const hops = Math.min(maxHops, local.flowsHops + 1);
                dispatch({ type: "SET_FLOWS_HOPS", hops });
                requestFlows({ hops });
              }}
              disabled={local.flowsHops >= maxHops}
              title="One hop deeper"
            >
              +
            </button>
          </div>
          <label className="ge-pill" title="Hide aggregated edges below this USD value">
            <span className="ge-pill-icon" aria-hidden>$</span>
            <input
              className="ge-flow-minusd"
              type="number"
              min={0}
              step={10}
              value={minUsdText ?? String(local.flowsMinUsd)}
              onChange={(e) => onMinUsdChange(e.target.value)}
              aria-label="Minimum USD per edge"
            />
          </label>
          <TokenMenu
            catalog={flows?.token_catalog ?? []}
            selected={local.flowsTokens}
            onChange={(tokens) => {
              dispatch({ type: "SET_FLOWS_TOKENS", tokens });
              requestFlows({ tokens });
            }}
          />
          <label className="ge-pill" title="How far back the trace reaches">
            <span className="ge-pill-icon" aria-hidden>🕑</span>
            <select
              value={local.flowsRangeDays}
              onChange={(e) => {
                const days = Number(e.target.value);
                dispatch({ type: "SET_FLOWS_RANGE", days });
                requestFlows({ rangeDays: days });
              }}
            >
              {RANGE_OPTIONS.map((d) => (
                <option key={d} value={d}>
                  {d >= 365 ? `${Math.round(d / 365)}y` : `${d}d`}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={`ge-btn ${local.flowsIncludeBridges ? "active" : ""}`}
            onClick={() => {
              const on = !local.flowsIncludeBridges;
              dispatch({ type: "SET_FLOWS_BRIDGES", on });
              requestFlows({ includeBridges: on });
            }}
            title="Annotate admitted transfers that match the validated bridge model; does not prove a destination-chain exit"
            aria-pressed={local.flowsIncludeBridges}
          >
            Bridges
          </button>
          </div>
        </details>
        <div className="ge-topbar-right">
          {controlsStale ? (
            <span className="ge-pending-chip" role="status" title={`Showing applied ${flows?.range_days ?? "?"}d ${flows?.direction ?? "out"} Money Trail`}>
              Applied results · draft pending
            </span>
          ) : null}
          <EvidenceTrigger
            scope={flows?.scope}
            datasets="Money Trail nodes and transfer edges"
            open={evidenceOpen}
            onOpen={() => {
              setDetailsOpen(false);
              setEvidenceOpen(true);
            }}
            buttonRef={evidenceTriggerRef}
          />
          <button
            type="button"
            className={`ge-icon-btn ${detailsOpen ? "active" : ""}`}
            onClick={() => {
              setEvidenceOpen(false);
              setDetailsOpen((v) => !v);
            }}
            title={detailsOpen ? "Hide details" : "Show details"}
            aria-pressed={detailsOpen}
          >
            ⓘ
          </button>
          {modeSwitch}
        </div>
      </header>

      {!hasData ? (
        <div className="ge-empty-investigate">
          <div className="ge-empty-card">
            <h2>Money Trail</h2>
            {visibleLoadError ? (
              <div className="ge-load-error" role="alert">
                <span>Money Trail load failed: {visibleLoadError}</span>
                <button
                  type="button"
                  className="ge-btn"
                  onClick={() =>
                    requestFlows(
                      seeds.length
                        ? { seeds }
                        : seedInput.trim()
                          ? { seeds: [seedInput.trim()] }
                          : {},
                    )
                  }
                >
                  Retry
                </button>
              </div>
            ) : null}
            <p>
              {loading
                ? "Tracing fund movements…"
                : "Inspect observed aggregate transfers hop by hop. The table is authoritative; the map never asserts custody continuity."}
            </p>
            {!loading ? (
              <>
                <div className="ge-flow-seed-add ge-flow-seed-add--primary">
                  <input
                    type="text"
                    value={seedInput}
                    placeholder="0x… seed address"
                    onChange={(e) => setSeedInput(e.target.value)}
                    onKeyDown={(e) =>
                      e.key === "Enter" &&
                      seedInput.trim() &&
                      requestFlows({ seeds: [seedInput.trim()] })
                    }
                    aria-label="Seed address"
                  />
                  <button
                    type="button"
                    className="ge-btn primary"
                    disabled={!seedInput.trim()}
                    onClick={() => requestFlows({ seeds: [seedInput.trim()] })}
                  >
                    Trace
                  </button>
                </div>
                {investigateSeed ? (
                  <button
                    type="button"
                    className="ge-btn"
                    onClick={() => requestFlows({ seeds: [investigateSeed] })}
                  >
                    Use current Investigate seed ({shortAddr(investigateSeed, 8, 6)})
                  </button>
                ) : (
                  <button type="button" className="ge-btn" onClick={onBrowseInvestigate}>
                    Find an address in Investigate
                  </button>
                )}
              </>
            ) : null}
          </div>
        </div>
      ) : (
        <>
        {visibleLoadError ? (
          <div className="ge-load-error" role="alert">
            <span>Money Trail load failed: {visibleLoadError}</span>
            <button type="button" className="ge-btn" onClick={() => requestFlows({})}>
              Retry applied scope
            </button>
          </div>
        ) : null}
        <div
          ref={moneyBodyRef}
          className={`ge-body ge-body--money ${
            detailsOpen ? "details-open" : "details-closed"
          }`}
          style={{ "--ge-money-table-width": `${tableWidth}%` } as CSSProperties}
        >
          <section className="ge-money-table" aria-label="Ranked money movements">
            <header>
              <div>
                <strong>Ranked movements</strong>
                <span>
                  Authoritative evidence · {rankedFlowEdges.length.toLocaleString()} movement row
                  {rankedFlowEdges.length === 1 ? "" : "s"} · page {visibleMoneyPage + 1}/
                  {moneyPageCount}
                </span>
              </div>
              <small>USD ↓</small>
            </header>
            <div className="ge-money-table__lanes" role="tablist" aria-label="Movement lane">
              {(
                [
                  ["all", "All"],
                  ["measured", "Measured USD"],
                  ["unpriced", "Unpriced"],
                  ["supply", "Mint / burn"],
                  ["bridge", "Bridge-attributed"],
                ] as Array<[MovementLane, string]>
              ).map(([lane, label]) => (
                <button
                  key={lane}
                  type="button"
                  role="tab"
                  aria-selected={movementLane === lane}
                  className={movementLane === lane ? "active" : ""}
                  onClick={() => setMovementLane(lane)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="ge-money-table__rows">
              {pagedFlowEdges.map((edge, index) => (
                <div
                  key={edge.id}
                  className={
                    local.selection.edgeId === edge.id || hoveredEdgeId === edge.id
                      ? "is-selected"
                      : ""
                  }
                  onMouseEnter={() => setHoveredEdgeId(edge.id)}
                  onMouseLeave={() => setHoveredEdgeId("")}
                >
                  <button
                    type="button"
                    className="ge-money-table__main"
                    onClick={() => selectFlowEdge(edge.id)}
                    title={`${edge.source} → ${edge.target}`}
                    aria-pressed={local.selection.edgeId === edge.id}
                  >
                    <span>{visibleMoneyPage * MONEY_TABLE_PAGE_SIZE + index + 1}</span>
                    <span>
                      <strong>{shortAddr(edge.source, 6, 4)} → {shortAddr(edge.target, 6, 4)}</strong>
                      <small>
                        {edge.edgeClass.replace(/_/g, " ")} ·{" "}
                        {edge.symbol || shortAddr(edge.tokenAddress, 6, 4)} ·{" "}
                        {edge.transferCount} transfer{edge.transferCount === 1 ? "" : "s"}
                        {edge.unknownUsdRows > 0
                          ? ` · ${edge.unknownUsdRows} unpriced source row${edge.unknownUsdRows === 1 ? "" : "s"}`
                          : ""}
                      </small>
                    </span>
                    <span>{fmtEdgeUsd(edge)}</span>
                  </button>
                  <button
                    type="button"
                    className="ge-money-table__open"
                    onClick={() =>
                      onOpenTransactions(edge, {
                        t0: flows?.t0 ?? "",
                        t1: flows?.t1 ?? "",
                        rangeDays: Number(flows?.range_days) || local.flowsRangeDays,
                      })
                    }
                  >
                    Open transactions
                  </button>
                </div>
              ))}
              {pagedFlowEdges.length === 0 ? (
                <p className="ge-money-table__empty">
                  No rows in this lane. The applied evidence remains available in the other lanes.
                </p>
              ) : null}
            </div>
            <nav className="ge-money-table__pagination" aria-label="Money movement pages">
              <button
                type="button"
                className="ge-btn"
                disabled={visibleMoneyPage === 0}
                onClick={() => setMoneyPage(Math.max(0, visibleMoneyPage - 1))}
              >
                Previous
              </button>
              <span>{visibleMoneyPage + 1} / {moneyPageCount}</span>
              <button
                type="button"
                className="ge-btn"
                disabled={visibleMoneyPage >= moneyPageCount - 1}
                onClick={() => setMoneyPage(Math.min(moneyPageCount - 1, visibleMoneyPage + 1))}
              >
                Next
              </button>
            </nav>
          </section>
          <div
            className="ge-money-resizer"
            role="separator"
            aria-label="Resize evidence table and Sankey"
            aria-orientation="vertical"
            aria-valuemin={34}
            aria-valuemax={68}
            aria-valuenow={tableWidth}
            tabIndex={0}
            title={`Evidence table width ${tableWidth}%`}
            onPointerDown={beginTableResize}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft") {
                event.preventDefault();
                setTableWidth((value) => Math.max(34, value - 2));
              } else if (event.key === "ArrowRight") {
                event.preventDefault();
                setTableWidth((value) => Math.min(68, value + 2));
              }
            }}
          >
            <span aria-hidden />
          </div>
          <main className={`ge-money-map ${controlsStale ? "is-stale" : ""}`}>
            {parsedFlowEdges.length === 0 ? (
              <div className="ge-flow-hint" role="status">
                No movements for {seeds.length > 1 ? "these seeds" : "this seed"} in
                the last {Number(flows?.range_days) || local.flowsRangeDays}d
                {Number(flows?.min_usd) > 0 ? ` above $${Number(flows?.min_usd)}` : ""}
                {(flows?.tokens ?? []).length ? " for the selected token(s)" : ""}. Widen
                the range, lower the min USD, clear the token filter, or switch direction.
              </div>
            ) : null}
            <MoneySankey
              nodes={parsedFlowNodes}
              edges={parsedFlowEdges}
              seeds={seeds}
              selectedNodeId={local.selection.nodeId}
              selectedEdgeId={local.selection.edgeId}
              hoveredEdgeId={hoveredEdgeId}
              singleTokenMode={(flows?.tokens ?? []).length === 1}
              maxCounterpartiesPerHop={40}
              onSelectNode={selectFlowNode}
              onSelectEdge={selectFlowEdge}
              onHoverEdge={setHoveredEdgeId}
              onClearSelection={onClearSelection}
            />
          </main>
          <DetailsPanel
            nodes={model.nodeRows}
            edges={model.edgeRows}
            selectedNodeId={local.selection.nodeId}
            selectedEdgeId={local.selection.edgeId}
            seedNodeId={seeds[0] ?? ""}
            nodeRoles={server.node_roles ?? {}}
            catalog={server.catalog ?? []}
            suggestions={[]}
            nodeEvidence={parseEvidenceRows(nodeEvidence?.rows)}
            edgeEvidence={parseEvidenceRows(edgeEvidence?.rows)}
            evidenceExpectation={evidenceExpectation}
            onApplyHop={() => undefined}
            onSelectNode={selectFlowNode}
            flowActions={
              inspectorNodeIsTerminal
                ? undefined
                : {
                    traceIn: (id) => traceFlow(id, "in"),
                    traceOut: (id) => traceFlow(id, "out"),
                  }
            }
            extraNodeRows={extraNodeRows}
            extraEdgeRows={extraEdgeRows}
            onClose={() => setDetailsOpen(false)}
          />
        </div>
        </>
      )}
      {evidenceOpen ? (
        <EvidencePanel
          scope={flows?.scope}
          datasets="Money Trail nodes and transfer edges"
          summary={counterpartySummary}
          bound={evidenceBound}
          onClose={() => setEvidenceOpen(false)}
          openerRef={evidenceTriggerRef}
        />
      ) : null}
    </>
  );
}
