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

export function DetailsPanel({ view, onExpand, onRecenter, onApplyHop }: Props) {
  const state = (view.view_state || {}) as GraphExplorerState;
  const nodes = parseNodeRows(view.datasets?.nodes?.preview_rows);
  const edges = parseEdgeRows(view.datasets?.edges?.preview_rows);
  const selectedNode =
    nodes.find((n) => n.id === state.selected_node_id) ||
    nodes.find((n) => n.id === state.seed_node?.id);
  const selectedEdge = edges.find((e) => e.id === state.selected_edge_id);

  const roles =
    selectedNode && state.node_roles
      ? state.node_roles[selectedNode.id]
      : undefined;

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
              <div className="ge-roles" style={{ marginTop: 10 }}>
                {ROLE_LABELS.filter(([flag]) => roles[flag]).map(([flag, label]) => (
                  <span key={flag} className="ge-badge">
                    {label}
                  </span>
                ))}
                {roles.dune_project ? (
                  <span className="ge-badge" title="Dune project">
                    {roles.dune_project}
                  </span>
                ) : null}
                {roles.circles_avatar_type ? (
                  <span className="ge-badge" title="Circles avatar type">
                    {roles.circles_avatar_type}
                  </span>
                ) : null}
              </div>
            ) : null}
          </>
        ) : (
          <div style={{ color: "#64748b", fontSize: "0.78rem" }}>
            Click a node in the graph to inspect it.
          </div>
        )}
      </section>

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
