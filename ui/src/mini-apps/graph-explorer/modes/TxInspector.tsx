import type { ReactNode } from "react";
import type { TxGroup, TxLegRow, TxNodeRow } from "../model/txLayout";
import type { TxContextRow } from "../model/txContext";

interface Props {
  txHash: string;
  transaction: TxGroup | undefined;
  receiptStatus: string;
  context?: TxContextRow;
  selectedEventKind?: "Mint" | "Burn" | "Transfer";
  selectedLeg: TxLegRow | undefined;
  selectedNode: TxNodeRow | undefined;
  graphVisible: boolean;
  onRevealGraph: () => void;
  onFollowNode: (nodeId: string) => void;
  onOpenNode: (nodeId: string) => void;
  onClose: () => void;
}

function fmtUsd(value: number | null): string {
  if (value == null) return "unknown";
  if (value === 0) return "$0.00";
  if (Math.abs(value) < 0.01) return "<$0.01";
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(1)}k`;
  return `$${value.toFixed(2)}`;
}

function copy(value: string): void {
  void navigator.clipboard?.writeText(value);
}

function Kv({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "contents" }}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/**
 * Transaction-specific inspector.
 *
 * A receipt leg is not a semantic-graph relationship: its token symbol is not
 * a relationship "profile", its USD value is not an unqualified "weight", and
 * `edge_count=1` conveys no useful evidence. Keep the three forensic contexts
 * explicit so a selected leg can never be mistaken for a relationship edge.
 */
export function TxInspector({
  txHash,
  transaction,
  receiptStatus,
  context,
  selectedEventKind,
  selectedLeg,
  selectedNode,
  graphVisible,
  onRevealGraph,
  onFollowNode,
  onOpenNode,
  onClose,
}: Props) {
  const legCount = transaction?.legs.length ?? 0;
  const unpriced = transaction?.unpricedLegCount ?? 0;
  const priced = Math.max(0, legCount - unpriced);
  const priceCoverage = legCount ? `${priced}/${legCount} legs priced` : "no ERC-20 legs";

  return (
    <aside className="ge-details ge-tx-inspector" aria-label="Transaction details">
      <header className="ge-tx-inspector__header">
        <strong>Transaction details</strong>
        <button type="button" className="ge-icon-btn" onClick={onClose} aria-label="Close transaction details">
          ×
        </button>
      </header>

      <section data-inspector-context="transaction">
        <h3>Transaction</h3>
        <dl className="ge-kv">
          <Kv label="hash"><code title={txHash}>{txHash || "unknown"}</code></Kv>
          <Kv label="receipt status">{receiptStatus || "unknown"}</Kv>
          <Kv label="block">{transaction?.blockNumber?.toLocaleString() ?? "unknown"}</Kv>
          <Kv label="timestamp">{transaction?.blockTimestamp || "unknown"}</Kv>
          <Kv label="initiator"><code>{context?.initiator ?? "unknown"}</code></Kv>
          <Kv label="called contract"><code>{context?.target ?? "contract creation or unknown"}</code></Kv>
          <Kv label="method">{context?.methodSelector ?? "unknown"}</Kv>
          <Kv label="nonce">{context?.nonce ?? "unknown"}</Kv>
          <Kv label="native value (raw)"><code>{context?.nativeValueRaw ?? "unknown"}</code></Kv>
          <Kv label="gas limit / used">{context?.gasLimit ?? "unknown"} / {context?.gasUsed ?? "unknown"}</Kv>
          <Kv label="effective gas price"><code>{context?.effectiveGasPrice ?? "unknown"}</code></Kv>
          <Kv label="fee (raw)"><code>{context?.feeRaw ?? "unknown"}</code></Kv>
          <Kv label="matched because">{context?.matchedBecause.join(", ") || "explicit hash"}</Kv>
          <Kv label="ERC-20 legs">{legCount}</Kv>
          <Kv label="known USD subtotal">
            {fmtUsd(transaction && priced > 0 ? transaction.knownUsdTotal : null)}
          </Kv>
          <Kv label="price coverage">{priceCoverage}</Kv>
          <Kv label="tokens">{transaction?.tokens.join(", ") || "unknown"}</Kv>
        </dl>
        <div className="ge-tx-inspector__actions">
          <button type="button" className="ge-btn" onClick={() => copy(txHash)}>Copy hash</button>
          {txHash ? (
            <a className="ge-btn" href={`https://gnosis.blockscout.com/tx/${txHash}`} target="_blank" rel="noreferrer">
              Explorer
            </a>
          ) : null}
        </div>
      </section>

      {selectedLeg ? (
        <section data-inspector-context="transfer-leg">
          <h3>Transfer leg</h3>
          <dl className="ge-kv">
            <Kv label="log order">{selectedLeg.seq + 1}</Kv>
            <Kv label="event">{selectedEventKind ?? "Transfer"}</Kv>
            <Kv label="block / tx / log">
              {selectedLeg.blockNumber.toLocaleString()} / {selectedLeg.transactionIndex} / {selectedLeg.logIndex}
            </Kv>
            <Kv label="sender"><code title={selectedLeg.source}>{selectedLeg.source}</code></Kv>
            <Kv label="recipient"><code title={selectedLeg.target}>{selectedLeg.target}</code></Kv>
            <Kv label="token">
              <span>{selectedLeg.symbol || "unknown"}</span>
              <code title={selectedLeg.tokenAddress}>{selectedLeg.tokenAddress || "unknown"}</code>
            </Kv>
            <Kv label="raw amount"><code>{selectedLeg.rawAmount || "unknown"}</code></Kv>
            <Kv label="normalized amount">
              {selectedLeg.amount == null
                ? "unknown (token decimals unavailable)"
                : selectedLeg.amount.toLocaleString()}
            </Kv>
            <Kv label="USD">{fmtUsd(selectedLeg.amountUsd)}</Kv>
            <Kv label="receipt status">{selectedLeg.txStatus || receiptStatus || "unknown"}</Kv>
          </dl>
          <div className="ge-tx-inspector__actions">
            <button type="button" className="ge-btn" onClick={() => copy(selectedLeg.id)}>Copy leg ID</button>
            <button type="button" className="ge-btn" onClick={onRevealGraph}>
              {graphVisible ? "Locate in graph" : "Show in graph"}
            </button>
          </div>
        </section>
      ) : selectedNode ? (
        <section data-inspector-context="participant">
          <h3>Participant</h3>
          <dl className="ge-kv">
            <Kv label="address"><code title={selectedNode.id}>{selectedNode.id}</code></Kv>
            <Kv label="role">{selectedNode.role || "address"}</Kv>
            <Kv label="project">{selectedNode.project || "No label in applied sources"}</Kv>
            <Kv label="legs in loaded scope">{selectedNode.legCount}</Kv>
            <Kv label="incoming USD">{fmtUsd(selectedNode.inUsd)}</Kv>
            <Kv label="outgoing USD">{fmtUsd(selectedNode.outUsd)}</Kv>
            <Kv label="flags">{selectedNode.flags.join(", ") || "none"}</Kv>
          </dl>
          <div className="ge-tx-inspector__actions">
            <button type="button" className="ge-btn" onClick={() => onFollowNode(selectedNode.id)}>
              Follow forward
            </button>
            <button type="button" className="ge-btn" onClick={() => onOpenNode(selectedNode.id)}>
              Open address transactions
            </button>
            <button type="button" className="ge-btn" onClick={() => copy(selectedNode.id)}>Copy</button>
            <a
              className="ge-btn"
              href={`https://gnosis.blockscout.com/address/${selectedNode.id}`}
              target="_blank"
              rel="noreferrer"
            >
              Blockscout
            </a>
            <button type="button" className="ge-btn" onClick={onRevealGraph}>
              {graphVisible ? "Locate in graph" : "Show in graph"}
            </button>
          </div>
        </section>
      ) : (
        <section data-inspector-context="selection-help">
          <h3>Selection</h3>
          <p className="ge-tx-inspector__hint">
            Select a transfer row, an arc, or a participant to inspect it. The ordered table remains the authoritative execution order.
          </p>
        </section>
      )}

      {!graphVisible ? (
        <div className="ge-tx-inspector__graph-state" role="status">
          <span>Transfer graph hidden.</span>
          <button type="button" className="ge-link-btn" onClick={onRevealGraph}>Show graph</button>
        </div>
      ) : null}
    </aside>
  );
}

export default TxInspector;
