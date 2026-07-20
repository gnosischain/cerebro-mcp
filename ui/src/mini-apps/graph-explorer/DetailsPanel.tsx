// Right-hand inspector: selected node (roles, evidence, neighbors), selected
// edge (weights, evidence), semantic provenance, and suggested next hops.
// All rows arrive PRE-PARSED through model/parseRows — this panel never
// parses dataset rows itself. (No "Ask" button by design — removed in WS10.)

import { filterEvidenceRows, shortId } from "./model/parseRows";
import type {
  AddressRoles,
  EvidenceExpectation,
  EvidenceRow,
  GraphEdgeRow,
  GraphNodeRow,
  HopSuggestion,
  ProfileCard,
} from "./types";

interface Props {
  nodes: GraphNodeRow[];
  edges: GraphEdgeRow[];
  selectedNodeId: string;
  selectedEdgeId: string;
  seedNodeId: string;
  nodeRoles: Record<string, AddressRoles>;
  catalog: ProfileCard[];
  suggestions: HopSuggestion[];
  nodeEvidence: EvidenceRow[];
  edgeEvidence: EvidenceRow[];
  /** Latest CLIENT focus intent. `null` explicitly invalidates all evidence;
   * when omitted during migration, owner + subject are still enforced but
   * request-order safety requires the caller to provide this prop. */
  evidenceExpectation?: EvidenceExpectation | null;
  /** OPTIONAL on purpose: a mode that cannot expand must not render an
   * Expand button. Three of four modes used to pass `() => undefined`, so the
   * button rendered, looked live, and silently did nothing when clicked. An
   * action with no handler is now simply not shown. */
  onExpand?: (nodeId: string) => void;
  onRecenter?: (nodeId: string) => void;
  onApplyHop: (profileId: string) => void;
  onSelectNode: (nodeId: string) => void;
  /** Flows mode: per-node Trace actions replace Expand/Recenter. Tracing
   * through a node OVERRIDES terminal-sector status (analyst's choice). */
  flowActions?: {
    traceIn: (nodeId: string) => void;
    traceOut: (nodeId: string) => void;
  };
  /** Extra key/value rows appended to the node / edge sections (Flows:
   * hop rank, in/out USD, flags; token, amounts). */
  extraNodeRows?: Array<[string, string]>;
  extraEdgeRows?: Array<[string, string]>;
  onClose?: () => void;
}

const ROLE_LABELS: Array<[keyof AddressRoles, string]> = [
  ["is_safe", "Safe"],
  ["is_gpay_wallet", "GPay Wallet"],
  ["is_ga_user", "GA User"],
  ["is_circles_avatar", "Circles Avatar"],
  ["is_circles_wrapper", "Circles Wrapper"],
  ["is_safe_owner", "Safe Owner"],
  ["is_lp_provider", "LP Provider"],
  ["is_pool", "Pool"],
  ["is_lending_user", "Lender"],
  ["is_validator_depositor", "Validator Depositor"],
  ["has_dune_label", "Dune Labeled"],
];

// String-valued role attributes — surfaced as key/value rows (not boolean
// badges) so the backing identity context is visible.
const ROLE_DETAILS: Array<[keyof AddressRoles, string]> = [
  ["controls_gpay_wallet", "Controls GPay wallet"],
  ["pool_protocol", "Pool protocol"],
  ["circles_avatar_type", "Circles avatar type"],
  ["dune_project", "Dune project"],
];

export function DetailsPanel({
  nodes,
  edges,
  selectedNodeId,
  selectedEdgeId,
  seedNodeId,
  nodeRoles,
  catalog,
  suggestions,
  nodeEvidence,
  edgeEvidence,
  evidenceExpectation,
  onExpand,
  onRecenter,
  onApplyHop,
  onSelectNode,
  flowActions,
  extraNodeRows,
  extraEdgeRows,
  onClose,
}: Props) {
  const selectedNode =
    nodes.find((n) => n.id === selectedNodeId) ||
    nodes.find((n) => n.id === seedNodeId);
  const selectedEdge = edges.find((e) => e.id === selectedEdgeId);

  const safeNodeEvidence =
    evidenceExpectation === undefined
      ? nodeEvidence.filter(
          (row) => row.subjectKind === "node" && row.ownerId === selectedNodeId,
        )
      : filterEvidenceRows(nodeEvidence, evidenceExpectation).filter(
          (row) => row.subjectKind === "node",
        );
  const safeEdgeEvidence =
    evidenceExpectation === undefined
      ? edgeEvidence.filter(
          (row) => row.subjectKind === "edge" && row.ownerId === selectedEdgeId,
        )
      : filterEvidenceRows(edgeEvidence, evidenceExpectation).filter(
          (row) => row.subjectKind === "edge",
        );

  // Neighbors of the selected node, derived from the loaded edge list. Each is
  // clickable (select) with an inline expand, so the panel doubles as a
  // navigation aid for the canvas.
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const neighbors: Array<{
    id: string;
    kind: string;
    label: string;
    profile: string;
    dir: "in" | "out";
  }> = [];
  if (selectedNode) {
    const seen = new Set<string>();
    for (const e of edges) {
      let otherId = "";
      let dir: "in" | "out" = "out";
      if (e.source === selectedNode.id) { otherId = e.target; dir = "out"; }
      else if (e.target === selectedNode.id) { otherId = e.source; dir = "in"; }
      else continue;
      if (!otherId || seen.has(otherId)) continue;
      seen.add(otherId);
      const node = nodeById.get(otherId);
      neighbors.push({
        id: otherId,
        kind: node?.kind ?? "address",
        label: node?.label ?? shortId(otherId),
        profile: e.profile,
        dir,
      });
    }
  }

  const roles = selectedNode ? nodeRoles[selectedNode.id] : undefined;
  const catalogByProfile: Record<string, ProfileCard> = Object.fromEntries(
    catalog.map((p) => [p.profile, p]),
  );

  // Selection context is ordered by the analyst's explicit intent. An edge
  // click must put edge identity/evidence first; the seed fallback node and
  // its neighbours remain useful secondary context below it.
  const edgeSection = selectedEdge ? (
    <section data-inspector-context="edge">
      <h3>Edge</h3>
      <dl className="ge-kv">
        <dt>profile</dt>
        <dd>{selectedEdge.profile}</dd>
        <dt>source</dt>
        <dd>{selectedEdge.source}</dd>
        <dt>target</dt>
        <dd>{selectedEdge.target}</dd>
        <dt>weight</dt>
        <dd>
          {Number.isFinite(selectedEdge.weight)
            ? selectedEdge.weight.toFixed(4)
            : "unknown"}
        </dd>
        <dt>edge_count</dt>
        <dd>{selectedEdge.edge_count}</dd>
        {(extraEdgeRows ?? []).map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {safeEdgeEvidence.length > 0 ? (
        <div className="ge-evidence" style={{ marginTop: 8 }}>
          <h4>Evidence</h4>
          <dl className="ge-kv">
            {safeEdgeEvidence.map((e, i) => (
              <div key={`${e.column}-${i}`} style={{ display: "contents" }}>
                <dt>{e.column}</dt>
                <dd>{e.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </section>
  ) : null;

  return (
    <aside className="ge-details">
      {onClose ? (
        <header className="ge-details__header">
          <strong>Details</strong>
          <button type="button" className="ge-icon-btn" onClick={onClose} aria-label="Close details">
            ×
          </button>
        </header>
      ) : null}
      {edgeSection}
      <section data-inspector-context="node">
        <h3>Node</h3>
        {selectedNode ? (
          <>
            <dl className="ge-kv">
              <dt>id</dt>
              <dd>{selectedNode.id}</dd>
              <dt>kind</dt>
              <dd>{selectedNode.kind}</dd>
              <dt>profiles</dt>
              <dd>{selectedNode.profiles.join(", ") || "—"}</dd>
              {(extraNodeRows ?? []).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              {flowActions ? (
                <>
                  <button
                    type="button"
                    className="ge-btn"
                    onClick={() => flowActions.traceIn(selectedNode.id)}
                    title="Trace where this address was funded FROM (1 hop upstream)"
                  >
                    ← Trace in
                  </button>
                  <button
                    type="button"
                    className="ge-btn"
                    onClick={() => flowActions.traceOut(selectedNode.id)}
                    title="Trace where funds went FROM this address (1 hop downstream)"
                  >
                    Trace out →
                  </button>
                </>
              ) : (
                <>
                  {onExpand && (
                    <button
                      type="button"
                      className="ge-btn"
                      onClick={() => onExpand(selectedNode.id)}
                    >
                      Expand
                    </button>
                  )}
                  {onRecenter && (
                    <button
                      type="button"
                      className="ge-btn"
                      onClick={() => onRecenter(selectedNode.id)}
                    >
                      Investigate from here
                    </button>
                  )}
                </>
              )}
              <button
                type="button"
                className="ge-btn"
                onClick={() => navigator.clipboard?.writeText(selectedNode.id)}
              >
                Copy
              </button>
            </div>
            {roles ? (
              <>
                <div className="ge-roles" style={{ marginTop: 10 }}>
                  {ROLE_LABELS.filter(([flag]) => roles[flag]).map(([flag, label]) => (
                    <span key={flag} className="ge-badge">
                      {label}
                    </span>
                  ))}
                </div>
                {ROLE_DETAILS.some(([flag]) => roles[flag]) ? (
                  <dl className="ge-kv" style={{ marginTop: 8 }}>
                    {ROLE_DETAILS.filter(([flag]) => roles[flag]).map(
                      ([flag, label]) => (
                        <div key={flag} style={{ display: "contents" }}>
                          <dt>{label}</dt>
                          <dd>{String(roles[flag])}</dd>
                        </div>
                      ),
                    )}
                  </dl>
                ) : null}
              </>
            ) : null}
            {safeNodeEvidence.length > 0 ? (
              <div className="ge-evidence" style={{ marginTop: 10 }}>
                <h4>Evidence</h4>
                <dl className="ge-kv">
                  {safeNodeEvidence.map((e, i) => (
                    <div key={`${e.column}-${i}`} style={{ display: "contents" }}>
                      <dt>{e.column}</dt>
                      <dd>{e.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ) : null}
          </>
        ) : (
          <div style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
            Click a node in the graph to inspect it.
          </div>
        )}
      </section>

      {selectedNode && neighbors.length > 0 ? (
        <section>
          <h3>Neighbors ({neighbors.length})</h3>
          <div className="ge-neighbors">
            {neighbors.slice(0, 50).map((nb) => (
              <div key={nb.id} className="ge-neighbor">
                <button
                  type="button"
                  className="ge-neighbor-main"
                  onClick={() => onSelectNode(nb.id)}
                  title={`${nb.id}\n${nb.dir === "out" ? "→" : "←"} via ${nb.profile}`}
                >
                  <span className={`ge-neighbor-dir ${nb.dir}`} aria-hidden>
                    {nb.dir === "out" ? "→" : "←"}
                  </span>
                  <span className="ge-neighbor-label">{nb.label || shortId(nb.id)}</span>
                  <span className="ge-neighbor-kind">{nb.kind}</span>
                </button>
                {onExpand && (
                  <button
                    type="button"
                    className="ge-neighbor-expand"
                    onClick={() => onExpand(nb.id)}
                    title="Expand this neighbor"
                  >
                    +
                  </button>
                )}
              </div>
            ))}
            {neighbors.length > 50 ? (
              <div className="ge-neighbor-more">+{neighbors.length - 50} more…</div>
            ) : null}
          </div>
        </section>
      ) : null}

      {selectedNode && catalogByProfile[selectedNode.profiles[0] ?? ""] ? (
        <section>
          <h3>Semantic</h3>
          {(() => {
            const profile =
              catalogByProfile[selectedNode.profiles[0]] ||
              Object.values(catalogByProfile)[0];
            if (!profile) return null;
            return (
              <dl className="ge-kv">
                <dt>model</dt>
                <dd>{profile.model_name}</dd>
                <dt>module</dt>
                <dd>{profile.module}</dd>
                <dt>status</dt>
                <dd>{profile.semantic_status}</dd>
                <dt>quality</dt>
                <dd>{profile.quality_tier || "—"}</dd>
                <dt>source</dt>
                <dd>{profile.semantic_source_file}</dd>
              </dl>
            );
          })()}
        </section>
      ) : null}

      {suggestions.length > 0 ? (
        <section>
          <h3>Suggested next hops</h3>
          <div className="ge-suggestions">
            {suggestions.map((hop) => (
              <button
                key={hop.profile}
                type="button"
                className="ge-suggestion"
                onClick={() => onApplyHop(hop.profile)}
              >
                <span>{hop.label}</span>
                <span className="ge-rationale">
                  via {hop.rationale || "semantic relationship"} · {hop.quality_tier}
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </aside>
  );
}
