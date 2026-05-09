import { useMemo, useState } from "react";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { SummaryCards } from "../shared/SummaryCards";
import { CollapsibleSection } from "../shared/CollapsibleSection";

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

interface ContractExplorerState {
  address: string;
  contract_name: string;
  abi_source: string;
  implementation_address: string;
  target: "auto" | "implementation" | "proxy";
  read_functions: AbiFunction[];
  write_functions: AbiFunction[];
  events: AbiEvent[];
  call_history: CallEntry[];
  warnings: string[];
}

const APP_ID = "contract_explorer";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function shortAddr(addr: string): string {
  if (!addr || addr.length < 12) return addr || "—";
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

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

const MOCK_STATE: ContractExplorerState = {
  address: "0x420CA0f9B9b604cE0fd9C18EF134C705e5Fa3430",
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
  const [defaultBlock, setDefaultBlock] = useState<string>("latest");

  const state = view?.view_state;

  const lastResults = useMemo(
    () => lastResultPerFunction(state?.call_history ?? []),
    [state?.call_history],
  );

  if (!view) {
    return (
      <div className="contract-explorer-loading">
        <p>Loading Contract Explorer…</p>
      </div>
    );
  }

  // Empty state — no address yet.
  const isEmpty = !state || !state.address;

  async function loadAddress(addr: string, target: typeof pendingTarget) {
    if (!addr.trim()) return;
    if (view?.view_id) {
      await callTool("load_contract_explorer_address", {
        view_id: view.view_id,
        address: addr.trim(),
        target,
      });
    } else {
      await callTool("open_contract_explorer", {
        address: addr.trim(),
        target,
      });
    }
  }

  return (
    <div className="contract-explorer-app">
      <header className="contract-explorer-header">
        <div>
          <h1>{view.title || "Contract Explorer"}</h1>
          {state?.contract_name ? (
            <div className="contract-explorer-subtitle">
              <strong>{state.contract_name}</strong>{" "}
              <code>{state.address}</code>
              {state.implementation_address ? (
                <span className="contract-explorer-badge">
                  proxy → {shortAddr(state.implementation_address)}
                </span>
              ) : null}
              <span className="contract-explorer-source">
                ABI: {state.abi_source || "—"}
              </span>
              <span className="contract-explorer-source">
                target: <code>{state.target}</code>
              </span>
            </div>
          ) : null}
        </div>

        <form
          className="contract-explorer-address-bar"
          onSubmit={(e) => {
            e.preventDefault();
            void loadAddress(pendingAddress, pendingTarget);
          }}
        >
          <label className="contract-explorer-address-field">
            <span>Contract address</span>
            <input
              type="text"
              placeholder="0x… paste a Gnosis Chain contract address"
              value={pendingAddress}
              onChange={(e) => setPendingAddress(e.target.value)}
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
            />
          </label>
          <label
            className="contract-explorer-target-label"
            title="Which ABI to load when the address is a proxy. Most users want 'auto'."
          >
            <span>target</span>
            <select
              value={pendingTarget}
              onChange={(e) =>
                setPendingTarget(
                  e.target.value as "auto" | "implementation" | "proxy",
                )
              }
            >
              <option value="auto">auto (impl ABI on proxies)</option>
              <option value="implementation">implementation only</option>
              <option value="proxy">proxy own ABI (advanced)</option>
            </select>
          </label>
          <button type="submit">Load</button>
        </form>

        <div className="contract-explorer-block-bar">
          <label className="contract-explorer-block-label">
            <span>Default block</span>
            <input
              type="text"
              value={defaultBlock}
              onChange={(e) => setDefaultBlock(e.target.value)}
              placeholder="latest"
              spellCheck={false}
              autoCapitalize="off"
              title="latest, finalized, safe, or a numeric block (e.g. 30000000). Historical blocks require GNOSIS_ARCHIVE_RPC_URL."
            />
          </label>
          <div className="contract-explorer-block-presets">
            {(["latest", "finalized", "safe"] as const).map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => setDefaultBlock(preset)}
                className={
                  defaultBlock === preset
                    ? "contract-explorer-preset active"
                    : "contract-explorer-preset"
                }
              >
                {preset}
              </button>
            ))}
          </div>
          <p className="contract-explorer-block-hint">
            Calls fall back to this block if the function card doesn't override
            it. Numeric blocks query historical state via the archive node.
          </p>
        </div>
      </header>

      <WarningBanner warnings={view.warnings ?? []} />
      {state?.warnings?.length ? (
        <WarningBanner warnings={state.warnings} />
      ) : null}

      {view.summary_cards?.length ? (
        <SummaryCards cards={view.summary_cards} />
      ) : null}

      {isEmpty ? (
        <div className="contract-explorer-empty">
          <p>
            Paste a contract address above to inspect its ABI and call any
            view/pure function. Proxies are followed automatically.
          </p>
        </div>
      ) : (
        <>
          {state!.read_functions.length === 0 ? (
            <div className="contract-explorer-empty-result">
              <strong>No view/pure functions found</strong>
              <p>
                Resolved target: <code>{state!.target}</code>
                {state!.contract_name ? (
                  <>
                    {" "}
                    — contract reported as <code>{state!.contract_name}</code>
                  </>
                ) : null}
                .
              </p>
              {state!.target === "proxy" ? (
                <p>
                  This is the proxy's own ABI — most proxies (e.g.
                  ERC1967Proxy) have no public functions of their own. Switch
                  the target dropdown to <strong>auto</strong> and click Load
                  again to see the implementation contract's functions.
                </p>
              ) : (
                <p>
                  The resolved ABI has no view/pure functions. Check the
                  address, or try a different target if this is a proxy.
                </p>
              )}
              <button
                type="button"
                onClick={() => {
                  setPendingTarget("auto");
                  void loadAddress(state!.address, "auto");
                }}
              >
                Retry with target=auto
              </button>
            </div>
          ) : null}

          <CollapsibleSection
            title={`Read functions (${state!.read_functions.length})`}
            defaultOpen
          >
            <div className="contract-explorer-fn-list">
              {state!.read_functions.map((fn) => (
                <FunctionCard
                  key={fn.signature}
                  fn={fn}
                  viewId={view.view_id}
                  callTool={callTool}
                  lastResult={lastResults.get(fn.signature)}
                  defaultBlock={defaultBlock}
                />
              ))}
            </div>
          </CollapsibleSection>

          {state!.write_functions.length > 0 ? (
            <CollapsibleSection
              title={`Write functions (${state!.write_functions.length}) — read-only mode, calls disabled`}
            >
              <div className="contract-explorer-fn-list">
                {state!.write_functions.map((fn) => (
                  <div key={fn.signature} className="contract-explorer-fn-card disabled">
                    <code>{fn.signature}</code>
                    <span className="contract-explorer-mutability">
                      {fn.stateMutability}
                    </span>
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          ) : null}

          {state!.events.length > 0 ? (
            <CollapsibleSection title={`Events (${state!.events.length})`}>
              <div className="contract-explorer-fn-list">
                {state!.events.map((ev) => (
                  <div key={ev.signature} className="contract-explorer-fn-card">
                    <code>{ev.signature}</code>
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          ) : null}

          {state!.call_history.length > 0 ? (
            <CollapsibleSection title="Call history">
              <ul className="contract-explorer-history">
                {state!.call_history.map((entry, i) => (
                  <li key={`${i}-${entry.called_at}`}>
                    <code>{entry.signature}</code>
                    <span className="contract-explorer-history-args">
                      ({entry.args.map((a) => String(a)).join(", ")})
                    </span>
                    <span className="contract-explorer-history-block">
                      @ {String(entry.block ?? "latest")}
                    </span>
                    <span
                      className={
                        entry.ok
                          ? "contract-explorer-ok"
                          : "contract-explorer-err"
                      }
                    >
                      {entry.ok ? renderResult(entry.result) : `error: ${entry.error}`}
                    </span>
                    <span className="contract-explorer-history-meta">
                      {entry.elapsed_seconds}s
                    </span>
                  </li>
                ))}
              </ul>
            </CollapsibleSection>
          ) : null}
        </>
      )}
    </div>
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
}

function FunctionCard({
  fn,
  viewId,
  callTool,
  lastResult,
  defaultBlock,
}: FunctionCardProps) {
  const [args, setArgs] = useState<string[]>(() =>
    fn.inputs.map(() => ""),
  );
  const [block, setBlock] = useState<string>("");
  const [pending, setPending] = useState(false);
  const [callError, setCallError] = useState<string | null>(null);

  // Per-card override falls back to the global default if blank.
  const effectiveBlock = block.trim() || defaultBlock;

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
      // Coerce numeric inputs from strings; leave addresses as strings (the
      // server auto-checksums them via _checksum_args).
      const coerced = args.map((v, i) => {
        const t = fn.inputs[i].type;
        if (t.startsWith("uint") || t.startsWith("int")) {
          // Pass as string — web3.py accepts numeric strings for big ints.
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
        block_identifier: effectiveBlock || "latest",
      });
    } catch (err) {
      setCallError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="contract-explorer-fn-card">
      <header>
        <code>{fn.signature}</code>
        <span className="contract-explorer-mutability">{fn.stateMutability}</span>
      </header>
      {fn.outputs.length > 0 ? (
        <div className="contract-explorer-outputs">
          → {fn.outputs.map((o) => o.type).join(", ")}
        </div>
      ) : null}
      <div className="contract-explorer-form">
        {fn.inputs.map((input, i) => (
          <label key={`${input.name}-${i}`}>
            <span>
              {input.name} <em>({input.type})</em>
            </span>
            <input
              type="text"
              value={args[i] ?? ""}
              onChange={(e) => setArg(i, e.target.value)}
              placeholder={input.type}
              spellCheck={false}
              autoCapitalize="off"
            />
          </label>
        ))}
        <label>
          <span>
            block <em>(override; blank = default)</em>
          </span>
          <input
            type="text"
            value={block}
            onChange={(e) => setBlock(e.target.value)}
            placeholder={defaultBlock}
            spellCheck={false}
            autoCapitalize="off"
            title="latest, finalized, safe, or a numeric block. Falls back to the default block above."
          />
        </label>
        <div className="contract-explorer-call-row">
          <button onClick={call} disabled={pending} type="button">
            {pending ? "Calling…" : `Call @ ${effectiveBlock || "latest"}`}
          </button>
        </div>
      </div>
      {callError ? (
        <div className="contract-explorer-err">Error: {callError}</div>
      ) : null}
      {lastResult ? (
        <div
          className={
            lastResult.ok
              ? "contract-explorer-result"
              : "contract-explorer-result error"
          }
        >
          <strong>{lastResult.ok ? "Result" : "Error"}:</strong>{" "}
          <code>
            {lastResult.ok
              ? renderResult(lastResult.result)
              : lastResult.error}
          </code>
          <span className="contract-explorer-result-block">
            @ block {String(lastResult.block ?? "latest")}
          </span>
          <span className="contract-explorer-history-meta">
            {lastResult.elapsed_seconds}s
          </span>
        </div>
      ) : null}
    </div>
  );
}
