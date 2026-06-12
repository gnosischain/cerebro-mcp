import { useMemo, useState } from "react";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { MiniAppChrome, MaIdentity, MaSection } from "../shared/MiniAppChrome";
import { MaHelpButton } from "../shared/HelpDialog";
import { CONTRACT_EXPLORER_HELP } from "../shared/helpContent";
import { shortAddr } from "../../utils/format";

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
  const [subTab, setSubTab] = useState<"read" | "write" | "events">("read");

  const state = view?.view_state;

  const lastResults = useMemo(
    () => lastResultPerFunction(state?.call_history ?? []),
    [state?.call_history],
  );

  if (!view) {
    return (
      <MiniAppChrome activeTabId="contract" rightSlot={<MaHelpButton content={CONTRACT_EXPLORER_HELP} />}>
        <div className="ma-empty">Loading Contract Explorer…</div>
      </MiniAppChrome>
    );
  }

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
            void loadAddress(pendingAddress, pendingTarget);
          }}
        >
          <input
            type="text"
            placeholder="0x… paste a Gnosis Chain contract address"
            value={pendingAddress}
            onChange={(e) => setPendingAddress(e.target.value)}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
          />
          <select
            value={pendingTarget}
            onChange={(e) =>
              setPendingTarget(
                e.target.value as "auto" | "implementation" | "proxy",
              )
            }
            style={{
              padding: "6px 10px",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              background: "var(--surface)",
              color: "var(--text-primary)",
              border: "1px solid var(--border)",
              borderRadius: 3,
            }}
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

        <WarningBanner warnings={view.warnings ?? []} />
        {state?.warnings?.length ? (
          <WarningBanner warnings={state.warnings} />
        ) : null}

        {/* Identity card — only when a contract is loaded */}
        {state?.address ? (
          <MaIdentity
            label={`Contract${state.contract_name ? " · " + state.contract_name : ""}`}
            value={state.address}
            onCopy={() => navigator.clipboard?.writeText(state.address)}
            rightSlot={
              state.implementation_address ? (
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    color: "var(--success)",
                    padding: "2px 8px",
                    border: "1px solid var(--success)",
                    borderRadius: 3,
                  }}
                  title={state.implementation_address}
                >
                  proxy → {shortAddr(state.implementation_address)}
                </span>
              ) : null
            }
          />
        ) : (
          <div className="ma-empty">
            Paste a contract address above to inspect its ABI and call any
            view/pure function. Proxies are followed automatically.
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
}

function FunctionCard({
  fn,
  viewId,
  callTool,
  lastResult,
  defaultBlock,
  isWrite,
}: FunctionCardProps) {
  const [args, setArgs] = useState<string[]>(() =>
    fn.inputs.map(() => ""),
  );
  const [pending, setPending] = useState(false);
  const [callError, setCallError] = useState<string | null>(null);

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
    </>
  );
}
