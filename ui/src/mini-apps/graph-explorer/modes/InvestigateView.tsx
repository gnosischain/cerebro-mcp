// INVESTIGATE mode: seed line (id + counters from graph_metrics), FilterBar,
// ProfileChips, the canvas over the investigate dataset pair, and the
// DetailsPanel. With no seed loaded it shows a centered seed-input card with
// an "or browse the Atlas" escape hatch.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { shortAddr } from "../../../utils/format";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { DetailsPanel } from "../DetailsPanel";
import { FilterBar } from "../FilterBar";
import { ProfileChips } from "../ProfileChips";
import { GraphCanvas } from "../canvas/GraphCanvas";
import {
  buildGraphModel,
  parseEdgeRows,
  parseEvidenceRows,
  parseNodeRows,
} from "../model/parseRows";
import type { GraphAction, GraphLocalState } from "../state/graphReducer";
import type { GraphExplorerViewState } from "../types";

const REFETCH_DEBOUNCE_MS = 600;

export interface RefetchOverrides {
  profiles?: string[];
  windowDays?: number;
  maxNeighbors?: number;
}

interface Props {
  server: GraphExplorerViewState;
  local: GraphLocalState;
  dispatch: (action: GraphAction) => void;
  nodes: HydratedDataset | undefined;
  edges: HydratedDataset | undefined;
  nodeEvidence: HydratedDataset | undefined;
  edgeEvidence: HydratedDataset | undefined;
  graphMetrics: HydratedDataset | undefined;
  /** Re-query the CURRENT seed with fresh filters. */
  refetchSeed: (overrides: RefetchOverrides) => void;
  /** Seed a NEW investigate subgraph from an address / node id. */
  seedInvestigate: (nodeId: string) => void;
  /** BFS-expand a node by the current stepper depth. */
  expandNode: (nodeId: string) => void;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
  onBrowseAtlas: () => void;
  /** Atlas | Investigate switch — rendered inline in the seed row so the
   * standalone mode bar can be dropped (one less header row). */
  modeSwitch?: ReactNode;
}

export function InvestigateView({
  server,
  local,
  dispatch,
  nodes,
  edges,
  nodeEvidence,
  edgeEvidence,
  graphMetrics,
  refetchSeed,
  seedInvestigate,
  expandNode,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  onBrowseAtlas,
  modeSwitch,
}: Props) {
  const seedId = server.investigate?.seed?.id ?? "";
  const seedKind = server.investigate?.seed?.kind ?? "";

  // Status filter trims which ACTIVE profiles reach the canvas (view-layer
  // only — flipping back to "all" restores the full picture).
  const catalogByProfile = useMemo(
    () => new Map((server.catalog ?? []).map((p) => [p.profile, p])),
    [server.catalog],
  );
  const effectiveProfiles = useMemo(
    () =>
      local.statusFilter === "all"
        ? local.investigateProfiles
        : local.investigateProfiles.filter((id) => {
            const p = catalogByProfile.get(id);
            return !p || p.semantic_status === local.statusFilter;
          }),
    [local.investigateProfiles, local.statusFilter, catalogByProfile],
  );
  const trimmedCount = local.investigateProfiles.length - effectiveProfiles.length;

  const model = useMemo(
    () => buildGraphModel(nodes?.rows, edges?.rows, effectiveProfiles),
    [nodes?.rows, edges?.rows, effectiveProfiles],
  );
  const parsedNodes = useMemo(() => parseNodeRows(nodes?.rows), [nodes?.rows]);
  const parsedEdges = useMemo(() => parseEdgeRows(edges?.rows), [edges?.rows]);

  // Counters: prefer the server-computed graph_metrics dataset; fall back to
  // the loaded rows.
  const metrics = useMemo(() => {
    const out: Record<string, number> = {};
    for (const row of graphMetrics?.rows ?? []) {
      if (Array.isArray(row) && row[0]) out[String(row[0])] = Number(row[1] ?? 0);
    }
    return out;
  }, [graphMetrics?.rows]);
  const nodeCount = metrics.node_count ?? parsedNodes.length;
  const edgeCount = metrics.edge_count ?? parsedEdges.length;

  // Details panel: open by default on wide viewports, closed on narrow
  // (overlay style) so the graph canvas owns the entire visible area.
  const [detailsOpen, setDetailsOpen] = useState<boolean>(() =>
    typeof window === "undefined" ? true : window.innerWidth > 900,
  );
  useEffect(() => {
    if (!local.selection.nodeId) return;
    if (typeof window !== "undefined" && window.innerWidth <= 900) {
      setDetailsOpen(true);
    }
  }, [local.selection.nodeId]);

  // Debounce window/max-neighbors refetches so typing doesn't spam the server.
  const refetchTimerRef = useRef<number | null>(null);
  const localRef = useRef(local);
  localRef.current = local;
  const scheduleRefetch = () => {
    if (refetchTimerRef.current) window.clearTimeout(refetchTimerRef.current);
    refetchTimerRef.current = window.setTimeout(() => {
      refetchTimerRef.current = null;
      refetchSeed({
        windowDays: localRef.current.windowDays,
        maxNeighbors: localRef.current.maxNeighbors,
      });
    }, REFETCH_DEBOUNCE_MS);
  };
  useEffect(
    () => () => {
      if (refetchTimerRef.current) window.clearTimeout(refetchTimerRef.current);
    },
    [],
  );

  // Profile chip toggles: ADDING triggers a refetch with the union; removing
  // is a pure client-side filter (persisted by the bulk sync).
  const toggleProfile = (profileId: string, adding: boolean) => {
    dispatch({ type: "TOGGLE_INVESTIGATE_PROFILE", profile: profileId });
    if (adding) {
      const union = Array.from(new Set([...local.investigateProfiles, profileId]));
      refetchSeed({ profiles: union });
    }
  };
  const toggleGroup = (profileIds: string[], on: boolean) => {
    const current = new Set(local.investigateProfiles);
    let added = false;
    for (const id of profileIds) {
      if (on && !current.has(id)) {
        current.add(id);
        added = true;
        dispatch({ type: "TOGGLE_INVESTIGATE_PROFILE", profile: id });
      } else if (!on && current.has(id)) {
        current.delete(id);
        dispatch({ type: "TOGGLE_INVESTIGATE_PROFILE", profile: id });
      }
    }
    if (on && added) refetchSeed({ profiles: Array.from(current) });
  };

  // ---- Empty state: no seed yet ----
  if (!seedId) {
    return <EmptySeedCard seedInvestigate={seedInvestigate} onBrowseAtlas={onBrowseAtlas} />;
  }

  const expandTarget = local.selection.nodeId ? "selected node" : "seed";

  return (
    <>
      <div className="ge-seedline">
        <span className="ge-seedline-label">Seed{seedKind ? ` · ${seedKind}` : ""}</span>
        <span className="ge-seedline-addr" title={seedId}>
          {seedId.startsWith("0x") ? shortAddr(seedId, 10, 8) : seedId}
        </span>
        <button
          type="button"
          className="ge-seedline-copy"
          onClick={() => navigator.clipboard?.writeText(seedId)}
          title="Copy seed id"
        >
          Copy
        </button>
        <span className="ge-seedline-stats">
          <span><b>{nodeCount}</b> nodes</span>
          <span><b>{edgeCount}</b> edges</span>
          <span>
            hop <b>{server.investigate?.hops_used ?? 0}</b>/{local.limits.max_hops}
          </span>
          <span>
            <b>{local.investigateProfiles.length}</b>/{server.catalog?.length ?? 0} profiles
          </span>
        </span>
        {modeSwitch}
      </div>
      <FilterBar
        windowDays={local.windowDays}
        maxNeighbors={local.maxNeighbors}
        layout={local.layout}
        statusFilter={local.statusFilter}
        expandDepth={local.expandDepth}
        limits={local.limits}
        expandTarget={expandTarget}
        canExpand={Boolean(local.selection.nodeId || seedId)}
        detailsOpen={detailsOpen}
        onWindowChange={(days) => {
          dispatch({ type: "SET_WINDOW", days });
          scheduleRefetch();
        }}
        onMaxNeighborsChange={(value) => {
          dispatch({ type: "SET_MAX_NEIGHBORS", value });
          scheduleRefetch();
        }}
        onLayoutChange={(layout) => dispatch({ type: "SET_LAYOUT", layout })}
        onStatusFilterChange={(filter) => dispatch({ type: "SET_STATUS_FILTER", filter })}
        onExpandDepthChange={(depth) => dispatch({ type: "SET_EXPAND_DEPTH", depth })}
        onExpand={() => expandNode(local.selection.nodeId || seedId)}
        onToggleDetails={() => setDetailsOpen((v) => !v)}
      />
      {trimmedCount > 0 && (
        <div
          className="ge-status-filter-note"
          title="Status filter hides some active profiles. Click 'All' to see them again."
        >
          Status filter ({local.statusFilter}) hides {trimmedCount} active
          profile{trimmedCount === 1 ? "" : "s"} from the canvas. Switch to
          "All" to restore.
        </div>
      )}
      <ProfileChips
        catalog={server.catalog ?? []}
        activeProfiles={local.investigateProfiles}
        statusFilter={local.statusFilter}
        onToggle={toggleProfile}
        onToggleGroup={toggleGroup}
      />
      <div className={`ge-body ${detailsOpen ? "details-open" : "details-closed"}`}>
        <main className="ge-canvas">
          <GraphCanvas
            model={model}
            selectedNodeId={local.selection.nodeId}
            seedNodeId={seedId}
            layout={local.layout}
            emptyHint="No nodes in this window — widen the time window or add profiles."
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onExpandNode={expandNode}
            onViewClick={onClearSelection}
          />
        </main>
        <DetailsPanel
          nodes={parsedNodes}
          edges={parsedEdges}
          selectedNodeId={local.selection.nodeId}
          selectedEdgeId={local.selection.edgeId}
          seedNodeId={seedId}
          nodeRoles={server.node_roles ?? {}}
          catalog={server.catalog ?? []}
          suggestions={server.suggested_next_hops ?? []}
          nodeEvidence={parseEvidenceRows(nodeEvidence?.rows)}
          edgeEvidence={parseEvidenceRows(edgeEvidence?.rows)}
          onExpand={expandNode}
          onRecenter={seedInvestigate}
          onApplyHop={(profileId) => {
            if (!local.investigateProfiles.includes(profileId)) {
              toggleProfile(profileId, true);
            }
          }}
          onSelectNode={onSelectNode}
        />
      </div>
    </>
  );
}

function EmptySeedCard({
  seedInvestigate,
  onBrowseAtlas,
}: {
  seedInvestigate: (nodeId: string) => void;
  onBrowseAtlas: () => void;
}) {
  const [seedInput, setSeedInput] = useState("");
  const submit = () => {
    const trimmed = seedInput.trim();
    if (trimmed) seedInvestigate(trimmed);
  };
  return (
    <div className="ge-empty-investigate">
      <div className="ge-empty-card">
        <h2>Investigate an address</h2>
        <p>Load a bounded subgraph around a seed and expand hop by hop.</p>
        <div className="ge-catalog-seed-row">
          <input
            type="text"
            value={seedInput}
            onChange={(e) => setSeedInput(e.target.value)}
            placeholder="0x… (EVM address, Safe, or any avatar)"
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            autoFocus
          />
          <button
            type="button"
            className="ge-btn primary"
            onClick={submit}
            disabled={!seedInput.trim()}
          >
            Explore →
          </button>
        </div>
        <button type="button" className="ge-empty-atlas-link" onClick={onBrowseAtlas}>
          …or browse the Atlas
        </button>
      </div>
    </div>
  );
}
