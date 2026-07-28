import { useMemo, useState, type KeyboardEvent } from "react";

import { shortAddr } from "../../../utils/format";
import type { FlowEdgeRow, FlowNodeRow } from "../model/flowLayout";
import {
  buildMoneySankeyLayout,
  type MoneyEventKind,
  type MoneyRibbon,
} from "../model/moneySankeyLayout";
import { SvgViewport } from "./SvgViewport";
import type { Camera } from "./svgCamera";

/** Pane width the layout is asked for. Measured by SvgViewport; the layout may
 * exceed it when there are many hops (then the viewport scrolls). */
const LAYOUT_PADDING = 20;


interface Props {
  nodes: FlowNodeRow[];
  edges: FlowEdgeRow[];
  seeds: string[];
  selectedNodeId: string;
  selectedEdgeId: string;
  hoveredEdgeId?: string;
  singleTokenMode?: boolean;
  maxCounterpartiesPerHop?: number;
  /** Session camera identity. Changes only when the QUESTION changes. */
  stateKey?: string;
  universeKey?: string;
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
  stateKey = "money:sankey",
  universeKey = "",
  onSelectNode,
  onSelectEdge,
  onHoverEdge,
  onClearSelection,
}: Props) {
  // Measured pane width, fed back into the layout. This is the whole point of
  // the change: the layout used to be pinned at 1040 user-units regardless of
  // the pane, so a wide short pane letterboxed a tall map into illegibility.
  const [paneWidth, setPaneWidth] = useState(0);
  const layout = useMemo(
    () =>
      buildMoneySankeyLayout(nodes, edges, seeds, {
        singleTokenMode,
        maxCounterpartiesPerHop,
        width: paneWidth > 0 ? paneWidth - LAYOUT_PADDING * 2 : undefined,
      }),
    [edges, maxCounterpartiesPerHop, nodes, seeds, singleTokenMode, paneWidth],
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
      {/* One line of framing plus a legend. The three paragraphs that used to
          sit here (including the connector caveat) moved into "How to read
          this" — the caveats are load-bearing but they are reference, not
          something to re-read on every glance. */}
      <header className="ge-money-sankey__header">
        <div>
          <strong>Hop map</strong>
          <span>Aggregated transfer adjacency — not transaction-matched custody.</span>
        </div>
        <div className="ge-money-sankey__legend" aria-label="Ribbon width legend">
          {hasMeasured ? <span>Width · known USD</span> : null}
          {hasTokenAmount ? <span>Width · normalized token amount</span> : null}
          {hasCategorical ? <span>Dashed · unpriced</span> : null}
          <details className="ge-money-sankey__howto">
            <summary>How to read this</summary>
            <div>
              <p>
                Columns are investigative hops out from (and back to) the seed —
                the labels above the map name each one. Ribbon thickness is the
                aggregated value on that adjacency, not a single transfer.
              </p>
              <p>
                Every intermediary appears twice, as a <em>received</em> and a{" "}
                <em>sent</em> instance, joined by a dotted connector. That
                connector means “an analyst expanded this address,” not “the same
                funds continued.” This view cannot establish custody continuity
                and does not claim to.
              </p>
              <p>
                A dashed ribbon is unpriced: its value is unknown, not zero.
                Counts above each column say how many counterparties are drawn
                versus how many were loaded.
              </p>
              <p>Scroll or pinch to zoom, drag to pan, or use Fit width / Fit all.</p>
            </div>
          </details>
        </div>
      </header>
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
      <SvgViewport
        className="ge-money-sankey__viewport"
        contentBox={{ x: 0, y: 0, width: layout.width, height: layout.height }}
        stateKey={stateKey}
        universeKey={universeKey}
        fitMode="width"
        padding={LAYOUT_PADDING}
        ariaLabel="Observed aggregate transfer adjacency by investigative hop. Intermediaries have separate received and sent instances; dotted connectors record an analyst expansion and carry no value."
        onBackgroundClick={onClearSelection}
        onMeasure={(size) => setPaneWidth(size.width)}
        chrome={(camera) => (
          <ColumnHeaders layout={layout} camera={camera} />
        )}
      >
      {() => (
      <>
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
                  // Non-scaling: the hit band must stay a comfortable ~14 SCREEN
                  // px at every zoom. Scaled, it would balloon into neighbouring
                  // ribbons when zoomed in (ROW_GAP is only 34) and shrink below
                  // clickability at Fit all.
                  vectorEffect="non-scaling-stroke"
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
              vectorEffect="non-scaling-stroke"
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
      </>
      )}
      </SvgViewport>
    </section>
  );
}

/**
 * Per-column headers, drawn in SCREEN space so they stay legible at any zoom.
 *
 * This replaces the stacked `.ge-money-sankey__coverage` strip, which listed
 * every hop's counts as prose above the map and left the reader to work out
 * which column each line referred to. Coverage belongs over its own column.
 */
function ColumnHeaders({
  layout,
  camera,
}: {
  layout: ReturnType<typeof buildMoneySankeyLayout>;
  camera: Camera;
}) {
  return (
    <g className="ge-money-sankey__columns" aria-hidden="true">
      {layout.columns.map((column) => {
        const x = column.x * camera.scale + camera.tx;
        const coverage = column.coverage;
        return (
          <g key={`${column.direction}:${column.stage}`} transform={`translate(${x} 0)`}>
            <text className="ge-money-col__label" y={16} textAnchor="middle">
              {column.label}
            </text>
            {coverage ? (
              <text
                className={`ge-money-col__coverage${
                  coverage.omittedCounterparties ? " is-truncated" : ""
                }`}
                y={30}
                textAnchor="middle"
              >
                {coverage.shownCounterparties}/{coverage.loadedCounterparties} shown
                {coverage.omittedCounterparties
                  ? ` · ${coverage.omittedCounterparties} not drawn`
                  : ""}
              </text>
            ) : null}
          </g>
        );
      })}
    </g>
  );
}
