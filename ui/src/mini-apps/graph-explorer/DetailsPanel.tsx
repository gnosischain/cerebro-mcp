import type { MiniAppPayload } from "../shared/miniAppTypes";
import type {
  AddressRoles,
  GraphEdgeRow,
  GraphExplorerState,
  GraphNodeRow,
  ProfileCard,
} from "./types";

interface Props {
  view: MiniAppPayload<GraphExplorerState>;
  onExpand: (nodeId: string) => void;
  onRecenter: (nodeId: string) => void;
  onApplyHop: (profileId: string) => void;
  onSelectNode: (nodeId: string) => void;
}

function shortId(id: string): string {
  if (id.startsWith("0x") && id.length > 16) {
    return `${id.slice(0, 8)}…${id.slice(-6)}`;
  }
  return id.length > 22 ? `${id.slice(0, 20)}…` : id;
}

function parseNodeRows(rows: unknown[][] | undefined): GraphNodeRow[] {
  return (rows ?? []).map((r) => ({
    id: String(r[0] ?? ""),
    kind: String(r[1] ?? "address"),
    label: String(r[2] ?? ""),
    profiles: Array.isArray(r[3]) ? (r[3] as string[]) : [],
  }));
}

function parseEdgeRows(rows: unknown[][] | undefined): GraphEdgeRow[] {
  return (rows ?? []).map((r) => ({
    id: String(r[0] ?? ""),
    source: String(r[1] ?? ""),
    target: String(r[2] ?? ""),
    profile: String(r[3] ?? ""),
    weight: Number(r[4] ?? 0),
    edge_count: Number(r[5] ?? 0),
    directed: Boolean(r[6]),
  }));
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

function parseEvidenceRows(
  rows: unknown[][] | undefined,
): Array<{ column: string; value: string }> {
  return (rows ?? []).map((r) => ({
    column: String(r[1] ?? ""),
    value: String(r[2] ?? ""),
  }));
}

export function DetailsPanel({ view, onExpand, onRecenter, onApplyHop, onSelectNode }: Props) {
  const state = (view.view_state || {}) as GraphExplorerState;
  const nodes = parseNodeRows(view.datasets?.nodes?.preview_rows);
  const edges = parseEdgeRows(view.datasets?.edges?.preview_rows);
  const selectedNode =
    nodes.find((n) => n.id === state.selected_node_id) ||
    nodes.find((n) => n.id === state.seed_node?.id);
  const selectedEdge = edges.find((e) => e.id === state.selected_edge_id);

  // Neighbors of the selected node, derived from the loaded edge list. Each is
  // clickable (select) with an inline expand, so the panel doubles as a
  // navigation aid for the canvas.
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const neighbors: Array<{ id: string; kind: string; label: string; profile: string; dir: "in" | "out" }> = [];
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

  const roles =
    selectedNode && state.node_roles
      ? state.node_roles[selectedNode.id]
      : undefined;

  const nodeEvidence = parseEvidenceRows(
    view.datasets?.node_evidence?.preview_rows,
  );
  const edgeEvidence = parseEvidenceRows(
    view.datasets?.edge_evidence?.preview_rows,
  );

  const catalogByProfile: Record<string, ProfileCard> = Object.fromEntries(
    (state.catalog || []).map((p) => [p.profile, p]),
  );

  const suggestions = state.suggested_next_hops || [];

  return (
    <aside className="ge-details">
      <section>
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
            </dl>
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              <button
                type="button"
                className="ge-btn"
                onClick={() => onExpand(selectedNode.id)}
              >
                Expand
              </button>
              <button
                type="button"
                className="ge-btn"
                onClick={() => onRecenter(selectedNode.id)}
              >
                Recenter
              </button>
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
            {nodeEvidence.length > 0 ? (
              <div className="ge-evidence" style={{ marginTop: 10 }}>
                <h4>Evidence</h4>
                <dl className="ge-kv">
                  {nodeEvidence.map((e, i) => (
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
                <button
                  type="button"
                  className="ge-neighbor-expand"
                  onClick={() => onExpand(nb.id)}
                  title="Expand this neighbor"
                >
                  +
                </button>
              </div>
            ))}
            {neighbors.length > 50 ? (
              <div className="ge-neighbor-more">+{neighbors.length - 50} more…</div>
            ) : null}
          </div>
        </section>
      ) : null}

      {selectedEdge ? (
        <section>
          <h3>Edge</h3>
          <dl className="ge-kv">
            <dt>profile</dt>
            <dd>{selectedEdge.profile}</dd>
            <dt>source</dt>
            <dd>{selectedEdge.source}</dd>
            <dt>target</dt>
            <dd>{selectedEdge.target}</dd>
            <dt>weight</dt>
            <dd>{selectedEdge.weight.toFixed(4)}</dd>
            <dt>edge_count</dt>
            <dd>{selectedEdge.edge_count}</dd>
          </dl>
          {edgeEvidence.length > 0 ? (
            <div className="ge-evidence" style={{ marginTop: 8 }}>
              <h4>Evidence</h4>
              <dl className="ge-kv">
                {edgeEvidence.map((e, i) => (
                  <div key={`${e.column}-${i}`} style={{ display: "contents" }}>
                    <dt>{e.column}</dt>
                    <dd>{e.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
        </section>
      ) : null}

      {selectedNode && catalogByProfile[selectedNode.profiles[0] ?? ""] ? (
        <section>
          <h3>Semantic</h3>
          {(() => {
            const profile =
              catalogByProfile[selectedNode.profiles[0]] || Object.values(catalogByProfile)[0];
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
