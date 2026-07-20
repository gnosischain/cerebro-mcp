import { useMemo, type KeyboardEvent } from "react";

import { shortAddr } from "../../../utils/format";
import type { FlowEdgeRow, FlowNodeRow } from "../model/flowLayout";
import {
  buildMoneySankeyLayout,
  type MoneyEventKind,
  type MoneyRibbon,
} from "../model/moneySankeyLayout";

interface Props {
  nodes: FlowNodeRow[];
  edges: FlowEdgeRow[];
  seeds: string[];
  selectedNodeId: string;
  selectedEdgeId: string;
  hoveredEdgeId?: string;
  singleTokenMode?: boolean;
  maxCounterpartiesPerHop?: number;
  onSelectNode: (nodeId: string) => void;
  onSelectEdge: (edgeId: string) => void;
  onHoverEdge?: (edgeId: string) => void;
  onClearSelection: () => void;
}

function fmtUsd(value: number | null): string {
  if (value == null) return "unknown USD";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M known`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}k known`;
  return `$${value.toFixed(2)} known`;
}

function eventLabel(kind: MoneyEventKind): string {
  if (kind === "bridge_attributed") return "bridge-attributed transfer";
  if (kind === "contract_endpoint") return "contract endpoint transfer";
  return kind;
}

function ribbonLabel(ribbon: MoneyRibbon): string {
  const tokens = ribbon.symbols.length
    ? ribbon.symbols.join(", ")
    : ribbon.tokenAddresses.map((token) => shortAddr(token, 6, 4)).join(", ");
  const width =
    ribbon.widthBasis === "token_amount"
      ? `${ribbon.normalizedAmount ?? "unknown"} token units`
      : ribbon.widthBasis === "known_usd"
        ? fmtUsd(ribbon.knownUsd)
        : "categorical width; value unknown";
  return `${eventLabel(ribbon.eventKind)}: ${shortAddr(ribbon.sourceAddress, 8, 6)} to ${shortAddr(ribbon.targetAddress, 8, 6)}; ${tokens || "unknown token"}; ${width}; ${ribbon.transferCount} transfer${ribbon.transferCount === 1 ? "" : "s"}`;
}

function ribbonPath(ribbon: MoneyRibbon): string {
  const distance = Math.max(24, Math.abs(ribbon.targetX - ribbon.sourceX) * 0.45);
  return [
    `M ${ribbon.sourceX} ${ribbon.sourceY}`,
    `C ${ribbon.sourceX + distance} ${ribbon.sourceY}`,
    `${ribbon.targetX - distance} ${ribbon.targetY}`,
    `${ribbon.targetX} ${ribbon.targetY}`,
  ].join(" ");
}

function activateOnKeyboard(event: KeyboardEvent<SVGElement>, action: () => void): void {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  action();
}

export function MoneySankey({
  nodes,
  edges,
  seeds,
  selectedNodeId,
  selectedEdgeId,
  hoveredEdgeId = "",
  singleTokenMode = false,
  maxCounterpartiesPerHop = 40,
  onSelectNode,
  onSelectEdge,
  onHoverEdge,
  onClearSelection,
}: Props) {
  const layout = useMemo(
    () =>
      buildMoneySankeyLayout(nodes, edges, seeds, {
        singleTokenMode,
        maxCounterpartiesPerHop,
      }),
    [edges, maxCounterpartiesPerHop, nodes, seeds, singleTokenMode],
  );
  const activeEdgeId = selectedEdgeId || hoveredEdgeId;
  const hasMeasured = layout.ribbons.some((ribbon) => ribbon.widthBasis === "known_usd");
  const hasTokenAmount = layout.ribbons.some(
    (ribbon) => ribbon.widthBasis === "token_amount",
  );
  const hasCategorical = layout.ribbons.some(
    (ribbon) => ribbon.widthBasis === "categorical",
  );
  const selectedRibbon = layout.ribbons.find((ribbon) =>
    ribbon.edgeIds.includes(selectedEdgeId),
  );

  if (!layout.ribbons.length) {
    return (
      <section className="ge-money-sankey ge-money-sankey--empty" aria-label="Money Trail flow map">
        <header className="ge-money-sankey__header">
          <div>
            <strong>Sankey-style hop map</strong>
            <span>Aggregated transfer adjacency — not transaction-matched custody.</span>
          </div>
        </header>
        <p>No admitted movements are available for the applied scope.</p>
      </section>
    );
  }

  return (
    <section className="ge-money-sankey" aria-label="Money Trail Sankey-style hop map">
      <header className="ge-money-sankey__header">
        <div>
          <strong>Sankey-style hop map</strong>
          <span>Aggregated transfer adjacency — not transaction-matched custody.</span>
          <small>
            Dotted connectors mean “analyst expanded this address,” not “the same funds continued.”
          </small>
        </div>
        <div className="ge-money-sankey__legend" aria-label="Ribbon width legend">
          {hasMeasured ? <span>Solid width · known USD</span> : null}
          {hasTokenAmount ? <span>Solid width · normalized token amount</span> : null}
          {hasCategorical ? <span>Dashed · unpriced/categorical</span> : null}
        </div>
      </header>
      <div className="ge-money-sankey__coverage" aria-label="Visible graph cap by hop">
        {layout.hopCoverage.map((coverage) => (
          <span key={`${coverage.direction}:${coverage.hop}`}>
            {coverage.direction === "in" ? "Incoming" : "Outgoing"} hop {coverage.hop} ·{" "}
            {coverage.shownCounterparties}/{coverage.loadedCounterparties} loaded counterparties
            {coverage.omittedCounterparties
              ? ` · ${coverage.omittedCounterparties} omitted from map`
              : ""}
          </span>
        ))}
      </div>
      {selectedRibbon ? (
        <div className="ge-money-sankey__selection" role="status">
          <strong>
            {shortAddr(selectedRibbon.sourceAddress, 8, 6)} →{" "}
            {shortAddr(selectedRibbon.targetAddress, 8, 6)}
          </strong>
          <span>
            {eventLabel(selectedRibbon.eventKind)} ·{" "}
            {selectedRibbon.symbols.join(", ") ||
              selectedRibbon.tokenAddresses
                .map((token) => shortAddr(token, 6, 4))
                .join(", ")} · {fmtUsd(selectedRibbon.knownUsd)}
            {selectedRibbon.unpricedRows
              ? ` · ${selectedRibbon.unpricedRows} unpriced source row${selectedRibbon.unpricedRows === 1 ? "" : "s"}`
              : ""}
          </span>
        </div>
      ) : null}
      <svg
        className="ge-money-sankey__svg"
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="group"
        aria-labelledby="ge-money-sankey-title ge-money-sankey-desc"
        onClick={(event) => {
          if (event.target === event.currentTarget) onClearSelection();
        }}
      >
        <title id="ge-money-sankey-title">Observed aggregate transfer adjacency by investigative hop</title>
        <desc id="ge-money-sankey-desc">
          Intermediaries have separate received and sent instances. Dotted connectors record an
          analyst expansion and carry no value.
        </desc>
        <g className="ge-money-sankey__ribbons">
          {layout.ribbons.map((ribbon) => {
            const selected = ribbon.edgeIds.includes(selectedEdgeId);
            const hovered = ribbon.edgeIds.includes(hoveredEdgeId);
            const dimmed = Boolean(activeEdgeId) && !ribbon.edgeIds.includes(activeEdgeId);
            const edgeId = ribbon.edgeIds[0];
            const path = ribbonPath(ribbon);
            return (
              <g
                key={ribbon.id}
                className={[
                  "ge-money-ribbon",
                  `is-${ribbon.eventKind}`,
                  ribbon.widthBasis === "categorical" ? "is-categorical" : "",
                  selected ? "is-selected" : "",
                  hovered ? "is-hovered" : "",
                  dimmed ? "is-dimmed" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <path
                  className="ge-money-ribbon__hit"
                  d={path}
                  strokeWidth={Math.max(14, ribbon.strokeWidth + 10)}
                  role="button"
                  tabIndex={0}
                  aria-label={ribbonLabel(ribbon)}
                  aria-pressed={selected}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectEdge(edgeId);
                  }}
                  onKeyDown={(event) =>
                    activateOnKeyboard(event, () => onSelectEdge(edgeId))
                  }
                  onMouseEnter={() => onHoverEdge?.(edgeId)}
                  onMouseLeave={() => onHoverEdge?.("")}
                  onFocus={() => onHoverEdge?.(edgeId)}
                  onBlur={() => onHoverEdge?.("")}
                >
                  <title>{ribbonLabel(ribbon)}</title>
                </path>
                {selected ? (
                  <path
                    className="ge-money-ribbon__selection"
                    d={path}
                    strokeWidth={ribbon.strokeWidth + 7}
                    aria-hidden="true"
                  />
                ) : null}
                <path
                  className="ge-money-ribbon__visible"
                  d={path}
                  strokeWidth={ribbon.strokeWidth}
                  aria-hidden="true"
                />
              </g>
            );
          })}
        </g>
        <g className="ge-money-sankey__connectors">
          {layout.connectors.map((connector) => (
            <line
              key={connector.id}
              x1={connector.x1}
              y1={connector.y1}
              x2={connector.x2}
              y2={connector.y2}
              data-kind={connector.kind}
              role="button"
              tabIndex={0}
              aria-label={`Expanded ${connector.address}; no custody continuity asserted`}
              onClick={(event) => {
                event.stopPropagation();
                onSelectNode(connector.address);
              }}
              onKeyDown={(event) =>
                activateOnKeyboard(event, () => onSelectNode(connector.address))
              }
            >
              <title>Expanded this address — no custody continuity asserted</title>
            </line>
          ))}
        </g>
        <g className="ge-money-sankey__nodes">
          {layout.nodes.map((node) => {
            const selected = node.address === selectedNodeId;
            return (
              <g
                key={node.id}
                className={`ge-money-node is-${node.role}${selected ? " is-selected" : ""}`}
                role="button"
                tabIndex={0}
                aria-label={`${node.label}; ${node.role}; hop ${node.hop}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onSelectNode(node.address);
                }}
                onKeyDown={(event) =>
                  activateOnKeyboard(event, () => onSelectNode(node.address))
                }
              >
                <rect
                  x={node.x - node.width / 2}
                  y={node.y - node.height / 2}
                  width={node.width}
                  height={node.height}
                  rx={2}
                />
                <text
                  x={node.x + (node.role === "received" ? -8 : 8)}
                  y={node.y - 11}
                  textAnchor={node.role === "received" ? "end" : "start"}
                >
                  {node.label}
                </text>
                <text
                  className="ge-money-node__role"
                  x={node.x + (node.role === "received" ? -8 : 8)}
                  y={node.y + 14}
                  textAnchor={node.role === "received" ? "end" : "start"}
                >
                  {node.role === "terminal"
                    ? node.eventKinds.map(eventLabel).join(" / ")
                    : node.role}
                </text>
                <title>{`${node.address} · ${node.role} · hop ${node.hop}`}</title>
              </g>
            );
          })}
        </g>
      </svg>
    </section>
  );
}
