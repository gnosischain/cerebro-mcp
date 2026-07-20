import { useMemo, useState } from "react";
import type { TxSvgLeg, TxSvgNode } from "./TxSvgCanvas";
import {
  TX_NODE_ROLE_LABEL,
  colorForTxNode,
  colorForTxToken,
  txNodeVisualRole,
  type TxNodeVisualRole,
} from "./txVisualEncoding";

export interface TxLegendToken {
  tokenAddress: string | null;
  symbol: string | null;
  color: string;
  legCount: number;
}

interface Props {
  legs: readonly TxSvgLeg[];
  nodes: readonly TxSvgNode[];
  decodedLogCount?: number | null;
}

const MAX_COMPACT_TOKENS = 6;

export function buildTxLegendTokens(legs: readonly TxSvgLeg[]): TxLegendToken[] {
  const grouped = new Map<string, TxLegendToken>();
  for (const leg of legs) {
    const address = leg.tokenAddress?.toLowerCase() || null;
    const key = address || `unknown:${(leg.symbol || "").toLowerCase()}`;
    const current = grouped.get(key);
    if (current) {
      current.legCount += 1;
      if (!current.symbol && leg.symbol) current.symbol = leg.symbol;
      continue;
    }
    grouped.set(key, {
      tokenAddress: address,
      symbol: leg.symbol || null,
      color: colorForTxToken(address),
      legCount: 1,
    });
  }
  return [...grouped.values()].sort((left, right) => {
    const a = left.symbol || left.tokenAddress || "Unknown token";
    const b = right.symbol || right.tokenAddress || "Unknown token";
    return a.localeCompare(b);
  });
}

export function TxGraphLegend({ legs, nodes, decodedLogCount }: Props) {
  const [expanded, setExpanded] = useState(false);
  const tokens = useMemo(() => buildTxLegendTokens(legs), [legs]);
  const roles = useMemo(() => {
    const present = new Map<TxNodeVisualRole, TxSvgNode>();
    nodes.forEach((node) => present.set(txNodeVisualRole(node), node));
    return [...present.entries()];
  }, [nodes]);
  const shownTokens = expanded ? tokens : tokens.slice(0, MAX_COMPACT_TOKENS);
  const hiddenCount = Math.max(0, tokens.length - shownTokens.length);

  return (
    <div className="ge-tx-legend" aria-label="Transaction graph legend">
      <div className="ge-tx-legend__summary">
        <strong>{legs.length.toLocaleString()} paths</strong>
        <span>/ {(decodedLogCount ?? legs.length).toLocaleString()} decoded receipt logs</span>
        <span className="ge-tx-legend__direction" aria-label="Arrows show transfer direction">
          ──▶ transfer direction
        </span>
      </div>
      <div className="ge-tx-legend__items" aria-label="Token line colors">
        {shownTokens.map((token) => (
          <span
            key={token.tokenAddress || `unknown:${token.symbol || "token"}`}
            className="ge-tx-legend__item"
            title={`${token.tokenAddress || "Unknown token address"} · ${token.legCount} leg${token.legCount === 1 ? "" : "s"}`}
          >
            <i className="ge-tx-legend__line" style={{ background: token.color }} />
            {token.symbol || token.tokenAddress || "Unknown token"}
            <small>×{token.legCount}</small>
          </span>
        ))}
        {hiddenCount ? (
          <button type="button" onClick={() => setExpanded(true)}>
            All {tokens.length} tokens
          </button>
        ) : expanded && tokens.length > MAX_COMPACT_TOKENS ? (
          <button type="button" onClick={() => setExpanded(false)}>Show less</button>
        ) : null}
      </div>
      <div className="ge-tx-legend__items" aria-label="Node fill colors">
        {roles.map(([role, node]) => (
          <span key={role} className="ge-tx-legend__item">
            <i className="ge-tx-legend__node" style={{ background: colorForTxNode(node) }} />
            {TX_NODE_ROLE_LABEL[role]}
          </span>
        ))}
      </div>
    </div>
  );
}

