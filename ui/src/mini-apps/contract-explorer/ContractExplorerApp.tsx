import { useMemo, useState } from "react";
import { useMiniApp } from "../shared/useMiniApp";
import { ToastStack } from "../shared/ToastStack";
import { MiniAppChrome, MaIdentity, MaSection } from "../shared/MiniAppChrome";
import { MaHelpButton } from "../shared/HelpDialog";
import { CONTRACT_EXPLORER_HELP } from "../shared/helpContent";
import { ChainBadge } from "../shared/ChainBadge";
import { AsyncButton } from "../shared/AsyncButton";
import { shortAddr } from "../../utils/format";
import { HistoryChart } from "./HistoryChart";
import type { HistorySeries } from "./historyChartOption";

// ---------------------------------------------------------------------------
// Wire types — mirror src/cerebro_mcp/tools/contract_explorer.py
// ---------------------------------------------------------------------------

interface AbiInput {
  name: string;
  type: string;
  indexed?: boolean;
}

interface AbiFunction {
  name: string;
  signature: string;
  stateMutability: string;
  inputs: AbiInput[];
  outputs: AbiInput[];
}

interface AbiEvent {
  name: string;
  signature: string;
  inputs: AbiInput[];
}

interface CallEntry {
  function: string;
  signature: string;
  args: unknown[];
  block: string | number;
  called_at: string;
  ok: boolean;
  result?: unknown;
  error?: string;
  elapsed_seconds: number;
}

interface ExplorerInfo {
  provider: string;
  brand: string;
  base_url: string;
  transaction_url_template: string;
  address_url_template: string;
  token_url_template: string;
  api_base_url: string;
}

interface ChainOption {
  chain_id: number;
  name: string;
  native_symbol: string;
  environment: string;
  explorer: ExplorerInfo;
  icon_url?: string;
}

interface ContractExplorerState {
  address: string;
  chain_id: number;
  chain_name: string;
  chain_options: ChainOption[];
  explorer: ExplorerInfo | null;
  contract_name: string;
  abi_source: string;
  implementation_address: string;
  target: "auto" | "implementation" | "proxy";
  read_functions: AbiFunction[];
  write_functions: AbiFunction[];
  events: AbiEvent[];
  call_history: CallEntry[];
  history: HistorySeries[];
  warnings: string[];
}

const APP_ID = "contract_explorer";

/** Preset sweep windows. Values are what the backend's `since` accepts. */
const RANGE_PRESETS: ReadonlyArray<readonly [label: string, since: string]> = [
  ["24h", "24h"],
  ["7d", "7d"],
  ["30d", "30d"],
  ["90d", "90d"],
  ["1y", "365d"],
] as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function lastResultPerFunction(history: CallEntry[]): Map<string, CallEntry> {
  // history is most-recent first; keep first entry seen per signature.
  const map = new Map<string, CallEntry>();
  for (const entry of history) {
    if (!map.has(entry.signature)) map.set(entry.signature, entry);
  }
  return map;
}

function renderResult(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

// ---------------------------------------------------------------------------
// Mock payload (Vite dev mode)
// ---------------------------------------------------------------------------

const MOCK_EXPLORER: ExplorerInfo = {
  provider: "blockscout",
  brand: "Blockscout",
  base_url: "https://gnosis.blockscout.com",
  transaction_url_template: "https://gnosis.blockscout.com/tx/{hash}",
  address_url_template: "https://gnosis.blockscout.com/address/{address}",
  token_url_template: "https://gnosis.blockscout.com/token/{address}",
  api_base_url: "https://gnosis.blockscout.com/api/v2",
};

const MOCK_STATE: ContractExplorerState = {
  address: "0x420CA0f9B9b604cE0fd9C18EF134C705e5Fa3430",
  chain_id: 100,
  chain_name: "Gnosis",
  chain_options: [
    {
      chain_id: 100, name: "Gnosis", native_symbol: "xDAI",
      environment: "production", explorer: MOCK_EXPLORER,
    },
    {
      chain_id: 1, name: "Ethereum", native_symbol: "ETH",
      environment: "production",
      explorer: { ...MOCK_EXPLORER, base_url: "https://eth.blockscout.com" },
    },
  ],
  explorer: MOCK_EXPLORER,
  contract_name: "GnosisControllerToken",
  abi_source: "blockscout",
  implementation_address: "0x60cb9FdD0fcFd9BB3b2B721864Db5E7C07F4635D",
  target: "auto",
  read_functions: [
    {
      name: "balanceOf",
      signature: "balanceOf(address)",
      stateMutability: "view",
      inputs: [{ name: "account", type: "address" }],
      outputs: [{ name: "", type: "uint256" }],
    },
    {
      name: "decimals",
      signature: "decimals()",
      stateMutability: "view",
      inputs: [],
      outputs: [{ name: "", type: "uint8" }],
    },
    {
      name: "symbol",
      signature: "symbol()",
      stateMutability: "view",
      inputs: [],
      outputs: [{ name: "", type: "string" }],
    },
    {
      name: "totalSupply",
      signature: "totalSupply()",
      stateMutability: "view",
      inputs: [],
      outputs: [{ name: "", type: "uint256" }],
    },
  ],
  write_functions: [
    {
      name: "transfer",
      signature: "transfer(address,uint256)",
      stateMutability: "nonpayable",
      inputs: [
        { name: "to", type: "address" },
        { name: "amount", type: "uint256" },
      ],
      outputs: [{ name: "", type: "bool" }],
    },
  ],
  events: [],
  call_history: [],
  history: [],
  warnings: [],
};

const MOCK_PAYLOAD = {
  type: "INITIAL_LOAD" as const,
  view_id: "mock-view",
  app_id: APP_ID,
  title: "Contract Explorer",
  status: "ready" as const,
  summary_cards: [
    { label: "Contract", value: "GnosisControllerToken", tone: "neutral" as const },
    { label: "ABI source", value: "blockscout", tone: "neutral" as const },
    { label: "Read functions", value: "4", tone: "neutral" as const },
    { label: "Implementation", value: "0x60cb…635D", tone: "positive" as const },
  ],
  view_state: MOCK_STATE,
  warnings: [],
};

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function ContractExplorerApp() {
  const { view, callTool } = useMiniApp<ContractExplorerState>({
    appId: APP_ID,
    mockPayload: MOCK_PAYLOAD,
  });

  const [pendingAddress, setPendingAddress] = useState("");
  const [pendingTarget, setPendingTarget] = useState<
    "auto" | "implementation" | "proxy"
  >("auto");
  const [pendingChain, setPendingChain] = useState<number | null>(null);
  const [defaultBlock, setDefaultBlock] = useState<string>("latest");
  const [subTab, setSubTab] = useState<"read" | "write" | "events">("read");

  const state = view?.view_state;

  const lastResults = useMemo(
    () => lastResultPerFunction(state?.call_history ?? []),
    [state?.call_history],
  );

  const historyBySignature = useMemo(() => {
    const map = new Map<string, HistorySeries>();
    for (const series of state?.history ?? []) map.set(series.signature, series);
    return map;
  }, [state?.history]);

  // Null means "untouched" — follow the view's chain until the user picks one.
  const chainId = pendingChain ?? state?.chain_id ?? 100;
  const chainOptions = state?.chain_options ?? [];

  if (!view) {
    return (
      <MiniAppChrome activeTabId="contract" rightSlot={<MaHelpButton content={CONTRACT_EXPLORER_HELP} />}>
        <div className="ma-empty">Loading Contract Explorer…</div>
      </MiniAppChrome>
    );
  }

  async function loadAddress(
    addr: string,
    target: typeof pendingTarget,
    chain: number,
  ) {
    if (!addr.trim()) return;
    if (view?.view_id) {
      await callTool("load_contract_explorer_address", {
        view_id: view.view_id,
        address: addr.trim(),
        target,
        chain: String(chain),
      });
    } else {
      await callTool("open_contract_explorer", {
        address: addr.trim(),
        target,
        chain: String(chain),
      });
    }
  }

  const visibleFns: AbiFunction[] =
    subTab === "read"
      ? state?.read_functions ?? []
      : subTab === "write"
        ? state?.write_functions ?? []
        : [];
  const visibleEvents: AbiEvent[] = subTab === "events" ? state?.events ?? [] : [];

  return (
    <MiniAppChrome activeTabId="contract" rightSlot={<MaHelpButton content={CONTRACT_EXPLORER_HELP} />}>
      <div className="contract-explorer-app">
        {/* Load form — always visible, compact */}
        <form
          className="ma-call-row"
          style={{ marginBottom: 14 }}
          onSubmit={(e) => {
            e.preventDefault();
            void loadAddress(pendingAddress, pendingTarget, chainId);
          }}
        >
          <input
            type="text"
            placeholder="0x… paste a contract address"
            value={pendingAddress}
            onChange={(e) => setPendingAddress(e.target.value)}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
          />
          {chainOptions.length > 0 ? (
            <select
              value={chainId}
              onChange={(e) => setPendingChain(Number(e.target.value))}
              title="Chain to resolve and call against"
              className="ce-select"
            >
              {chainOptions.map((opt) => (
                <option key={opt.chain_id} value={opt.chain_id}>
                  {opt.name}
                </option>
              ))}
            </select>
          ) : null}
          <select
            value={pendingTarget}
            onChange={(e) =>
              setPendingTarget(
                e.target.value as "auto" | "implementation" | "proxy",
              )
            }
            className="ce-select"
          >
            <option value="auto">auto (impl ABI on proxies)</option>
            <option value="implementation">implementation only</option>
            <option value="proxy">proxy own ABI</option>
          </select>
          <button type="submit" className="ma-call-btn">
            Load
          </button>
          <input
            type="text"
            value={defaultBlock}
            onChange={(e) => setDefaultBlock(e.target.value)}
            placeholder="latest"
            title="Default block: latest, finalized, safe, or a numeric block."
            style={{ flex: "0 1 140px" }}
          />
        </form>

        <ToastStack
          warnings={[...(view.warnings ?? []), ...(state?.warnings ?? [])]}
        />

        {/* Identity card — only when a contract is loaded */}
        {state?.address ? (
          <MaIdentity
            label={`Contract${state.contract_name ? " · " + state.contract_name : ""}`}
            value={state.address}
            onCopy={() => navigator.clipboard?.writeText(state.address)}
            rightSlot={
              <span className="ce-identity-tags">
                <ChainBadge
                  chainId={state.chain_id}
                  iconUrl={
                    chainOptions.find((c) => c.chain_id === state.chain_id)
                      ?.icon_url
                  }
                />
                {state.explorer?.address_url_template ? (
                  <a
                    className="ce-explorer-link"
                    href={state.explorer.address_url_template.replace(
                      "{address}",
                      state.address,
                    )}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {state.explorer.brand} ↗
                  </a>
                ) : null}
                {state.implementation_address ? (
                  <span
                    className="ce-proxy-tag"
                    title={state.implementation_address}
                  >
                    proxy → {shortAddr(state.implementation_address)}
                  </span>
                ) : null}
              </span>
            }
          />
        ) : (
          <div className="ma-empty">
            Paste a contract address above to inspect its ABI and call any
            view/pure function on any configured chain. Proxies are followed
            automatically.
          </div>
        )}

        {/* Sub-nav: [01] read · [02] write · [03] events */}
        {state?.address ? (
          <div className="ma-subnav">
            {(
              [
                ["read", "01", state.read_functions.length],
                ["write", "02", state.write_functions.length],
                ["events", "03", state.events.length],
              ] as const
            ).map(([id, num, count]) => (
              <button
                key={id}
                type="button"
                className={`ma-subnav-item ${subTab === id ? "is-active" : ""}`}
                onClick={() => setSubTab(id as "read" | "write" | "events")}
              >
                <span className="ma-subnav-num">[{num}]</span> {id}
                {count ? (
                  <span style={{ color: "var(--text-muted)", marginLeft: 4 }}>
                    {count}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        ) : null}

        {/* Function sections */}
        {state?.address && subTab !== "events" && visibleFns.length === 0 ? (
          <div className="ma-empty">
            No {subTab} functions on this contract.
          </div>
        ) : null}

        {state?.address && subTab === "events" && visibleEvents.length === 0 ? (
          <div className="ma-empty">No events on this contract.</div>
        ) : null}

        {visibleFns.map((fn, i) => (
          <MaSection
            key={fn.signature}
            index={`[${String(i + 1).padStart(2, "0")}]`}
            title={fn.signature}
            meta={`${fn.stateMutability} → ${fn.outputs.map((o) => o.type).join(", ") || "void"}`}
          >
            <FunctionCard
              fn={fn}
              viewId={view.view_id}
              callTool={callTool}
              lastResult={lastResults.get(fn.signature)}
              defaultBlock={defaultBlock}
              isWrite={subTab === "write"}
              series={historyBySignature.get(fn.signature)}
            />
          </MaSection>
        ))}

        {visibleEvents.map((ev, i) => (
          <MaSection
            key={ev.signature}
            index={`[${String(i + 1).padStart(2, "0")}]`}
            title={ev.signature}
            meta="event"
          >
            <pre className="ma-abi-block">
              <span className="kw">event</span> {ev.name}(
              {ev.inputs.map((inp, j) => (
                <span key={j}>
                  {"\n  "}
                  {inp.indexed ? <span className="kw">indexed </span> : null}
                  <span className="ty">{inp.type}</span> {inp.name}
                  {j < ev.inputs.length - 1 ? "," : ""}
                </span>
              ))}
              {ev.inputs.length > 0 ? "\n" : ""})
            </pre>
          </MaSection>
        ))}
      </div>
    </MiniAppChrome>
  );
}

// ---------------------------------------------------------------------------
// Function card with inline form
// ---------------------------------------------------------------------------

interface FunctionCardProps {
  fn: AbiFunction;
  viewId: string;
  callTool: <T = unknown>(
    name: string,
    args: Record<string, unknown>,
  ) => Promise<T | null>;
  lastResult?: CallEntry;
  defaultBlock: string;
  isWrite?: boolean;
  series?: HistorySeries;
}

/** Only numeric returns can be plotted over time. */
function isPlottable(fn: AbiFunction): boolean {
  const t = fn.outputs[0]?.type ?? "";
  return /^u?int\d*$/.test(t) || t === "bool";
}

function FunctionCard({
  fn,
  viewId,
  callTool,
  lastResult,
  defaultBlock,
  isWrite,
  series,
}: FunctionCardProps) {
  const [args, setArgs] = useState<string[]>(() =>
    fn.inputs.map(() => ""),
  );
  const [pending, setPending] = useState(false);
  const [callError, setCallError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [range, setRange] = useState("30d");
  const [points, setPoints] = useState("60");
  const [decimals, setDecimals] = useState("");
  const [historyError, setHistoryError] = useState<string | null>(null);

  function setArg(idx: number, value: string) {
    setArgs((prev) => {
      const next = [...prev];
      next[idx] = value;
      return next;
    });
  }

  async function call() {
    setCallError(null);
    setPending(true);
    try {
      const coerced = args.map((v, i) => {
        const t = fn.inputs[i].type;
        if (t.startsWith("uint") || t.startsWith("int")) {
          return v.trim();
        }
        if (t === "bool") {
          return v.trim().toLowerCase() === "true";
        }
        return v;
      });
      await callTool("contract_explorer_call_function", {
        view_id: viewId,
        function_name: fn.name,
        function_signature: fn.signature,
        args: coerced,
        block_identifier: defaultBlock || "latest",
      });
    } catch (err) {
      setCallError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  function coercedArgs() {
    return args.map((v, i) => {
      const t = fn.inputs[i].type;
      if (t.startsWith("uint") || t.startsWith("int")) return v.trim();
      if (t === "bool") return v.trim().toLowerCase() === "true";
      return v;
    });
  }

  async function sweepHistory() {
    setHistoryError(null);
    try {
      await callTool("contract_explorer_read_history", {
        view_id: viewId,
        function_name: fn.name,
        function_signature: fn.signature,
        args: coercedArgs(),
        since: range,
        points: Number(points) || 60,
        decimals: Number(decimals) || 0,
      });
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : String(err));
    }
  }

  const returnsClause =
    fn.outputs.length > 0
      ? ` returns ${fn.outputs.map((o) => o.type).join(", ")}`
      : "";

  return (
    <>
      {/* Mono ABI block */}
      <pre className="ma-abi-block">
        <span className="kw">function</span> {fn.name}(
        {fn.inputs.length === 0 ? (
          <>{`)${fn.stateMutability !== "nonpayable" ? ` ${fn.stateMutability}` : ""}${returnsClause}`}</>
        ) : (
          <>
            {fn.inputs.map((inp, i) => (
              <span key={i}>
                {"\n  "}
                <span>{inp.name || `arg${i}`}</span>:{" "}
                <span className="ty">{inp.type}</span>
                {i < fn.inputs.length - 1 ? "," : ""}
              </span>
            ))}
            {`\n)${fn.stateMutability !== "nonpayable" ? ` ${fn.stateMutability}` : ""}${returnsClause}`}
          </>
        )}
      </pre>

      {/* Inputs + Call button */}
      <div className="ma-call-row">
        {fn.inputs.map((input, i) => (
          <input
            key={`${input.name}-${i}`}
            type="text"
            value={args[i] ?? ""}
            onChange={(e) => setArg(i, e.target.value)}
            placeholder={`${input.name || `arg${i}`}: ${input.type}`}
            spellCheck={false}
            autoCapitalize="off"
            disabled={isWrite}
          />
        ))}
        <button
          className="ma-call-btn"
          onClick={call}
          type="button"
          disabled={pending || isWrite}
          title={isWrite ? "Read-only mode — write calls are disabled" : undefined}
        >
          {pending ? "Calling…" : isWrite ? "Disabled" : "Call"}
        </button>
        {!isWrite && isPlottable(fn) ? (
          <button
            className="ma-call-btn ce-history-toggle"
            type="button"
            onClick={() => setShowHistory((v) => !v)}
            title="Plot this value across a block range"
          >
            {showHistory ? "Hide history" : "History"}
          </button>
        ) : null}
      </div>

      {/* Result line */}
      {callError ? (
        <div className="ma-result-line">
          <span className="ma-result-arrow">↳</span>
          <span className="ma-result-label">error:</span>
          <span className="ma-result-value ma-result-value--err">{callError}</span>
        </div>
      ) : null}
      {lastResult ? (
        <div className="ma-result-line">
          <span className="ma-result-arrow">↳</span>
          <span className="ma-result-label">
            {lastResult.ok ? "result:" : "error:"}
          </span>
          <span
            className={`ma-result-value ${!lastResult.ok ? "ma-result-value--err" : ""}`}
          >
            {lastResult.ok
              ? renderResult(lastResult.result)
              : lastResult.error}
          </span>
          {lastResult.ok && fn.outputs.length > 0 ? (
            <span className="ma-result-type">
              ({fn.outputs.map((o) => o.type).join(", ")})
            </span>
          ) : null}
          <span className="ma-result-type">
            @ {String(lastResult.block ?? "latest")} ·{" "}
            {lastResult.elapsed_seconds}s
          </span>
        </div>
      ) : null}

      {/* History sweep — reads this function across a block range */}
      {showHistory ? (
        <div className="ce-history-panel">
          <div className="ma-call-row">
            <div className="ce-range-presets">
              {RANGE_PRESETS.map(([label, value]) => (
                <button
                  key={value}
                  type="button"
                  className={`ce-range-btn ${range === value ? "is-active" : ""}`}
                  onClick={() => setRange(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <input
              type="text"
              value={points}
              onChange={(e) => setPoints(e.target.value)}
              placeholder="points"
              title="Samples across the range (max 200)"
              style={{ flex: "0 1 90px" }}
            />
            <input
              type="text"
              value={decimals}
              onChange={(e) => setDecimals(e.target.value)}
              placeholder="decimals"
              title="Scale the plotted value by 10^n (e.g. 18 for a token amount)"
              style={{ flex: "0 1 100px" }}
            />
            <AsyncButton
              onClick={sweepHistory}
              loadingLabel="Sweeping"
              variant="primary"
            >
              Sweep
            </AsyncButton>
          </div>

          {historyError ? (
            <div className="ma-result-line">
              <span className="ma-result-arrow">↳</span>
              <span className="ma-result-label">error:</span>
              <span className="ma-result-value ma-result-value--err">
                {historyError}
              </span>
            </div>
          ) : null}

          {series ? (
            <HistoryChart series={series} />
          ) : (
            <div className="ma-empty">
              Pick a range and sweep — each sample is a live archive read, so a
              60-point sweep takes a few seconds.
            </div>
          )}
        </div>
      ) : null}
    </>
  );
}
