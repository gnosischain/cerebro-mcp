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
import { ChainBadge } from "../../shared/ChainBadge";
import { GNOSIS_CHAIN_ID, txUrl } from "../model/explorerLinks";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { EvidencePanel, EvidenceTrigger } from "../ForensicScopeDisclosure";
import { FilterDrawer } from "../FilterDrawer";
import { GraphErrorBoundary } from "../GraphErrorBoundary";
import { GraphTableFallback } from "../GraphTableFallback";
import { TxGraphLegend } from "../canvas/TxGraphLegend";
import { TxSvgCanvas, type TxSvgTransaction } from "../canvas/TxSvgCanvas";
import {
  buildTxGraphModel,
  groupLegsByTx,
  parseTxListRows,
  type TxLegRow,
  type TxListRow,
} from "../model/txLayout";
import { parseTxContextRows, type TxContextRow } from "../model/txContext";
import type { GraphLocalState } from "../state/graphReducer";
import type { EvidenceExpectation, ForensicScope, GraphExplorerViewState } from "../types";
import { TxInspector } from "./TxInspector";
import "./transaction-detail.css";

export interface TxSettings {
  /** Additive app contract. Legacy callers may omit this field. */
  operation: "legacy" | "discover" | "receipt";
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
  /** Opaque newest-first discovery cursor returned by the server. */
  cursor: string;
  /** Candidate page admission; independent from receipt-leg hydration. */
  pageSize: number;
  activityKinds: Array<"direct" | "erc20">;
  /** EVM chain for this request. Receipts are RPC-sourced and portable; the
   * server refuses address discovery off Gnosis rather than faking it. */
  chain: string;
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
  txContext?: HydratedDataset;
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

const MAX_TX_OPTIONS = [25, 50, 100];
const DISCOVERY_PAGE_SIZE = 25;

const TX_HASH_RE = /^0x[0-9a-fA-F]{64}$/;
const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;

interface TransactionTaskUiState {
  inputMode: "hash" | "address";
  activeTxHash: string;
  selectedLegId: string;
  selectedNodeId: string;
  txsPick: number | null;
  discoveryWindowMode: "all" | "custom";
  discoveryT0: string;
  discoveryT1: string;
  discoveryActivityKinds: Array<"direct" | "erc20">;
  discoveryTokenInput: string;
  canvasOpen: boolean;
  detailsOpen: boolean;
  subview: TransactionSubview;
}

type TransactionSubview = "activity" | "receipt";

interface PendingTxIntent {
  settings: Partial<TxSettings>;
  label: string;
}

interface AutomaticDiscovery {
  address: string;
  lastScopeRevision: string;
  seenCursors: Set<string>;
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
  const query = objectValue(tx.query);
  const queryKind = String(query.kind ?? "");
  if (queryKind === "hash") {
    const hashes = stableIds(query.hashes);
    if (hashes.length) return `hash:${hashes.join(",")}`;
  }
  if (["address", "money_edge", "follow"].includes(queryKind)) {
    const address = String(query.address ?? "").toLowerCase();
    const counterparties = stableIds(query.counterparties).join(",");
    const tokens = stableIds(query.tokens).join(",");
    return `discovery:${address}|cp:${counterparties}|tokens:${tokens}`;
  }
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

type DiscoveryActivityKind = "direct" | "erc20";

interface AppliedDiscoveryFilters {
  windowMode: "all" | "custom";
  t0: string;
  t1: string;
  activityKinds: DiscoveryActivityKind[];
  tokens: string[];
}

function normalizedActivityKinds(value: unknown): DiscoveryActivityKind[] {
  if (!Array.isArray(value)) return ["direct", "erc20"];
  const selected = new Set(
    value.filter((kind): kind is DiscoveryActivityKind =>
      kind === "direct" || kind === "erc20"
    ),
  );
  return (["direct", "erc20"] as const).filter((kind) => selected.has(kind));
}

function utcInputValue(value: unknown): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  const normalized = raw.replace(" ", "T");
  // Server windows are UTC. Keep their wall-clock value visible instead of
  // applying the analyst workstation's local offset to a datetime-local input.
  return normalized.replace(/Z$/i, "").slice(0, 16);
}

function utcBoundFromInput(value: string): string {
  if (!value) return "";
  const parsed = new Date(`${value}:00Z`);
  return Number.isFinite(parsed.getTime()) ? parsed.toISOString() : "";
}

function tokenAddressesFromInput(value: string): string[] {
  return [...new Set(
    value
      .split(/[\s,;]+/)
      .map((token) => token.trim().toLowerCase())
      .filter(Boolean),
  )];
}

function readAppliedDiscoveryFilters(
  tx: GraphExplorerViewState["transactions"],
  discoveryScope: Record<string, unknown>,
): AppliedDiscoveryFilters {
  const query = objectValue(tx?.query);
  const queryWindow = objectValue(query.window);
  const scopeWindow = objectValue(discoveryScope.window);
  const windowSource = String(queryWindow.source ?? scopeWindow.source ?? "");
  const queryT0 = String(queryWindow.t0 ?? "");
  const queryT1 = String(queryWindow.t1 ?? "");
  const legacyExact = ["money_trail_applied_window", "custom_utc_window"]
    .includes(windowSource);
  const rawT0 = queryT0 || (legacyExact ? String(tx?.t0 ?? scopeWindow.t0 ?? "") : "");
  const rawT1 = queryT1 || (legacyExact ? String(tx?.t1 ?? scopeWindow.t1 ?? "") : "");
  const exact = Boolean(rawT0 && rawT1);
  const tokens = stableIds(query.tokens ?? tx?.tokens ?? []);
  const activityKinds = normalizedActivityKinds(query.activity_kinds);
  return {
    windowMode: exact ? "custom" : "all",
    t0: exact ? utcInputValue(rawT0) : "",
    t1: exact ? utcInputValue(rawT1) : "",
    activityKinds: tokens.length ? ["erc20"] : activityKinds,
    tokens,
  };
}

function discoveryFilterSummary(filters: AppliedDiscoveryFilters): string {
  const window = filters.windowMode === "custom"
    ? `${filters.t0.replace("T", " ")} → ${filters.t1.replace("T", " ")} UTC`
    : "All available history + RPC head";
  const activity = filters.activityKinds.length === 2
    ? "Direct + ERC-20"
    : filters.activityKinds[0] === "direct"
      ? "Direct only"
      : "ERC-20 only";
  const tokens = filters.tokens.length
    ? `${filters.tokens.length} token address${filters.tokens.length === 1 ? "" : "es"}`
    : "All tokens";
  return `${window} · ${activity} · ${tokens}`;
}

const STRUCTURAL_ADDRESS_RE = /^0x(?:0{40}|0{36}dead)$/i;

function transferEventKind(
  leg: Pick<TxLegRow, "source" | "target">,
  sourceRole?: string,
  targetRole?: string,
): "Mint" | "Burn" | "Transfer" {
  if (sourceRole === "burn" || STRUCTURAL_ADDRESS_RE.test(leg.source)) return "Mint";
  if (targetRole === "burn" || STRUCTURAL_ADDRESS_RE.test(leg.target)) return "Burn";
  return "Transfer";
}

function TransactionContext({ context }: { context: TxContextRow }) {
  return (
    <section className="ge-tx-context" aria-label="Transaction context">
      <header>
        <strong>Transaction context</strong>
        <span>RPC transaction envelope and receipt</span>
      </header>
      <dl>
        <dt>Initiator</dt>
        <dd><code title={context.initiator ?? ""}>{context.initiator ?? "unknown"}</code></dd>
        <dt>Called contract</dt>
        <dd><code title={context.target ?? ""}>{context.target ?? "contract creation"}</code></dd>
        <dt>Method</dt>
        <dd><code>{context.methodSelector ?? "unknown"}</code></dd>
        <dt>Nonce</dt>
        <dd>{context.nonce ?? "unknown"}</dd>
        <dt>Native value (raw)</dt>
        <dd><code>{context.nativeValueRaw ?? "unknown"}</code></dd>
        <dt>Gas limit / used</dt>
        <dd>{context.gasLimit ?? "unknown"} / {context.gasUsed ?? "unknown"}</dd>
        <dt>Effective gas price</dt>
        <dd><code>{context.effectiveGasPrice ?? "unknown"}</code></dd>
        <dt>Fee (raw)</dt>
        <dd><code>{context.feeRaw ?? "unknown"}</code></dd>
        <dt>Block / tx</dt>
        <dd>{context.blockNumber ?? "unknown"} / {context.transactionIndex ?? "unknown"}</dd>
        <dt>Matched because</dt>
        <dd>{context.matchedBecause.join(", ") || "explicit hash"}</dd>
      </dl>
      {context.input ? (
        <details>
          <summary>Input data</summary>
          <code>{context.input}</code>
        </details>
      ) : null}
    </section>
  );
}

function DiscoveryResults({
  rows,
  address,
  targetRows,
  activeTxHash,
  receiptStatuses,
  onChoose,
  canLoadMore,
  loading,
  automatic,
  onStop,
  onLoadMore,
  emptyActionLabel = "Continue older history",
}: {
  rows: TxListRow[];
  address: string;
  targetRows: number;
  activeTxHash: string;
  receiptStatuses: Record<string, unknown>;
  onChoose: (hash: string) => void;
  canLoadMore: boolean;
  loading: boolean;
  automatic: boolean;
  onStop: () => void;
  onLoadMore: () => void;
  emptyActionLabel?: string;
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

  if (!rows.length) {
    if (!canLoadMore && !loading) return null;
    return (
      <section className="ge-tx-discovery-results" aria-label="Address activity results">
        <header>
          <div>
            <strong>{loading ? "Searching address history" : "No matches in the scanned portion yet"}</strong>
            <span><code>{address}</code>{loading ? " · newest stored history first" : " · older stored history remains unscanned"}</span>
          </div>
          {automatic ? (
            <button type="button" className="ge-btn" onClick={onStop}>Stop search</button>
          ) : canLoadMore ? (
            <button type="button" className="ge-btn primary" onClick={onLoadMore}>
              {emptyActionLabel}
            </button>
          ) : null}
        </header>
      </section>
    );
  }
  return (
    <section className="ge-tx-discovery-results" aria-label="Address activity results">
      <header>
        <div>
          <strong>Activity for <code>{address}</code></strong>
          <span>Newest first · select a transaction to verify its RPC receipt.</span>
        </div>
        <div className="ge-tx-discovery-results__status" role="status">
          {automatic ? <span className="ge-tx-spinner" aria-hidden /> : null}
          <span>
            {rows.length.toLocaleString()}{automatic ? ` of ${targetRows}` : ""} loaded
            {automatic ? " · searching older history" : ` · page ${visiblePage + 1}/${pageCount}`}
          </span>
          {automatic ? (
            <button type="button" className="ge-btn" onClick={onStop}>Stop search</button>
          ) : null}
        </div>
      </header>
      <div className="ge-table-scroll" tabIndex={0}>
        <table>
          <thead>
            <tr>
              <th scope="col">Time / position</th>
              <th scope="col">Transaction</th>
              <th scope="col">Indexed activity</th>
              <th scope="col"><span className="sr-only">Receipt evidence</span></th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => {
              const selected = row.txHash === activeTxHash;
              const receipt = String(receiptStatuses[row.txHash] ?? "");
              return (
                <tr
                  key={row.txHash}
                  className={selected ? "is-selected" : ""}
                >
                  <td>
                    {row.blockTimestamp || "unknown"}
                    <span className="ge-tx-row-meta">
                      Block {row.blockNumber.toLocaleString()} · transaction {row.transactionIndex}
                    </span>
                  </td>
                  <td><code title={row.txHash}>{row.txHash}</code></td>
                  <td>
                    {row.legCount > 0
                      ? `${row.legCount.toLocaleString()} standard ERC-20 event${row.legCount === 1 ? "" : "s"}`
                      : "Direct transaction"}
                    {row.tokenCount > 0 ? (
                      <span className="ge-tx-row-meta">
                        {row.tokenCount.toLocaleString()} token{row.tokenCount === 1 ? "" : "s"}
                      </span>
                    ) : null}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="ge-btn ge-btn--primary"
                      aria-current={selected ? "true" : undefined}
                      onClick={() => onChoose(row.txHash)}
                    >
                      {selected
                        ? "Viewing receipt"
                        : receipt
                          ? `Inspect · ${receipt}`
                          : "Inspect receipt"}
                    </button>
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
            {loading ? "Loading…" : "Continue older history"}
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
  txContext,
  requestTransactions,
  loading,
  loadError,
}: Props) {
  const tx = server.transactions ?? {};
  const txRecord = tx as unknown as Record<string, unknown>;
  const baseScope = (tx.scope ?? {}) as Record<string, unknown>;
  const initialDiscoveryScope = objectValue(txRecord.discovery_scope ?? baseScope);
  const initialDiscoveryFilters = readAppliedDiscoveryFilters(
    server.transactions,
    initialDiscoveryScope,
  );
  const queryKind = String(tx.query?.kind ?? "");
  const scopeQueryKind = String(baseScope.query_kind ?? txRecord.query_kind ?? "");
  // The query namespace is the current subject authority. `scope` can still
  // contain the last accepted discovery while a newly selected receipt is
  // committed into receipt_scope; preferring that stale generic scope made a
  // verified explicit hash render the blank address start state.
  const appliedQueryKind = queryKind === "hash"
    ? "explicit_hash"
    : ["address", "money_edge", "follow"].includes(queryKind)
      ? "address_discovery"
      : scopeQueryKind || (
        String(objectValue(baseScope.window).source ?? "") === "ignored_for_explicit_hash"
          ? "explicit_hash"
          : String(tx.seed ?? "")
            ? "address_discovery"
            : ""
      );
  const appliedInputMode: "hash" | "address" =
    appliedQueryKind === "explicit_hash" || !appliedQueryKind ? "hash" : "address";
  const taskUiKey = `${viewId}:tx:${transactionSubject(
    tx as Record<string, unknown>,
    baseScope,
  )}`;
  const restoredUi = transactionTaskUi.get(taskUiKey);
  const [inputMode, setInputMode] = useState<"hash" | "address">(
    restoredUi?.inputMode ?? appliedInputMode,
  );
  const [input, setInput] = useState("");
  const [subview, setSubview] = useState<TransactionSubview>(
    restoredUi?.subview ?? (appliedInputMode === "address" ? "activity" : "receipt"),
  );
  // Chain selection is local until a request carries it (see the picker).
  const [chainChoice, setChainChoice] = useState<number | null>(null);
  const chainOptions = server.transactions?.chain_options ?? [];
  const serverChainId =
    server.transactions?.scope?.chain_id ??
    server.transactions?.chain_id ??
    GNOSIS_CHAIN_ID;
  const activeChainId = chainChoice ?? serverChainId;
  const activeChain = chainOptions.find((o) => o.chain_id === activeChainId);
  // Read by issueRequest, which is defined before this value in render order.
  const activeChainIdRef = useRef(activeChainId);
  activeChainIdRef.current = activeChainId;
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
  const [discoveryWindowMode, setDiscoveryWindowMode] = useState<"all" | "custom">(
    restoredUi?.discoveryWindowMode ?? initialDiscoveryFilters.windowMode,
  );
  const [discoveryT0, setDiscoveryT0] = useState(
    restoredUi?.discoveryT0 ?? initialDiscoveryFilters.t0,
  );
  const [discoveryT1, setDiscoveryT1] = useState(
    restoredUi?.discoveryT1 ?? initialDiscoveryFilters.t1,
  );
  const [discoveryActivityKinds, setDiscoveryActivityKinds] = useState<
    DiscoveryActivityKind[]
  >(
    restoredUi?.discoveryActivityKinds ?? initialDiscoveryFilters.activityKinds,
  );
  const [discoveryTokenInput, setDiscoveryTokenInput] = useState(
    restoredUi?.discoveryTokenInput ?? initialDiscoveryFilters.tokens.join(", "),
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
  const [autoDiscovering, setAutoDiscovering] = useState(false);
  const automaticDiscoveryRef = useRef<AutomaticDiscovery | null>(null);
  const discoveryScope = objectValue(txRecord.discovery_scope ?? baseScope);
  const receiptScope = objectValue(txRecord.receipt_scope ?? baseScope);
  // Switching the search kind creates a new local subject immediately. Do
  // not let the previous address failure/evidence masquerade as receipt
  // evidence (or vice versa) while the analyst is composing the new query.
  const activeForensicScope = inputMode !== appliedInputMode
    ? undefined
    : (
        subview === "receipt" && receiptScope.scope_id
          ? txRecord.receipt_scope ?? tx.scope
          : txRecord.discovery_scope ?? tx.scope
      ) as ForensicScope | undefined;
  const scope = objectValue(activeForensicScope);
  const taskUiKeyRef = useRef(taskUiKey);
  const restoringTaskUiRef = useRef(false);
  const overviewRef = useRef<HTMLElement | null>(null);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const hasSelection = Boolean(selectedNodeId || selectedLegId);
  const appliedDiscoveryFilters = readAppliedDiscoveryFilters(
    server.transactions,
    discoveryScope,
  );
  const discoveryTokens = tokenAddressesFromInput(discoveryTokenInput);
  const invalidDiscoveryTokens = discoveryTokens.filter((token) => !ADDR_RE.test(token));
  const effectiveDiscoveryActivityKinds: DiscoveryActivityKind[] = discoveryTokens.length
    ? ["erc20"]
    : discoveryActivityKinds;
  const hasOneDiscoveryBound = Boolean(discoveryT0) !== Boolean(discoveryT1);
  const discoveryBoundsOrdered = !discoveryT0 || !discoveryT1 || (
    Date.parse(`${discoveryT0}:00Z`) < Date.parse(`${discoveryT1}:00Z`)
  );
  const discoveryFiltersValid = Boolean(
    effectiveDiscoveryActivityKinds.length &&
      invalidDiscoveryTokens.length === 0 &&
      (discoveryWindowMode === "all" || (
        !hasOneDiscoveryBound &&
        Boolean(discoveryT0 && discoveryT1) &&
        discoveryBoundsOrdered
      )),
  );
  const draftDiscoveryFilters: AppliedDiscoveryFilters = {
    windowMode: discoveryWindowMode,
    t0: discoveryWindowMode === "custom" ? discoveryT0 : "",
    t1: discoveryWindowMode === "custom" ? discoveryT1 : "",
    activityKinds: effectiveDiscoveryActivityKinds,
    tokens: discoveryTokens,
  };
  const appliedDiscoverySummary = discoveryFilterSummary(appliedDiscoveryFilters);
  const draftDiscoverySummary = discoveryFilterSummary(draftDiscoveryFilters);
  const discoveryFiltersDirty = JSON.stringify(draftDiscoveryFilters) !==
    JSON.stringify(appliedDiscoveryFilters);

  const { legs, nodes } = useMemo(
    () => buildTxGraphModel(txNodes?.rows, txLegs?.rows),
    [txNodes?.rows, txLegs?.rows],
  );
  const groups = useMemo(() => groupLegsByTx(legs), [legs]);
  const discoveryRows = useMemo(
    () => parseTxListRows(txList?.rows),
    [txList?.rows],
  );
  const contextRows = useMemo(
    () => parseTxContextRows(txContext?.rows, txContext?.columns),
    [txContext?.columns, txContext?.rows],
  );
  const loadedHashes = useMemo(
    () => stableIds(txRecord.result_hashes ?? tx.tx_hashes ?? []),
    [tx.tx_hashes, txRecord.result_hashes],
  );
  const appliedWindowScope = appliedQueryKind === "explicit_hash"
    ? scope
    : discoveryScope;
  const scopeWindow = objectValue(appliedWindowScope.window);
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
  const showingDiscovery = isDiscoveryScope && subview === "activity";

  const issueRequest = (settings: Partial<TxSettings>, label: string) => {
    // Stamp the chain on every request from this view; the server defaults to
    // Gnosis when it is absent, which would silently ignore the picker.
    const withChain = { ...settings, chain: String(activeChainIdRef.current) };
    setPendingIntent({ settings: withChain, label });
    requestTransactions(withChain);
  };

  const stopAutomaticDiscovery = () => {
    automaticDiscoveryRef.current = null;
    setAutoDiscovering(false);
  };

  const startAutomaticDiscovery = (address: string) => {
    automaticDiscoveryRef.current = {
      address: address.toLowerCase(),
      lastScopeRevision: String(scope.scope_id ?? scope.request_id ?? ""),
      seenCursors: new Set<string>(),
    };
    setAutoDiscovering(true);
  };

  const requestDiscoveryControls = (
    controlSettings: Partial<TxSettings>,
    preserveExactWindow: boolean,
    label?: string,
  ) => {
    const exactWindow = preserveExactWindow &&
      appliedDiscoveryFilters.windowMode === "custom";
    const requestSettings: Partial<TxSettings> = {
      operation: "discover",
      // Discovery also publishes its found hashes; explicitly retire those as
      // a request subject so a Range/Txs edit reruns the original predicate.
      txHashes: [],
      seed: String(tx.query?.address ?? tx.seed ?? ""),
      counterparties: (tx.counterparties ?? []).map(String),
      tokens: controlSettings.tokens ?? appliedDiscoveryFilters.tokens,
      activityKinds:
        controlSettings.activityKinds ?? appliedDiscoveryFilters.activityKinds,
      minUsd: Number(tx.min_usd) || 0,
      t0: controlSettings.t0 ?? (
        exactWindow ? utcBoundFromInput(appliedDiscoveryFilters.t0) : ""
      ),
      t1: controlSettings.t1 ?? (
        exactWindow ? utcBoundFromInput(appliedDiscoveryFilters.t1) : ""
      ),
      ...controlSettings,
      cursor: "",
      pageSize: (controlSettings.maxTxs ?? Number(tx.max_txs)) || 25,
    };
    startAutomaticDiscovery(String(requestSettings.seed ?? ""));
    issueRequest(
      requestSettings,
      label ?? `Reloading address discovery for ${shortAddr(String(tx.query?.address ?? tx.seed ?? ""))}`,
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
    setDiscoveryWindowMode(
      restored?.discoveryWindowMode ?? appliedDiscoveryFilters.windowMode,
    );
    setDiscoveryT0(restored?.discoveryT0 ?? appliedDiscoveryFilters.t0);
    setDiscoveryT1(restored?.discoveryT1 ?? appliedDiscoveryFilters.t1);
    setDiscoveryActivityKinds(
      restored?.discoveryActivityKinds ?? appliedDiscoveryFilters.activityKinds,
    );
    setDiscoveryTokenInput(
      restored?.discoveryTokenInput ?? appliedDiscoveryFilters.tokens.join(", "),
    );
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
      discoveryWindowMode,
      discoveryT0,
      discoveryT1,
      discoveryActivityKinds,
      discoveryTokenInput,
      canvasOpen,
      detailsOpen,
      subview,
    });
  }, [
    activeTxHash,
    canvasOpen,
    detailsOpen,
    discoveryActivityKinds,
    discoveryT0,
    discoveryT1,
    discoveryTokenInput,
    discoveryWindowMode,
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
  const activeContext = contextRows.find((row) => row.txHash === activeTxHash.toLowerCase());
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
  const selectedEventKind = selectedLeg
    ? transferEventKind(
        selectedLeg,
        nodes.find((node) => node.id === selectedLeg.source)?.role,
        nodes.find((node) => node.id === selectedLeg.target)?.role,
      )
    : undefined;

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
    stopAutomaticDiscovery();
    setActiveTxHash(txHash);
    setSubview("receipt");
    // A selected leg or participant from another receipt must never remain in
    // an inspector beside a graph that cannot contain it.
    setSelectedLegId("");
    setSelectedNodeId("");
    if (!groups.some((group) => group.txHash === txHash)) {
      issueRequest(
        {
          operation: "receipt",
          txHashes: [txHash],
          seed: "",
          merge: true,
        },
        `Loading RPC receipt ${txHash}`,
      );
    }
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

  const discoveryRequestFilters = (): Pick<
    TxSettings,
    "tokens" | "activityKinds" | "t0" | "t1" | "rangeDays"
  > => ({
    tokens: discoveryTokens,
    activityKinds: effectiveDiscoveryActivityKinds,
    t0: discoveryWindowMode === "custom"
      ? utcBoundFromInput(discoveryT0)
      : "",
    t1: discoveryWindowMode === "custom"
      ? utcBoundFromInput(discoveryT1)
      : "",
    rangeDays: 0,
  });

  const applyDiscoveryFilters = () => {
    const address = String(tx.query?.address ?? tx.seed ?? "");
    if (!ADDR_RE.test(address) || !discoveryFiltersValid) return;
    const requestFilters = discoveryRequestFilters();
    requestDiscoveryControls(
      requestFilters,
      false,
      `Applying ${draftDiscoverySummary} to ${shortAddr(address)}`,
    );
  };

  const submit = () => {
    const value = input.trim();
    if (!value) return;
    if (inputMode === "hash" && TX_HASH_RE.test(value)) {
      const hash = value.toLowerCase();
      stopAutomaticDiscovery();
      setInput(hash);
      setSubview("receipt");
      issueRequest(
        { operation: "receipt", txHashes: [hash], seed: "" },
        `Opening transaction ${hash}`,
      );
    } else if (inputMode === "address" && ADDR_RE.test(value)) {
      const address = value.toLowerCase();
      if (!discoveryFiltersValid) return;
      startAutomaticDiscovery(address);
      setInput(address);
      setSubview("activity");
      setActiveTxHash("");
      setDetailsOpen(false);
      setEvidenceOpen(false);
      issueRequest(
        {
          operation: "discover",
          seed: address,
          txHashes: [],
          counterparties: [],
          ...discoveryRequestFilters(),
          maxTxs: (txsPick ?? Number(tx.max_txs)) || 25,
          pageSize: (txsPick ?? Number(tx.max_txs)) || 25,
          cursor: "",
        },
        `Searching ${draftDiscoverySummary} for ${address}`,
      );
    }
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
      // Forward expansion is the legacy cursor-to-head operation. The
      // candidate-only `discover` contract intentionally rejects
      // expand_node_id; using it here would make every graph Follow action a
      // deterministic backend validation failure.
      operation: "legacy",
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
  const lastAttempt = objectValue(txRecord.last_attempt);
  const lastAttemptScope = objectValue(lastAttempt.scope);
  const rawTransactionFailure =
    loadError ||
    (lastAttempt.status === "failed"
      ? String(
          lastAttempt.error ??
          lastAttemptScope.error ??
          "Transaction request failed; the last applied evidence was preserved.",
        )
      : scopeStatus === "failed"
        ? String(scope.error ?? scope.warnings ?? "source scope failed")
        : txList?.phase === "failed" && showingDiscovery
          ? txList.error || "address-discovery hydration failed"
          : txLegs?.phase === "failed" && (!isDiscoveryScope || subview === "receipt")
            ? txLegs.error || "transfer-leg hydration failed"
            : null);
  const failureQueryKind = String(
    lastAttempt.query_kind ??
    (pendingIntent?.settings.operation === "receipt"
      ? "explicit_hash"
      : pendingIntent?.settings.operation === "discover"
        ? "address_discovery"
        : appliedQueryKind),
  );
  const visibleFailureKind = inputMode === "hash" || subview === "receipt"
    ? "receipt"
    : "discovery";
  const failureKind = failureQueryKind === "explicit_hash" ||
      failureQueryKind === "receipt"
    ? "receipt"
    : "discovery";
  const transactionFailure = failureKind === visibleFailureKind
    ? rawTransactionFailure
    : null;
  // TWO different completenesses, and conflating them is exactly the failure
  // the scope contract exists to prevent. "179 of 179 legs" while the badge
  // says PARTIAL reads as a contradiction; what was capped is the TRANSACTION
  // SET, not the legs of the transactions on screen.
  const legsComplete = legsTotal != null && legsReturned >= legsTotal;
  const discoveryCoverage = objectValue(
    txRecord.discovery_coverage ?? discoveryScope.discovery_coverage,
  );
  const nextDiscoveryCursor = String(discoveryCoverage.next_cursor ?? "");
  const moreTxsAvailable = Boolean(
    discoveryScope.more_transactions_available || nextDiscoveryCursor,
  );
  const transactionLowerBound = numeric(
    discoveryCoverage.total_lower_bound ?? discoveryScope.txs_total_lower_bound,
  );
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
    ...discoveryRows.map((row) => row.txHash),
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
  const discoveryTotalExact = numeric(
    discoveryCoverage.total_exact ?? discoveryScope.txs_total_exact,
  );
  const discoveryComplete = Boolean(
    discoveryCoverage.complete ?? discoveryScope.discovery_complete,
  );
  const uncoveredDiscoveryRanges = Array.isArray(discoveryCoverage.uncovered_ranges)
    ? discoveryCoverage.uncovered_ranges
    : [];
  const olderHistoryUnscanned = Boolean(discoveryCoverage.older_history_unscanned);
  const discoveryHasNoRows = Boolean(
    showingDiscovery &&
      !loading &&
      !autoDiscovering &&
      !transactionFailure &&
      txList?.phase !== "loading" &&
      txList?.phase !== "failed" &&
      transactionCount === 0 &&
      scope.scope_id,
  );
  const verifiedEmptyDiscovery = Boolean(
    discoveryHasNoRows &&
    verificationStatus === "verified" &&
    scopeStatus !== "failed" &&
    discoveryComplete &&
    uncoveredDiscoveryRanges.length === 0 &&
    !olderHistoryUnscanned &&
    discoveryTotalExact === 0,
  );
  const unresolvedEmptyDiscovery = discoveryHasNoRows && !verifiedEmptyDiscovery;
  const hasAcceptedEvidence = [discoveryScope, receiptScope].some(
    (candidate) => Boolean(
      candidate.scope_id && ["ready", "partial"].includes(String(candidate.status ?? "")),
    ),
  ) || discoveryRows.length > 0 || legs.length > 0;
  const badge =
    txList?.phase === "failed" && showingDiscovery
      ? "FAILED ADDRESS DISCOVERY HYDRATION"
      : txLegs?.phase === "failed" && !showingDiscovery
      ? `FAILED RECEIPT HYDRATION · ${rowsLoaded}/${rowsExpected ?? "?"}`
      : transactionFailure
        ? showingDiscovery
          ? "FAILED ADDRESS DISCOVERY"
          : "FAILED RECEIPT INSPECTION"
      : txLegs?.phase === "loading"
        ? `LOADING RECEIPT LEGS · ${rowsLoaded}/${rowsExpected ?? "?"}`
        : verifiedEmptyDiscovery
          ? "NO MATCHES · VERIFIED ADDRESS DISCOVERY"
          : showingDiscovery
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
  const transactionEvidenceSummary = showingDiscovery
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
      } match · older stored history remains available`
    : legsTruncated
      ? "4,000-leg safety cap · whole transactions dropped, never split"
      : explicitHashes.length
        ? "RPC receipt authority · ERC-20 Transfer logs"
        : String(scope.discovery_path ?? "") === "execution_tables_rpc_tail"
          ? "Execution transactions + Transfer logs + RPC head · authoritative RPC receipt legs"
          : String(scope.discovery_path ?? "") === "execution_logs_window"
            ? "Applied-window Transfer-log discovery · authoritative RPC receipt legs"
            : "Scoped chain discovery · RPC receipt legs";
  const hasInspector = Boolean(activeTxHash || hasSelection);
  const fallbackRetrySettings: Partial<TxSettings> = {
    operation: explicitHashes.length ? "receipt" : "discover",
    txHashes: explicitHashes,
    seed: explicitHashes.length ? "" : String(tx.seed ?? ""),
    counterparties: (tx.counterparties ?? []).map(String),
    tokens: appliedDiscoveryFilters.tokens,
    activityKinds: appliedDiscoveryFilters.activityKinds,
    maxTxs: Number(tx.max_txs) || 25,
    ...(!explicitHashes.length && [
      "execution_tables_plus_rpc_head",
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
  useEffect(() => {
    if (!transactionFailure) return;
    setTxsPick(null);
  }, [transactionFailure]);

  useEffect(() => {
    if (
      loading ||
      transactionFailure ||
      pendingIntent?.settings.operation !== "discover"
    ) return;
    setDiscoveryWindowMode(appliedDiscoveryFilters.windowMode);
    setDiscoveryT0(appliedDiscoveryFilters.t0);
    setDiscoveryT1(appliedDiscoveryFilters.t1);
    setDiscoveryActivityKinds(appliedDiscoveryFilters.activityKinds);
    setDiscoveryTokenInput(appliedDiscoveryFilters.tokens.join(", "));
  // The scope revision is the server acknowledgement for the pending
  // snapshot. Draft typing without a request must never be overwritten by an
  // unrelated receipt response.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, scopeRevision, transactionFailure]);

  useEffect(() => {
    const automatic = automaticDiscoveryRef.current;
    if (!automatic || loading) return;
    if (automatic.lastScopeRevision === scopeRevision) return;
    automatic.lastScopeRevision = scopeRevision;

    const appliedAddress = String(tx.query?.address ?? tx.seed ?? "").toLowerCase();
    const pageSize = Number(tx.query?.page_size ?? tx.max_txs) || 25;
    if (
      transactionFailure ||
      !showingDiscovery ||
      appliedAddress !== automatic.address ||
      discoveryRows.length >= pageSize ||
      verifiedEmptyDiscovery ||
      !nextDiscoveryCursor ||
      automatic.seenCursors.has(nextDiscoveryCursor)
    ) {
      stopAutomaticDiscovery();
      return;
    }

    automatic.seenCursors.add(nextDiscoveryCursor);
    const settings: Partial<TxSettings> = {
      operation: "discover",
      seed: automatic.address,
      txHashes: [],
      counterparties: tx.query?.counterparties ?? tx.counterparties ?? [],
      tokens: tx.query?.tokens ?? tx.tokens ?? [],
      cursor: nextDiscoveryCursor,
      pageSize,
      maxTxs: pageSize,
      activityKinds: tx.query?.activity_kinds ?? ["direct", "erc20"],
    };
    setPendingIntent({
      settings,
      label: `Searching older stored history for ${shortAddr(automatic.address)}`,
    });
    requestTransactions(settings);
  // The scope revision is the acknowledgement boundary. Query fields are
  // read from that immutable acknowledged snapshot before the next cursor is
  // enqueued, so draft edits cannot leak into an automatic continuation.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    discoveryRows.length,
    loading,
    nextDiscoveryCursor,
    scopeRevision,
    showingDiscovery,
    transactionFailure,
    verifiedEmptyDiscovery,
  ]);

  useEffect(() => {
    // A successful response adopts the pending snapshot. A failed response
    // deliberately retains it so Retry replays the exact subject the analyst
    // asked for rather than silently falling back to the previous receipt.
    if (!loading && !transactionFailure && !automaticDiscoveryRef.current) {
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
        {/* Chain picker — Transaction Detail only. Receipts come from RPC and
          * work on any configured chain; the other modes read the single-chain
          * Gnosis warehouse, so offering a selector there would be a dead
          * control. Hidden entirely when only one chain is configured. */}
        {chainOptions.length > 1 ? (
          <label className="ge-tx-chain" title="Chain to read receipts from">
            <span className="ge-tx-chain__label">Chain</span>
            <ChainBadge chainId={activeChainId} showName={false} />
            <select
              value={activeChainId}
              aria-label="Chain"
              onChange={(event) => {
                const next = Number(event.target.value);
                if (next === activeChainId) return;
                // The subject belongs to the old chain: a hash or address means
                // nothing on the new one. Clear it rather than re-running it
                // somewhere it was never seen.
                stopAutomaticDiscovery();
                setInput("");
                setActiveTxHash("");
                clearSelection();
                setDetailsOpen(false);
                setEvidenceOpen(false);
                setPendingIntent(null);
                const option = chainOptions.find((o) => o.chain_id === next);
                if (option && !option.supports_address_discovery) {
                  setInputMode("hash");
                  setSubview("receipt");
                }
                setChainChoice(next);
              }}
            >
              {chainOptions.map((option) => (
                <option key={option.chain_id} value={option.chain_id}>
                  {option.name}
                  {option.supports_address_discovery ? "" : " · hash only"}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {activeChain && !activeChain.supports_address_discovery ? (
          // Rendered at BAR level, not inside the discovery controls: those
          // only mount in address mode, so a user who just switched to a
          // hash-only chain would have seen no explanation at all.
          <p className="ge-tx-chain-note" role="note">
            Address search needs the indexed execution tables, which exist for
            Gnosis only. On {activeChain.name} open a transaction hash — its
            receipt is read from this chain's RPC. USD is unavailable here and
            token symbols are not resolved, so amounts show the raw value and
            the token address.
          </p>
        ) : null}
        <div className="ge-tx-query-kind" role="group" aria-label="Transaction search mode">
          <button
            type="button"
            className={inputMode === "hash" ? "active" : ""}
            aria-pressed={inputMode === "hash"}
            onClick={() => {
              stopAutomaticDiscovery();
              setInputMode("hash");
              setSubview("receipt");
              setInput("");
              setActiveTxHash("");
              clearSelection();
              setDetailsOpen(false);
              setEvidenceOpen(false);
              setPendingIntent(null);
            }}
          >
            Transaction hash
          </button>
          <button
            type="button"
            className={inputMode === "address" ? "active" : ""}
            aria-pressed={inputMode === "address"}
            onClick={() => {
              stopAutomaticDiscovery();
              setInputMode("address");
              setSubview("activity");
              setInput("");
              setActiveTxHash("");
              clearSelection();
              setDetailsOpen(false);
              setEvidenceOpen(false);
              setPendingIntent(null);
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
              : "Find normal transactions and token transfers in stored execution history plus the RPC head, then verify each selected receipt by RPC"
          }
        />
        <button
          type="button"
          className="ge-btn ge-btn--primary"
          onClick={submit}
          disabled={
            !input.trim() ||
            !inputValid ||
            loading ||
            autoDiscovering ||
            (inputMode === "address" && !discoveryFiltersValid)
          }
        >
          {inputMode === "hash" ? "Open receipt →" : "Search activity →"}
        </button>
        {!inputValid && (
          <span className="ge-tx-invalid">
            {inputMode === "hash" ? "needs a 66-character transaction hash" : "needs a 42-character address"}
          </span>
        )}
        {inputMode === "address" && (
        <FilterDrawer
          label="Filters"
          className="ge-filter-drawer--tx"
          collapsibleOnDesktop
        >
          <div
            className="ge-field ge-tx-discovery-contract"
            title="Stored execution transactions and Transfer logs cover history; RPC scans only the uncovered head. Every selected result is decoded from its RPC receipt."
          >
            <span>Default scope</span>
            <strong>
              {activeChain && !activeChain.supports_address_discovery
                ? `${activeChain.name} · receipts by hash only`
                : "All stored history + RPC head"}
            </strong>
          </div>

          <fieldset className="ge-tx-filter-group ge-tx-filter-group--activity">
            <legend>Activity</legend>
            <label title="Normal transactions where the address is sender or recipient">
              <input
                type="checkbox"
                checked={
                  !discoveryTokenInput.trim() &&
                  discoveryActivityKinds.includes("direct")
                }
                disabled={Boolean(discoveryTokenInput.trim())}
                onChange={(event) => {
                  setDiscoveryActivityKinds((current) => event.target.checked
                    ? normalizedActivityKinds([...current, "direct"])
                    : current.filter((kind) => kind !== "direct"));
                }}
              />
              Direct transactions
            </label>
            <label title="Standard ERC-20 Transfer event candidates">
              <input
                type="checkbox"
                checked={
                  Boolean(discoveryTokenInput.trim()) ||
                  discoveryActivityKinds.includes("erc20")
                }
                disabled={Boolean(discoveryTokenInput.trim())}
                onChange={(event) => {
                  setDiscoveryActivityKinds((current) => event.target.checked
                    ? normalizedActivityKinds([...current, "erc20"])
                    : current.filter((kind) => kind !== "erc20"));
                }}
              />
              ERC-20 transfers
            </label>
          </fieldset>

          <label className="ge-field ge-tx-token-filter">
            <span>Token addresses <small>optional</small></span>
            <input
              type="text"
              className="ge-input"
              aria-label="Token address filter"
              placeholder="0x… (comma-separated)"
              value={discoveryTokenInput}
              onChange={(event) => setDiscoveryTokenInput(event.target.value)}
              spellCheck={false}
            />
            <small>A token filter applies to ERC-20 candidates only.</small>
          </label>

          <fieldset className="ge-tx-filter-group ge-tx-filter-group--window">
            <legend>UTC bounds</legend>
            <label>
              <input
                type="radio"
                name={`${viewId}-tx-window`}
                value="all"
                checked={discoveryWindowMode === "all"}
                onChange={() => setDiscoveryWindowMode("all")}
              />
              All available history
            </label>
            <label>
              <input
                type="radio"
                name={`${viewId}-tx-window`}
                value="custom"
                checked={discoveryWindowMode === "custom"}
                onChange={() => setDiscoveryWindowMode("custom")}
              />
              Custom UTC window
            </label>
          </fieldset>

          {discoveryWindowMode === "custom" ? (
            <div className="ge-tx-window-inputs">
              <label className="ge-field">
                <span>From UTC</span>
                <input
                  type="datetime-local"
                  aria-label="From UTC"
                  value={discoveryT0}
                  onChange={(event) => setDiscoveryT0(event.target.value)}
                />
              </label>
              <label className="ge-field">
                <span>To UTC</span>
                <input
                  type="datetime-local"
                  aria-label="To UTC"
                  value={discoveryT1}
                  onChange={(event) => setDiscoveryT1(event.target.value)}
                />
              </label>
            </div>
          ) : null}

          <label className="ge-field" title="Maximum candidates admitted per discovery page">
            <span>Results per page</span>
            <select
              value={txsPick ?? (Number(tx.max_txs) || 25)}
              onChange={(e) => {
                const v = Number(e.target.value);
                setTxsPick(v);
                if (isDiscoveryScope && (tx.query?.address || tx.seed)) {
                  // Admission changes do not change the applied predicate.
                  requestDiscoveryControls({ maxTxs: v }, true);
                }
              }}
            >
              {MAX_TX_OPTIONS.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>

          {!discoveryFiltersValid ? (
            <span className="ge-tx-filter-error" role="alert">
              {invalidDiscoveryTokens.length
                ? "Every token filter must be a 42-character address."
                : hasOneDiscoveryBound || !discoveryT0 || !discoveryT1
                  ? "Enter both UTC bounds."
                  : !discoveryBoundsOrdered
                    ? "The From UTC bound must be earlier than To UTC."
                    : "Select at least one activity kind."}
            </span>
          ) : null}

          <div className="ge-tx-filter-actions">
            <button
              type="button"
              className="ge-btn"
              onClick={() => {
                setDiscoveryWindowMode("all");
                setDiscoveryT0("");
                setDiscoveryT1("");
                setDiscoveryActivityKinds(["direct", "erc20"]);
                setDiscoveryTokenInput("");
              }}
            >
              Clear filters
            </button>
            <button
              type="button"
              className="ge-btn ge-btn--primary"
              disabled={
                !isDiscoveryScope ||
                !ADDR_RE.test(String(tx.query?.address ?? tx.seed ?? "")) ||
                !discoveryFiltersValid ||
                !discoveryFiltersDirty ||
                loading ||
                autoDiscovering
              }
              onClick={applyDiscoveryFilters}
            >
              Apply to address
            </button>
          </div>
        </FilterDrawer>
        )}
        {inputMode === "address" ? (
          <div className="ge-tx-filter-status" aria-live="polite">
            <span title={appliedDiscoverySummary}>
              {isDiscoveryScope ? `Applied · ${appliedDiscoverySummary}` : `Draft · ${draftDiscoverySummary}`}
            </span>
            {loading && pendingIntent ? (
              <span className="is-pending" title={pendingIntent.label}>
                Pending · {pendingIntent.label}
              </span>
            ) : discoveryFiltersDirty && isDiscoveryScope ? (
              <span className="is-draft" title={draftDiscoverySummary}>
                Draft not applied · {draftDiscoverySummary}
              </span>
            ) : null}
          </div>
        ) : null}
        <div className="ge-topbar-right">
          <EvidenceTrigger
            scope={activeForensicScope}
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
            : {transactionFailure}.
            {hasAcceptedEvidence ? " The last applied evidence remains on screen." : ""}
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
      {(loading || autoDiscovering) && !showingDiscovery && (
        <div className="ge-tx-busy" role="status">
          <span className="ge-tx-spinner" aria-hidden />
          {pendingIntent?.label || "Searching stored transaction history…"}
          {autoDiscovering ? (
            <button
              type="button"
              className="ge-btn"
              onClick={stopAutomaticDiscovery}
            >
              Stop
            </button>
          ) : null}
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
          {isDiscoveryScope && subview === "activity" && !transactionFailure &&
            !verifiedEmptyDiscovery && (
            <DiscoveryResults
              rows={discoveryRows}
              address={String(tx.query?.address ?? tx.seed ?? "")}
              targetRows={Number(tx.query?.page_size ?? tx.max_txs) || 25}
              activeTxHash={activeTxHash}
              receiptStatuses={receiptStatuses}
              onChoose={chooseTransaction}
              canLoadMore={unresolvedEmptyDiscovery || Boolean(nextDiscoveryCursor) || (
                moreTxsAvailable && Number(tx.max_txs ?? 25) < 100
              )}
              loading={loading || autoDiscovering}
              automatic={autoDiscovering}
              onStop={stopAutomaticDiscovery}
              emptyActionLabel={nextDiscoveryCursor || olderHistoryUnscanned
                ? "Continue older history"
                : "Retry discovery"}
              onLoadMore={() => {
                if (nextDiscoveryCursor) {
                  const address = String(tx.query?.address ?? tx.seed ?? "");
                  startAutomaticDiscovery(address);
                  issueRequest(
                    {
                      operation: "discover",
                      seed: address,
                      txHashes: [],
                      counterparties: tx.query?.counterparties ?? tx.counterparties ?? [],
                      tokens: tx.query?.tokens ?? tx.tokens ?? [],
                      cursor: nextDiscoveryCursor,
                      pageSize: Number(tx.query?.page_size ?? tx.max_txs) || 25,
                      maxTxs: Number(tx.query?.page_size ?? tx.max_txs) || 25,
                      activityKinds: tx.query?.activity_kinds ?? ["direct", "erc20"],
                    },
                    `Loading older activity for ${shortAddr(address)}`,
                  );
                  return;
                }
                if (unresolvedEmptyDiscovery) {
                  retryTransactionLoad();
                  return;
                }
                const current = Number(tx.max_txs ?? 25);
                const next = Math.min(100, current < 50 ? 50 : current * 2);
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
                  window.requestAnimationFrame(() => {
                    const workspace = workspaceRef.current;
                    if (!workspace) return;
                    if (typeof workspace.scrollTo === "function") {
                      workspace.scrollTo({ top: 0 });
                    } else {
                      workspace.scrollTop = 0;
                    }
                  });
                }}
              >
                ← Address activity
              </button>
              <code title={activeTxHash}>{activeTxHash}</code>
            </div>
          ) : null}

          {verifiedEmptyDiscovery && (
            <section className="ge-tx-empty-discovery" role="status">
              <strong>No direct or standard ERC-20 Transfer transactions found</strong>
              <p>
                Address <code>{String(tx.seed ?? "unknown")}</code> had no matching
                {" direct transaction or standard ERC-20 Transfer activity in complete stored history plus the RPC head scan."}
              </p>
              <p>
                Stored address history and its uncovered RPC tail were checked through block {String(scope.data_horizon ?? "unknown")}.
              </p>
              <div>
                <span>Direct native transactions are discoverable; internal calls and non-standard token events remain outside this search.</span>
              </div>
            </section>
          )}

          {!activeTxHash && inputMode === "hash" && !transactionFailure && !loading && (
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
          {activeContext ? <TransactionContext context={activeContext} /> : null}
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
                    height="100%"
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
                        className={selected ? "is-selected" : ""}
                      >
                        <td>
                          <button
                            type="button"
                            className="ge-link-btn"
                            aria-pressed={selected}
                            aria-label={`Inspect transfer ${index + 1}, log ${leg.logIndex}, ${
                              leg.symbol || leg.tokenAddress || "unknown token"
                            }`}
                            onClick={() => selectLeg(leg.id)}
                          >
                            {index + 1}
                          </button>
                        </td>
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
                        <td>{transferEventKind(leg, srcNode?.role, tgtNode?.role)}</td>
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
                            href={txUrl(leg.txHash, activeChainId, chainOptions)}
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
            chainId={activeChainId}
            chainOptions={chainOptions}
            transaction={inspectorGroup}
            receiptStatus={String(
              receiptStatuses[activeTxHash] ??
                activeGroup?.legs[0]?.txStatus ??
                "unknown",
            )}
            context={activeContext}
            selectedEventKind={selectedEventKind}
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
          scope={activeForensicScope}
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
