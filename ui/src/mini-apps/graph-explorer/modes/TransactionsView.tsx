// TRANSACTIONS mode: per-transfer-leg forensics. Opens whole transactions and
// shows EVERY leg in chain order (block, transaction_index, log_index).
//
// Why a rail and not just a canvas: a node-link canvas holds one position per
// node, so it cannot draw a sequence ladder — an address appears at many log
// indices. The ordered LEG RAIL carries the sequence (the thing that makes a
// swap, a batch settlement or a drain legible); the canvas carries structure.
// The rail is the product; the canvas is the overview.
//
// No force sim — positions are deterministic, so the same transaction always
// draws the same way and a screenshot is reproducible evidence.

import { useEffect, useMemo, useRef, useState } from "react";
import { shortAddr } from "../../../utils/format";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { EvidencePanel, EvidenceTrigger } from "../ForensicScopeDisclosure";
import { GraphErrorBoundary } from "../GraphErrorBoundary";
import { GraphTableFallback } from "../GraphTableFallback";
import { TxGraphLegend } from "../canvas/TxGraphLegend";
import { TxSvgCanvas, type TxSvgTransaction } from "../canvas/TxSvgCanvas";
import {
  buildTxGraphModel,
  groupLegsByTx,
  parseTxListRows,
  type TxListRow,
} from "../model/txLayout";
import type { GraphLocalState } from "../state/graphReducer";
import type { EvidenceExpectation, GraphExplorerViewState } from "../types";
import { TxInspector } from "./TxInspector";
import "./transaction-detail.css";

export interface TxSettings {
  txHashes: string[];
  seed: string;
  counterparties: string[];
  tokens: string[];
  rangeDays: number;
  /** Optional exact discovery window, supplied by an applied Money Trail edge. */
  t0: string;
  t1: string;
  maxTxs: number;
  minUsd: number;
  /** Follow this address forward in time from (afterBlock, afterIndex). */
  expandNodeId: string;
  afterBlock: number;
  afterIndex: number;
  /** Union with the transactions already loaded instead of replacing them. */
  merge: boolean;
}

interface Props {
  /** Distinguishes transaction task state when more than one view is open. */
  viewId: string;
  server: GraphExplorerViewState;
  local: GraphLocalState;
  txNodes: HydratedDataset | undefined;
  txLegs: HydratedDataset | undefined;
  /** Address-discovery candidates. Receipt legs remain a separate dataset. */
  txList?: HydratedDataset;
  nodeEvidence: HydratedDataset | undefined;
  edgeEvidence: HydratedDataset | undefined;
  evidenceExpectation: EvidenceExpectation | null;
  requestTransactions: (settings: Partial<TxSettings>) => void;
  loading: boolean;
  loadError: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
}

const MAX_TX_OPTIONS = [25, 50, 100, 200];
const DISCOVERY_PAGE_SIZE = 25;

const TX_HASH_RE = /^0x[0-9a-fA-F]{64}$/;
const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;

interface TransactionTaskUiState {
  inputMode: "hash" | "address";
  activeTxHash: string;
  selectedLegId: string;
  selectedNodeId: string;
  txsPick: number | null;
  canvasOpen: boolean;
  detailsOpen: boolean;
  subview: TransactionSubview;
}

type TransactionSubview = "activity" | "receipt";

interface PendingTxIntent {
  settings: Partial<TxSettings>;
  label: string;
}

// GraphExplorerApp mounts only the active task. Keep this small,
// transaction-only state outside the component so a task switch does not
// silently reset which receipt the analyst was reading or erase a pending
// draft. The key includes view_id and the forensic subject, so a newly loaded
// subject cannot inherit another transaction's controls.
const TRANSACTION_UI_CACHE_LIMIT = 32;
const transactionTaskUi = new Map<string, TransactionTaskUiState>();

function stableIds(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  return [...new Set(values.map((value) => String(value).toLowerCase()))].sort();
}

function transactionSubject(
  tx: Record<string, unknown>,
  scope: Record<string, unknown>,
): string {
  const kind = String(tx.query_kind ?? "");
  const queryHashes = stableIds(tx.query_hashes);
  if (kind === "explicit_hash" && queryHashes.length) {
    return `hash:${queryHashes.join(",")}`;
  }
  const window = objectValue(scope.window);
  const windowSource = String(window.source ?? scope.window_source ?? "");
  const hashes = stableIds(tx.tx_hashes);
  if (windowSource === "ignored_for_explicit_hash" && hashes.length) {
    return `hash:${hashes.join(",")}`;
  }
  const seed = String(tx.seed ?? "").toLowerCase();
  const counterparties = stableIds(tx.counterparties).join(",");
  const tokens = stableIds(tx.tokens).join(",");
  if (seed) return `discovery:${seed}|cp:${counterparties}|tokens:${tokens}`;
  return hashes.length ? `hash:${hashes.join(",")}` : "empty";
}

function rememberTransactionUi(key: string, state: TransactionTaskUiState): void {
  if (!transactionTaskUi.has(key) && transactionTaskUi.size >= TRANSACTION_UI_CACHE_LIMIT) {
    const oldest = transactionTaskUi.keys().next().value as string | undefined;
    if (oldest) transactionTaskUi.delete(oldest);
  }
  transactionTaskUi.set(key, state);
}

/** Test isolation for the module-level task cache. */
export function resetTransactionTaskUiForTests(): void {
  transactionTaskUi.clear();
}

function fmtUsd(v: number | null): string {
  if (v == null) return "unknown";
  if (v === 0) return "$0.00";
  if (Math.abs(v) < 0.01) return "<$0.01";
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}k`;
  return `$${v.toFixed(2)}`;
}

function numeric(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function DiscoveryResults({
  rows,
  activeTxHash,
  receiptStatuses,
  onChoose,
  canLoadMore,
  loading,
  onLoadMore,
}: {
  rows: TxListRow[];
  activeTxHash: string;
  receiptStatuses: Record<string, unknown>;
  onChoose: (hash: string) => void;
  canLoadMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
}) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(rows.length / DISCOVERY_PAGE_SIZE));
  const visiblePage = Math.min(page, pageCount - 1);
  const visibleRows = rows.slice(
    visiblePage * DISCOVERY_PAGE_SIZE,
    (visiblePage + 1) * DISCOVERY_PAGE_SIZE,
  );

  useEffect(() => {
    setPage(0);
  }, [rows[0]?.txHash, rows.length]);

  if (!rows.length) return null;
  return (
    <section className="ge-tx-discovery-results" aria-label="Address activity results">
      <header>
        <div>
          <strong>Address activity results</strong>
          <span>Newest first. Select a transaction to inspect its RPC receipt.</span>
        </div>
        <span>{rows.length} loaded · page {visiblePage + 1}/{pageCount}</span>
      </header>
      <div className="ge-table-scroll" tabIndex={0}>
        <table>
          <thead>
            <tr>
              <th scope="col">Timestamp</th>
              <th scope="col">Block / tx</th>
              <th scope="col">Transaction hash</th>
              <th scope="col">Discovered ERC-20 logs</th>
              <th scope="col">Tokens</th>
              <th scope="col">Receipt evidence</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => {
              const selected = row.txHash === activeTxHash;
              const receipt = String(receiptStatuses[row.txHash] ?? "");
              return (
                <tr
                  key={row.txHash}
                  tabIndex={0}
                  className={selected ? "is-selected" : ""}
                  aria-selected={selected}
                  onClick={() => onChoose(row.txHash)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onChoose(row.txHash);
                    }
                  }}
                >
                  <td>{row.blockTimestamp || "unknown"}</td>
                  <td>{row.blockNumber.toLocaleString()} / {row.transactionIndex}</td>
                  <td><code title={row.txHash}>{row.txHash}</code></td>
                  <td>{row.legCount.toLocaleString()}</td>
                  <td>{row.tokenCount.toLocaleString()}</td>
                  <td>
                    {receipt
                      ? `RPC receipt · ${receipt}`
                      : "Receipt unavailable · partial"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <footer className="ge-tx-discovery-results__pagination">
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
        {canLoadMore ? (
          <button
            type="button"
            className="ge-btn primary"
            disabled={loading}
            onClick={onLoadMore}
          >
            {loading ? "Loading…" : "Load more history"}
          </button>
        ) : null}
      </footer>
    </section>
  );
}

/** One address in a leg row: click the address to inspect it, click ↪ to
 * follow it forward to its next transactions. Burn and token endpoints get no
 * follow button — the zero address is a sink by construction and a token
 * contract's "next transaction" is meaningless as a chain of custody. */
function AddrCell({
  address,
  node,
  burnLabel,
  roleCls,
  expanded,
  onSelect,
  onFollow,
}: {
  address: string;
  node?: { role: string };
  burnLabel: string;
  roleCls: (r?: string) => string;
  expanded: boolean;
  onSelect: (id: string) => void;
  onFollow: (id: string) => void;
}) {
  const structural = node?.role === "burn" || node?.role === "token";
  return (
    <span className="ge-tx-addr-cell">
      <button
        type="button"
        className={`ge-tx-addr${roleCls(node?.role)}`}
        title={`${address}\nClick to inspect`}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(address);
        }}
      >
        {node?.role === "burn" ? burnLabel : shortAddr(address)}
      </button>
      {!structural && (
        <button
          type="button"
          className={`ge-tx-follow${expanded ? " is-followed" : ""}`}
          title={`Follow ${shortAddr(address)} forward — load its next transactions after this one`}
          aria-label={`Follow ${address} forward`}
          onClick={(e) => {
            e.stopPropagation();
            onFollow(address);
          }}
        >
          ↪
        </button>
      )}
    </span>
  );
}

export function TransactionsView({
  viewId,
  server,
  txNodes,
  txLegs,
  txList,
  requestTransactions,
  loading,
  loadError,
}: Props) {
  const tx = server.transactions ?? {};
  const txRecord = tx as unknown as Record<string, unknown>;
  const scope = (tx.scope ?? {}) as Record<string, unknown>;
  const scopeQueryKind = String(scope.query_kind ?? txRecord.query_kind ?? "");
  const appliedQueryKind = scopeQueryKind || (
    String(objectValue(scope.window).source ?? "") === "ignored_for_explicit_hash"
      ? "explicit_hash"
      : String(tx.seed ?? "")
        ? "address_discovery"
        : ""
  );
  const appliedInputMode: "hash" | "address" =
    appliedQueryKind === "explicit_hash" || !appliedQueryKind ? "hash" : "address";
  const taskUiKey = `${viewId}:tx:${transactionSubject(
    tx as Record<string, unknown>,
    scope,
  )}`;
  const restoredUi = transactionTaskUi.get(taskUiKey);
  const [inputMode, setInputMode] = useState<"hash" | "address">(
    restoredUi?.inputMode ?? appliedInputMode,
  );
  const [input, setInput] = useState("");
  const [subview, setSubview] = useState<TransactionSubview>(
    restoredUi?.subview ?? (appliedInputMode === "address" ? "activity" : "receipt"),
  );
  const [detailsOpen, setDetailsOpen] = useState(restoredUi?.detailsOpen ?? false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceTriggerRef = useRef<HTMLButtonElement>(null);
  const [canvasOpen, setCanvasOpen] = useState(restoredUi?.canvasOpen ?? false);
  // Result admission is OPTIMISTIC. Binding it straight to server state made the
  // dropdown snap back to the old value for the several seconds the reload
  // takes, which reads as "the control is dead". Local intent wins until the
  // server echoes a different value back.
  const [txsPick, setTxsPick] = useState<number | null>(
    restoredUi?.txsPick ?? null,
  );
  const [activeTxHash, setActiveTxHash] = useState(restoredUi?.activeTxHash ?? "");
  // Transaction selection is deliberately local. `update_graph_explorer_focus`
  // resolves semantic relationship ids (`profile:source->target`); receipt leg
  // ids (`leg:hash:log`) are already authoritative attached rows and must not be
  // sent through that incompatible DBT evidence resolver.
  const [selectedLegId, setSelectedLegId] = useState(
    restoredUi?.selectedLegId ?? "",
  );
  const [selectedNodeId, setSelectedNodeId] = useState(
    restoredUi?.selectedNodeId ?? "",
  );
  const [pendingIntent, setPendingIntent] = useState<PendingTxIntent | null>(null);
  const taskUiKeyRef = useRef(taskUiKey);
  const restoringTaskUiRef = useRef(false);
  const overviewRef = useRef<HTMLElement | null>(null);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const hasSelection = Boolean(selectedNodeId || selectedLegId);

  const { legs, nodes } = useMemo(
    () => buildTxGraphModel(txNodes?.rows, txLegs?.rows),
    [txNodes?.rows, txLegs?.rows],
  );
  const groups = useMemo(() => groupLegsByTx(legs), [legs]);
  const discoveryRows = useMemo(
    () => parseTxListRows(txList?.rows),
    [txList?.rows],
  );
  const loadedHashes = useMemo(
    () => stableIds(txRecord.result_hashes ?? tx.tx_hashes ?? []),
    [tx.tx_hashes, txRecord.result_hashes],
  );
  const scopeWindow = objectValue(scope.window);
  const windowSource = String(scopeWindow.source ?? scope.window_source ?? "");
  // Address discovery also stores the hashes it found. The scope authority,
  // not the mere presence of tx_hashes, distinguishes those results from an
  // explicitly pasted hash (where Range truly is irrelevant).
  const explicitHashes = appliedQueryKind === "explicit_hash"
    ? stableIds(txRecord.query_hashes ?? loadedHashes)
    : [];
  const isDiscoveryScope = Boolean(
    appliedQueryKind && appliedQueryKind !== "explicit_hash",
  );

  const issueRequest = (settings: Partial<TxSettings>, label: string) => {
    setPendingIntent({ settings, label });
    requestTransactions(settings);
  };

  const requestDiscoveryControls = (
    controlSettings: Pick<Partial<TxSettings>, "maxTxs">,
    preserveExactWindow: boolean,
  ) => {
    const exactWindow =
      preserveExactWindow && ["money_trail_applied_window", "custom_utc_window"]
        .includes(windowSource);
    const requestSettings: Partial<TxSettings> = {
      // Discovery also publishes its found hashes; explicitly retire those as
      // a request subject so a Range/Txs edit reruns the original predicate.
      txHashes: [],
      seed: String(tx.seed ?? ""),
      counterparties: (tx.counterparties ?? []).map(String),
      tokens: (tx.tokens ?? []).map(String),
      minUsd: Number(tx.min_usd) || 0,
      t0: exactWindow ? String(tx.t0 ?? scopeWindow.t0 ?? "") : "",
      t1: exactWindow ? String(tx.t1 ?? scopeWindow.t1 ?? "") : "",
      ...controlSettings,
    };
    issueRequest(
      requestSettings,
      `Reloading address discovery for ${shortAddr(String(tx.seed ?? ""))}`,
    );
  };

  // A task switch unmounts this component; a subject change does not. Restore
  // the matching subject state in either case, but never leak another
  // transaction's selected receipt or pending draft into the new subject.
  useEffect(() => {
    if (taskUiKeyRef.current === taskUiKey) return;
    const restored = transactionTaskUi.get(taskUiKey);
    taskUiKeyRef.current = taskUiKey;
    restoringTaskUiRef.current = true;
    setInputMode(restored?.inputMode ?? appliedInputMode);
    setActiveTxHash(restored?.activeTxHash ?? "");
    setSelectedLegId(restored?.selectedLegId ?? "");
    setSelectedNodeId(restored?.selectedNodeId ?? "");
    setTxsPick(restored?.txsPick ?? null);
    setCanvasOpen(restored?.canvasOpen ?? false);
    setDetailsOpen(restored?.detailsOpen ?? false);
    setSubview(restored?.subview ?? (appliedInputMode === "address" ? "activity" : "receipt"));
  }, [taskUiKey]);

  useEffect(() => {
    // The first effect after a subject change still sees the previous
    // subject's render values. Do not write those into the new subject cache.
    if (restoringTaskUiRef.current) {
      restoringTaskUiRef.current = false;
      return;
    }
    rememberTransactionUi(taskUiKey, {
      inputMode,
      activeTxHash,
      selectedLegId,
      selectedNodeId,
      txsPick,
      canvasOpen,
      detailsOpen,
      subview,
    });
  }, [
    activeTxHash,
    canvasOpen,
    detailsOpen,
    inputMode,
    selectedLegId,
    selectedNodeId,
    subview,
    taskUiKey,
    txsPick,
  ]);

  useEffect(() => {
    const available = [
      ...discoveryRows.map((row) => row.txHash),
      ...groups
        .slice()
        .sort(
          (a, b) =>
            b.blockNumber - a.blockNumber ||
            (a.txHash < b.txHash ? -1 : a.txHash > b.txHash ? 1 : 0),
        )
        .map((group) => group.txHash),
    ].filter((hash, index, all) => all.indexOf(hash) === index);
    // Address activity is a block-explorer-style result list. Do not open the
    // newest receipt automatically: the analyst chooses which transaction to
    // inspect. Explicit hash mode still opens its only receipt immediately.
    const next = isDiscoveryScope ? "" : available[0] ?? loadedHashes[0] ?? "";
    if (!activeTxHash || ![...available, ...loadedHashes].includes(activeTxHash)) {
      setActiveTxHash(next);
    }
  }, [activeTxHash, discoveryRows, groups, isDiscoveryScope, loadedHashes]);

  const activeGroup = groups.find((group) => group.txHash === activeTxHash);
  const activeDiscoveryRow = discoveryRows.find(
    (row) => row.txHash === activeTxHash,
  );
  const inspectorGroup = activeGroup ?? (activeDiscoveryRow
    ? {
        txHash: activeDiscoveryRow.txHash,
        blockNumber: activeDiscoveryRow.blockNumber,
        blockTimestamp: activeDiscoveryRow.blockTimestamp,
        legs: [],
        totalUsd: null,
        knownUsdTotal: 0,
        unpricedLegCount: 0,
        tokens: [],
      }
    : undefined);
  const activeKnownUsdDisplay = activeGroup &&
    activeGroup.legs.length > activeGroup.unpricedLegCount
    ? fmtUsd(activeGroup.knownUsdTotal)
    : "unknown";
  const activeLegs = activeGroup?.legs ?? [];
  const activeIds = useMemo(
    () => new Set(activeLegs.flatMap((leg) => [leg.source, leg.target])),
    [activeLegs],
  );
  const activeNodes = useMemo(
    () => nodes.filter((node) => activeIds.has(node.id)),
    [activeIds, nodes],
  );
  const selectedLeg = legs.find((leg) => leg.id === selectedLegId);
  const selectedNode = nodes.find((node) => node.id === selectedNodeId);

  const selectLeg = (legId: string) => {
    const leg = legs.find((candidate) => candidate.id === legId);
    if (!leg) return;
    setActiveTxHash(leg.txHash);
    setSelectedLegId(leg.id);
    setSelectedNodeId("");
    setEvidenceOpen(false);
    setDetailsOpen(true);
  };

  const selectNode = (nodeId: string, txHash = activeTxHash) => {
    if (!nodeId) return;
    if (txHash) setActiveTxHash(txHash);
    setSelectedNodeId(nodeId);
    setSelectedLegId("");
    setEvidenceOpen(false);
    setDetailsOpen(true);
  };

  const clearSelection = () => {
    setSelectedLegId("");
    setSelectedNodeId("");
  };

  const chooseTransaction = (txHash: string) => {
    setActiveTxHash(txHash);
    setSubview("receipt");
    // A selected leg or participant from another receipt must never remain in
    // an inspector beside a graph that cannot contain it.
    setSelectedLegId("");
    setSelectedNodeId("");
  };

  const revealGraph = () => {
    setCanvasOpen(true);
    const scroll = () => {
      overviewRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    };
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(scroll);
    } else {
      window.setTimeout(scroll, 0);
    }
  };

  useEffect(() => {
    if (selectedLegId) {
      if (!selectedLeg) {
        setSelectedLegId("");
      } else if (selectedLeg.txHash !== activeTxHash) {
        setActiveTxHash(selectedLeg.txHash);
      }
      return;
    }
    if (!selectedNodeId) return;
    if (!selectedNode) {
      setSelectedNodeId("");
      return;
    }
    if (activeIds.has(selectedNodeId)) return;
    const owner = groups.find((group) =>
      group.legs.some(
        (leg) => leg.source === selectedNodeId || leg.target === selectedNodeId,
      ),
    );
    if (owner) setActiveTxHash(owner.txHash);
    else setSelectedNodeId("");
  }, [
    activeIds,
    activeTxHash,
    groups,
    selectedLeg,
    selectedLegId,
    selectedNode,
    selectedNodeId,
  ]);
  const svgTransaction = useMemo<TxSvgTransaction>(
    () => ({
      txHash: activeTxHash,
      nodes: activeNodes.map((node) => ({
        id: node.id,
        label: node.label,
        role: node.role,
        flags: node.flags,
      })),
      legs: activeLegs,
    }),
    [activeLegs, activeNodes, activeTxHash],
  );

  const submit = () => {
    const value = input.trim();
    if (!value) return;
    if (inputMode === "hash" && TX_HASH_RE.test(value)) {
      const hash = value.toLowerCase();
      setSubview("receipt");
      issueRequest({ txHashes: [hash], seed: "" }, `Opening transaction ${hash}`);
    } else if (inputMode === "address" && ADDR_RE.test(value)) {
      const address = value.toLowerCase();
      setSubview("activity");
      setActiveTxHash("");
      setDetailsOpen(false);
      setEvidenceOpen(false);
      issueRequest(
        {
          seed: address,
          txHashes: [],
          counterparties: [],
          tokens: [],
          t0: "",
          t1: "",
          maxTxs: (txsPick ?? Number(tx.max_txs)) || 25,
        },
        `Searching address activity for ${address}`,
      );
    }
    setInput("");
  };

  const inputValid = !input.trim() || (
    inputMode === "hash"
      ? TX_HASH_RE.test(input.trim())
      : ADDR_RE.test(input.trim())
  );

  /** Follow an address FORWARD in time: load the transactions it took part in
   * after the last one already on screen, and merge them in. This is the
   * "what did it do next?" step — the chain of custody continues from the
   * cursor rather than restarting the view. */
  const expandForward = (nodeId: string) => {
    if (!nodeId) return;
    const own = legs.filter((l) => l.source === nodeId || l.target === nodeId);
    const from = own.length ? own : legs;
    // Cursor = the latest position this address is known at, in chain order.
    const cursor = from.reduce(
      (acc, l) =>
        l.blockNumber > acc.block ||
        (l.blockNumber === acc.block && l.transactionIndex > acc.index)
          ? { block: l.blockNumber, index: l.transactionIndex }
          : acc,
      { block: 0, index: -1 },
    );
    const settings: Partial<TxSettings> = {
      expandNodeId: nodeId,
      afterBlock: cursor.block,
      afterIndex: cursor.index,
      merge: true,
    };
    issueRequest(settings, `Following ${nodeId} forward`);
  };

  useEffect(() => {
    if (txsPick !== null && Number(tx.max_txs) === txsPick) setTxsPick(null);
  }, [tx.max_txs, txsPick]);

  const expandedSet = useMemo(
    () => new Set<string>(((tx.expanded ?? []) as string[]).map(String)),
    [tx.expanded],
  );

  const coverage = objectValue(scope.coverage);
  const rowCoverage = objectValue(coverage.rows);
  const usdCoverage = objectValue(coverage.usd);
  const verification = objectValue(scope.verification);
  const truncation = objectValue(scope.truncation);
  const legsReturned =
    numeric(rowCoverage.shown) ?? numeric(scope.legs_returned) ?? legs.length;
  const legsTotal = numeric(rowCoverage.total) ?? numeric(scope.legs_total);
  const verificationStatus = String(verification.status ?? "unverified");
  const scopeStatus = String(scope.status ?? "partial");
  // TWO different completenesses, and conflating them is exactly the failure
  // the scope contract exists to prevent. "179 of 179 legs" while the badge
  // says PARTIAL reads as a contradiction; what was capped is the TRANSACTION
  // SET, not the legs of the transactions on screen.
  const legsComplete = legsTotal != null && legsReturned >= legsTotal;
  const moreTxsAvailable = Boolean(scope.more_transactions_available);
  const transactionLowerBound = numeric(scope.txs_total_lower_bound);
  const legsTruncated = Boolean(truncation.truncated ?? scope.truncated);
  const rowsLoaded = txLegs?.rowsLoaded ?? legs.length;
  const rowsExpected = txLegs?.rowsExpected ?? legsTotal;
  const hydratedRowsComplete = Boolean(
    txLegs?.phase === "complete" &&
      !txLegs.truncated &&
      (rowsExpected != null
        ? rowsLoaded >= rowsExpected
        : legsTotal != null && rowsLoaded >= legsTotal),
  );
  const verifiedComplete =
    hydratedRowsComplete &&
    verificationStatus === "verified" &&
    scopeStatus !== "failed" &&
    legsComplete &&
    !legsTruncated;
  const receiptStatuses = objectValue(scope.receipt_statuses);
  const decodeFailures = Array.isArray(scope.decode_failures)
    ? scope.decode_failures.map(objectValue)
    : [];
  const transactionCount = new Set([
    ...loadedHashes,
    ...groups.map((group) => group.txHash),
    ...Object.keys(receiptStatuses).map((hash) => hash.toLowerCase()),
  ]).size;
  const enrichmentPartial = Array.isArray(scope.sources) && scope.sources.some(
    (value) => {
      const source = objectValue(value);
      return source.role === "enrichment" &&
        !["ok", "not_needed"].includes(String(source.status ?? ""));
    },
  );
  const emptyDiscoveryResult = Boolean(
    isDiscoveryScope && transactionCount === 0 && scope.scope_id,
  );
  const verifiedEmptyDiscovery = Boolean(
    emptyDiscoveryResult &&
    verificationStatus === "verified" &&
    scopeStatus !== "failed" &&
    legsTotal === 0,
  );
  const badge =
    txLegs?.phase === "failed"
      ? `FAILED RECEIPT HYDRATION · ${rowsLoaded}/${rowsExpected ?? "?"}`
      : txLegs?.phase === "loading"
        ? `LOADING RECEIPT LEGS · ${rowsLoaded}/${rowsExpected ?? "?"}`
        : verifiedEmptyDiscovery
          ? "NO MATCHES · VERIFIED ADDRESS DISCOVERY"
          : isDiscoveryScope
            ? moreTxsAvailable
              ? `MORE ADDRESS ACTIVITY · ${transactionCount} SHOWN`
              : verificationStatus === "verified" && transactionCount > 0
                ? `VERIFIED ADDRESS DISCOVERY · ${transactionCount} TRANSACTION${
                    transactionCount === 1 ? "" : "S"
                  }`
                : "PARTIAL ADDRESS DISCOVERY"
            : verifiedComplete
              ? `RPC VERIFIED · ${legsReturned}/${legsTotal ?? "?"} ERC-20 LEGS${
                  enrichmentPartial ? " · ENRICHMENT PARTIAL" : ""
                }`
              : scopeStatus === "failed"
                ? "FAILED RECEIPT INSPECTION"
                : "PARTIAL RECEIPT INSPECTION";
  const stale = loading || txsPick !== null;
  const transactionEvidenceSummary = isDiscoveryScope
    ? [
        `${transactionCount} transaction${transactionCount === 1 ? "" : "s"}`,
        `${legsReturned.toLocaleString()} receipt leg${legsReturned === 1 ? "" : "s"}`,
        `${nodes.length.toLocaleString()} participants`,
      ].join(" · ")
    : [
        `${legsReturned.toLocaleString()}/${legsTotal?.toLocaleString() ?? "?"} receipt legs`,
        `${transactionCount} transaction${transactionCount === 1 ? "" : "s"}`,
        `${nodes.length.toLocaleString()} participants`,
        `known USD subtotal ${fmtUsd(
          legsReturned > 0 &&
            (numeric(usdCoverage.unknown_rows) ?? 0) >= legsReturned
            ? null
            : numeric(usdCoverage.known),
        )}`,
      ].join(" · ");
  const transactionEvidenceBound = moreTxsAvailable
    ? `Newest ${transactionCount} shown · at least ${
        transactionLowerBound?.toLocaleString() ?? transactionCount + 1
      } match · increase Results to scan farther back`
    : legsTruncated
      ? "4,000-leg safety cap · whole transactions dropped, never split"
      : explicitHashes.length
        ? "RPC receipt authority · ERC-20 Transfer logs"
        : String(scope.discovery_path ?? "") === "execution_logs_rpc_tail"
          ? "All-history execution-log discovery + RPC head · authoritative RPC receipt legs"
          : String(scope.discovery_path ?? "") === "address_index_rpc_tail"
            ? "Address index through its source horizons + RPC head · authoritative RPC receipt legs"
          : String(scope.discovery_path ?? "") === "address_index"
            ? "Address-indexed hash discovery · RPC receipt legs"
            : "Scoped chain-log hash discovery · RPC receipt legs";
  const hasInspector = Boolean(activeTxHash || hasSelection);
  const fallbackRetrySettings: Partial<TxSettings> = {
    txHashes: explicitHashes,
    seed: explicitHashes.length ? "" : String(tx.seed ?? ""),
    counterparties: (tx.counterparties ?? []).map(String),
    tokens: (tx.tokens ?? []).map(String),
    maxTxs: Number(tx.max_txs) || 25,
    ...(!explicitHashes.length && [
      "execution_logs_plus_rpc_head",
      "address_index_plus_rpc_head",
      "rpc_cursor_to_head",
    ].includes(windowSource)
      ? { t0: "", t1: "", rangeDays: 0 }
      : {}),
    ...(["money_trail_applied_window", "custom_utc_window"].includes(windowSource)
      ? { t0: String(tx.t0 ?? ""), t1: String(tx.t1 ?? "") }
      : {}),
  };
  const retryTransactionLoad = () => {
    const retry = pendingIntent ?? {
      settings: fallbackRetrySettings,
      label: "Retrying the last applied transaction scope",
    };
    issueRequest(retry.settings, retry.label);
  };
  const scopeRevision = String(scope.scope_id ?? scope.request_id ?? "");
  const lastAttempt = objectValue(txRecord.last_attempt);
  const lastAttemptScope = objectValue(lastAttempt.scope);
  const transactionFailure =
    loadError ||
    (lastAttempt.status === "failed"
      ? String(
          lastAttempt.error ??
          lastAttemptScope.error ??
          "Transaction request failed; the last applied evidence was preserved.",
        )
      : scopeStatus === "failed"
        ? String(scope.error ?? scope.warnings ?? "source scope failed")
      : txLegs?.phase === "failed"
        ? txLegs.error || "transfer-leg hydration failed"
        : null);

  useEffect(() => {
    if (!transactionFailure) return;
    setTxsPick(null);
  }, [transactionFailure]);

  useEffect(() => {
    // A successful response adopts the pending snapshot. A failed response
    // deliberately retains it so Retry replays the exact subject the analyst
    // asked for rather than silently falling back to the previous receipt.
    if (!loading && !transactionFailure) {
      setPendingIntent(null);
    }
  }, [loading, scopeRevision, transactionFailure]);

  return (
    <div className="ge-mode ge-mode--tx">
      {/* Chrome order matches every other mode: filters left, then
        * `.ge-topbar-right` with the details toggle and the mode switch LAST.
        * The switch is app chrome — if a mode places it somewhere else the
        * tab bar jumps as you switch modes. */}
      <div className="ge-topbar ge-tx-bar">
        <div className="ge-tx-query-kind" role="group" aria-label="Transaction search mode">
          <button
            type="button"
            className={inputMode === "hash" ? "active" : ""}
            aria-pressed={inputMode === "hash"}
            onClick={() => {
              setInputMode("hash");
              setSubview("receipt");
              setInput("");
            }}
          >
            Transaction hash
          </button>
          <button
            type="button"
            className={inputMode === "address" ? "active" : ""}
            aria-pressed={inputMode === "address"}
            onClick={() => {
              setInputMode("address");
              setSubview("activity");
              setInput("");
            }}
          >
            Address activity
          </button>
        </div>
        <input
          className="ge-input ge-tx-input"
          placeholder={inputMode === "hash" ? "0x… transaction hash" : "0x… address"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          title={
            inputMode === "hash"
              ? "Open one transaction through its authoritative RPC receipt"
              : "Find hashes across indexed execution history plus the RPC head, then verify every receipt by RPC"
          }
        />
        <button
          type="button"
          className="ge-btn ge-btn--primary"
          onClick={submit}
          disabled={!input.trim() || !inputValid || loading}
        >
          {inputMode === "hash" ? "Open receipt →" : "Search activity →"}
        </button>
        {!inputValid && (
          <span className="ge-tx-invalid">
            {inputMode === "hash" ? "needs a 66-character transaction hash" : "needs a 42-character address"}
          </span>
        )}
        {inputMode === "address" && (
        <details className="ge-filter-drawer ge-filter-drawer--tx">
          <summary>Address search</summary>
          <div className="ge-topbar-filters">
        <div className="ge-field" title="Stored execution logs cover history; RPC scans only the uncovered head. Every result is then decoded from its RPC receipt.">
          <span>Discovery scope</span>
          <strong>All indexed history + RPC head</strong>
        </div>
        <label className="ge-field" title="Maximum address-discovery results">
          <span>Results</span>
          <select
            value={txsPick ?? (Number(tx.max_txs) || 25)}
            onChange={(e) => {
              const v = Number(e.target.value);
              setTxsPick(v);
              if (isDiscoveryScope && tx.seed) {
                // Admission changes do not change the full-history predicate.
                requestDiscoveryControls({ maxTxs: v }, true);
              }
            }}
          >
            {MAX_TX_OPTIONS.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
          </div>
        </details>
        )}
        <div className="ge-topbar-right">
          {stale ? (
            <span className="ge-pending-chip" role="status" title={pendingIntent?.label || "Transaction request pending"}>
              Applied results · draft pending
            </span>
          ) : null}
          <EvidenceTrigger
            scope={tx.scope}
            datasets="Receipt transactions, participants, and ordered ERC-20 transfer legs"
            statusLabel={badge}
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
        </div>
      </div>

      {decodeFailures.length > 0 ? (
        <div className="ge-load-error" role="alert">
          <span>
            {decodeFailures.length} matching Transfer log
            {decodeFailures.length === 1 ? "" : "s"} could not be decoded and
            {decodeFailures.length === 1 ? " is" : " are"} omitted. No zero
            amount was invented; inspect Evidence details for the source scope.
          </span>
        </div>
      ) : null}

      {transactionFailure && (
        <div className="ge-load-error" role="alert">
          <span>
            {pendingIntent?.label ? `${pendingIntent.label}. ` : ""}
            {String(lastAttempt.query_kind ?? appliedQueryKind) === "explicit_hash"
              ? "Receipt inspection failed"
              : "Address discovery failed"}
            : {transactionFailure}. The last applied evidence remains on screen.
          </span>
          <button
            type="button"
            className="ge-btn"
            onClick={retryTransactionLoad}
          >
            Retry
          </button>
        </div>
      )}

      {/* Standard three-column body: canvas | leg rail | details. The details
        * panel MUST be a grid column, not a block stacked underneath — as a
        * sibling in a vertical flex it consumed half the viewport, collapsed
        * the canvas, and pushed the bottom-anchored legend up over the
        * toolbar. */}
      {/* Following an address forward takes seconds against the chain tables.
        * With no feedback the ↪ button reads as broken and gets clicked
        * repeatedly — which is exactly how it was reported. */}
      {loading && (
        <div className="ge-tx-busy" role="status">
          <span className="ge-tx-spinner" aria-hidden />
          {pendingIntent?.label || "Loading transactions…"}
        </div>
      )}
      <div
        className={`ge-body ge-body--tx-detail ${
          detailsOpen && hasInspector ? "details-open" : "details-closed"
        }`}
      >
        <main
          ref={workspaceRef}
          className={`ge-tx-workspace${stale ? " is-stale" : ""}`}
        >
          {isDiscoveryScope && subview === "activity" && (
            <DiscoveryResults
              rows={discoveryRows}
              activeTxHash={activeTxHash}
              receiptStatuses={receiptStatuses}
              onChoose={chooseTransaction}
              canLoadMore={moreTxsAvailable && Number(tx.max_txs ?? 25) < 200}
              loading={loading}
              onLoadMore={() => {
                const current = Number(tx.max_txs ?? 25);
                const next = Math.min(200, current < 50 ? 50 : current * 2);
                setTxsPick(next);
                requestDiscoveryControls({ maxTxs: next }, true);
              }}
            />
          )}

          {isDiscoveryScope && subview === "receipt" && activeTxHash ? (
            <div className="ge-tx-receipt-nav">
              <button
                type="button"
                className="ge-btn"
                onClick={() => {
                  setSubview("activity");
                  setDetailsOpen(false);
                  setEvidenceOpen(false);
                  window.requestAnimationFrame(() => workspaceRef.current?.scrollTo({ top: 0 }));
                }}
              >
                ← Address activity
              </button>
              <code title={activeTxHash}>{activeTxHash}</code>
            </div>
          ) : null}

          {emptyDiscoveryResult && (
            <section className="ge-tx-empty-discovery" role="status">
              <strong>
                {verifiedEmptyDiscovery
                  ? "No direct or standard ERC-20 Transfer transactions found"
                  : "No matching transactions observed in the covered data"}
              </strong>
              <p>
                Address <code>{String(tx.seed ?? "unknown")}</code> had no matching
                {verifiedEmptyDiscovery
                  ? " direct transaction or standard ERC-20 Transfer activity in the complete indexed history plus RPC head scan."
                  : " matching activity in the indexed/log pages and RPC head that completed."}
              </p>
              <p>
                Stored address history and its uncovered RPC tail were checked through block {String(scope.data_horizon ?? "unknown")}.
                {!verifiedEmptyDiscovery
                  ? " The scan did not complete, so this is not verified absence."
                  : ""}
              </p>
              <div>
                <span>Direct native transactions are discoverable; internal calls and non-standard token events remain outside this search.</span>
              </div>
            </section>
          )}

          {!activeTxHash && !emptyDiscoveryResult && !loading && (
            <section className="ge-tx-start-state">
              <strong>Open receipt evidence or search address activity</strong>
              <p>
                Transaction hashes are verified from RPC receipts. Address search
                only discovers candidate hashes; each selected result is then
                inspected from its receipt.
              </p>
            </section>
          )}

          {groups.length > 1 && discoveryRows.length === 0 && (
            <nav className="ge-tx-picker" aria-label="Loaded transactions">
              {groups.map((group, index) => (
                <button
                  key={group.txHash}
                  type="button"
                  className={group.txHash === activeTxHash ? "active" : ""}
                  aria-pressed={group.txHash === activeTxHash}
                  onClick={() => chooseTransaction(group.txHash)}
                  title={group.txHash}
                >
                  Tx {index + 1} · {group.legs.length} legs
                </button>
              ))}
            </nav>
          )}

          {activeTxHash && (!isDiscoveryScope || subview === "receipt") && (<>
          <section
            ref={overviewRef}
            className="ge-tx-overview"
            aria-label="Transaction transfer graph"
          >
            <header>
              <div>
                <strong>Transfer graph</strong>
                {activeTxHash && <code title={activeTxHash}>{activeTxHash}</code>}
                {activeTxHash && (
                  <span>
                    status {String(receiptStatuses[activeTxHash] ?? activeGroup?.legs[0]?.txStatus ?? "unknown")}
                    {" · "}known USD subtotal {activeKnownUsdDisplay}
                    {" · "}{activeGroup?.unpricedLegCount ?? 0} unpriced leg{activeGroup?.unpricedLegCount === 1 ? "" : "s"}
                  </span>
                )}
              </div>
              <div className="ge-tx-overview__actions">
                <button
                  type="button"
                  className="ge-btn"
                  aria-expanded={canvasOpen}
                  onClick={() => {
                    if (canvasOpen) setCanvasOpen(false);
                    else revealGraph();
                  }}
                >
                  {canvasOpen ? "Hide transfer graph" : "Show transfer graph"}
                </button>
              </div>
            </header>
            <TxGraphLegend
              legs={svgTransaction.legs}
              nodes={svgTransaction.nodes}
              decodedLogCount={activeLegs.length}
            />
            {canvasOpen && activeTxHash && (
              <div className="ge-tx-overview__graph">
                <GraphErrorBoundary
                  resetKey={`${activeTxHash}:${activeLegs.length}`}
                  fallback={(error, retry) => (
                    <GraphTableFallback
                      title="Transaction graph table"
                      error={error}
                      onRetry={retry}
                      selectedNodeId={selectedNodeId}
                      selectedEdgeId={selectedLegId}
                      onSelectNode={(nodeId) => selectNode(nodeId)}
                      onSelectEdge={selectLeg}
                      model={{
                        nodes: activeNodes.map((node) => ({
                          id: node.id,
                          label: node.label,
                          kind: node.role,
                        })),
                        edges: activeLegs.map((leg) => ({
                          id: leg.id,
                          source: leg.source,
                          target: leg.target,
                          label: leg.symbol || leg.tokenAddress,
                          weight: leg.amountUsd,
                        })),
                      }}
                    />
                  )}
                >
                  <TxSvgCanvas
                    transaction={svgTransaction}
                    selectedLegId={selectedLegId}
                    selectedNodeId={selectedNodeId}
                    onSelectLeg={selectLeg}
                    onSelectNode={(nodeId) => selectNode(nodeId)}
                    onClearSelection={clearSelection}
                    height={420}
                  />
                </GraphErrorBoundary>
              </div>
            )}
            {canvasOpen && !activeTxHash && (
              <div className="ge-tx-rail-empty">
                Paste a transaction hash to inspect every ERC-20 Transfer leg,
                or enter an address to discover transactions.
              </div>
            )}
            {!canvasOpen && (
              <div className="ge-tx-overview__hidden" role="status">
                <span>
                  Transfer graph hidden. The ordered leg table remains available.
                </span>
                <button type="button" className="ge-btn" onClick={revealGraph}>
                  Show transfer graph
                </button>
              </div>
            )}
          </section>

          <section className="ge-tx-table-region" aria-label="Ordered transfer legs">
            <header>
              <div>
                <strong>Ordered transfer legs</strong>
                <span>Receipt order is block → transaction index → log index.</span>
              </div>
              <span>{activeLegs.length.toLocaleString()} rows for selected receipt</span>
            </header>
            <div className="ge-table-scroll" tabIndex={0}>
              <table className="ge-tx-table">
                <thead>
                  <tr>
                    <th scope="col">Order</th>
                    <th scope="col">Transaction hash</th>
                    <th scope="col">Block / tx / log</th>
                    <th scope="col">Timestamp</th>
                    <th scope="col">Sender</th>
                    <th scope="col">Recipient</th>
                    <th scope="col">Token</th>
                    <th scope="col">Raw amount</th>
                    <th scope="col">USD</th>
                    <th scope="col">Participant role</th>
                    <th scope="col">Receipt</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {activeLegs.map((leg, index) => {
                    const srcNode = nodes.find((node) => node.id === leg.source);
                    const tgtNode = nodes.find((node) => node.id === leg.target);
                    const roleCls = (role?: string) =>
                      role === "token" || role === "burn" ? ` ge-tx-role--${role}` : "";
                    const selected = selectedLegId === leg.id;
                    return (
                      <tr
                        key={leg.id}
                        tabIndex={0}
                        className={selected ? "is-selected" : ""}
                        aria-selected={selected}
                        aria-label={`Transfer ${index + 1}, log ${leg.logIndex}, ${
                          leg.symbol || leg.tokenAddress || "unknown token"
                        }`}
                        onClick={() => selectLeg(leg.id)}
                        onKeyDown={(event) => {
                          if (
                            event.target === event.currentTarget &&
                            (event.key === "Enter" || event.key === " ")
                          ) {
                            event.preventDefault();
                            selectLeg(leg.id);
                          }
                        }}
                      >
                        <td>{index + 1}</td>
                        <td><code title={leg.txHash}>{leg.txHash}</code></td>
                        <td>{leg.blockNumber.toLocaleString()} / {leg.transactionIndex} / {leg.logIndex}</td>
                        <td>{leg.blockTimestamp || "unknown"}</td>
                        <td>
                          <AddrCell
                            address={leg.source}
                            node={srcNode}
                            burnLabel="∅ mint"
                            roleCls={roleCls}
                            expanded={expandedSet.has(leg.source)}
                            onSelect={(nodeId) => selectNode(nodeId, leg.txHash)}
                            onFollow={(nodeId) => {
                              setActiveTxHash(leg.txHash);
                              expandForward(nodeId);
                            }}
                          />
                        </td>
                        <td>
                          <AddrCell
                            address={leg.target}
                            node={tgtNode}
                            burnLabel="∅ burn"
                            roleCls={roleCls}
                            expanded={expandedSet.has(leg.target)}
                            onSelect={(nodeId) => selectNode(nodeId, leg.txHash)}
                            onFollow={(nodeId) => {
                              setActiveTxHash(leg.txHash);
                              expandForward(nodeId);
                            }}
                          />
                        </td>
                        <td>
                          <strong>{leg.symbol || "unknown"}</strong>
                          <code title={leg.tokenAddress}>{leg.tokenAddress || "unknown"}</code>
                        </td>
                        <td><code>{leg.rawAmount || "unknown"}</code></td>
                        <td>{fmtUsd(leg.amountUsd)}</td>
                        <td>{srcNode?.role || "address"} → {tgtNode?.role || "address"}</td>
                        <td>{String(receiptStatuses[leg.txHash] ?? leg.txStatus ?? "unknown")}</td>
                        <td>
                          <button
                            type="button"
                            className="ge-link-btn"
                            onClick={(event) => {
                              event.stopPropagation();
                              void navigator.clipboard?.writeText(leg.txHash);
                            }}
                          >
                            Copy
                          </button>{" "}
                          <a
                            href={`https://gnosis.blockscout.com/tx/${leg.txHash}`}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(event) => event.stopPropagation()}
                          >
                            Explorer
                          </a>
                        </td>
                      </tr>
                    );
                  })}
                  {activeLegs.length === 0 && (
                    <tr>
                      <td colSpan={12} className="ge-tx-table-empty">
                        {!isDiscoveryScope &&
                        Boolean(activeTxHash) &&
                        verificationStatus === "verified" &&
                        legsTotal === 0
                          ? "Receipt verified: this transaction has no ERC-20 Transfer legs."
                          : isDiscoveryScope
                            ? "Select an address-discovery result to inspect its receipt legs."
                            : "No verified receipt legs are loaded for this scope."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
          </>)}
        </main>

        {detailsOpen && hasInspector && (
          <TxInspector
            txHash={activeTxHash}
            transaction={inspectorGroup}
            receiptStatus={String(
              receiptStatuses[activeTxHash] ??
                activeGroup?.legs[0]?.txStatus ??
                "unknown",
            )}
            selectedLeg={selectedLeg}
            selectedNode={selectedNode}
            graphVisible={canvasOpen}
            onRevealGraph={revealGraph}
            onFollowNode={expandForward}
            onOpenNode={(nodeId) =>
              issueRequest(
                { seed: nodeId, txHashes: [] },
                `Opening transactions for ${nodeId}`,
              )
            }
            onClose={() => setDetailsOpen(false)}
          />
        )}
      </div>
      {evidenceOpen ? (
        <EvidencePanel
          scope={tx.scope}
          datasets="Receipt transactions, participants, and ordered ERC-20 transfer legs"
          statusLabel={badge}
          summary={transactionEvidenceSummary}
          bound={transactionEvidenceBound}
          onClose={() => setEvidenceOpen(false)}
          openerRef={evidenceTriggerRef}
        />
      ) : null}
    </div>
  );
}
