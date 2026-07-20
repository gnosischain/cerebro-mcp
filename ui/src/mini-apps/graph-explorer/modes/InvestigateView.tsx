// INVESTIGATE mode: seed line (id + counters from graph_metrics), FilterBar,
// ProfileChips, the canvas over the investigate dataset pair, and the
// DetailsPanel. With no seed loaded it shows a centered seed-input card with
// an "or browse the Atlas" escape hatch.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { shortAddr } from "../../../utils/format";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { DetailsPanel } from "../DetailsPanel";
import { FilterBar } from "../FilterBar";
import { EvidencePanel, EvidenceTrigger } from "../ForensicScopeDisclosure";
import { EdgeTypesMenu } from "../ProfileChips";
import { GraphCanvas } from "../canvas/GraphCanvas";
import {
  buildGraphModel,
  parseEvidenceRows,
  parseNodeRows,
} from "../model/parseRows";
import type { GraphAction, GraphLocalState } from "../state/graphReducer";
import type { EvidenceExpectation, GraphExplorerViewState } from "../types";

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
  evidenceExpectation: EvidenceExpectation | null;
  /** Re-query the CURRENT seed with fresh filters. */
  refetchSeed: (overrides: RefetchOverrides) => void;
  /** Seed a NEW investigate subgraph from an address / node id. */
  seedInvestigate: (nodeId: string) => void;
  /** BFS-expand a node by the current stepper depth. */
  expandNode: (nodeId: string) => void;
  loading: boolean;
  loadError: string | null;
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
  evidenceExpectation,
  refetchSeed,
  seedInvestigate,
  expandNode,
  loading,
  loadError,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  onBrowseAtlas,
  modeSwitch,
}: Props) {
  const seedId = server.investigate?.seed?.id ?? "";
  const seedKind = server.investigate?.seed?.kind ?? "";
  const appliedProfiles = server.investigate?.active_profiles ?? [];
  const appliedProfileSet = new Set(appliedProfiles);
  // Removing an already-loaded profile is an immediate visibility operation;
  // only additions require data the applied scope may not contain.
  const profilesNeedData = local.investigateProfiles.some(
    (profile) => !appliedProfileSet.has(profile),
  );
  const controlsStale = Boolean(
    loading ||
      profilesNeedData ||
      local.windowDays !== Number(server.investigate?.window_days) ||
      local.maxNeighbors !== Number(server.investigate?.max_neighbors),
  );

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
  const appliedEffectiveProfiles = useMemo(
    () =>
      local.statusFilter === "all"
        ? appliedProfiles
        : appliedProfiles.filter((id) => {
            const profile = catalogByProfile.get(id);
            return !profile || profile.semantic_status === local.statusFilter;
          }),
    [appliedProfiles, local.statusFilter, catalogByProfile],
  );
  const hasAppliedProfileScope = Boolean(server.investigate?.scope?.scope_id);
  // Keep the server-applied edge universe mounted while client visibility
  // changes. The controlled set below then drives canvas, legend and table
  // together without deleting a hidden profile from the legend immediately.
  const modelProfiles = hasAppliedProfileScope
    ? appliedEffectiveProfiles
    : effectiveProfiles;

  const model = useMemo(
    () =>
      buildGraphModel(nodes?.rows, edges?.rows, modelProfiles, {
        profileSelectionPhase: hasAppliedProfileScope
          ? "applied"
          : "unresolved",
      }),
    [nodes?.rows, edges?.rows, modelProfiles, hasAppliedProfileScope],
  );
  const parsedNodes = useMemo(() => parseNodeRows(nodes?.rows), [nodes?.rows]);
  const visibleRelationshipProfiles = useMemo(
    () =>
      !hasAppliedProfileScope && effectiveProfiles.length === 0
        ? new Set(model.profileColor.keys())
        : new Set(effectiveProfiles),
    [effectiveProfiles, hasAppliedProfileScope, model.profileColor],
  );
  const visibleEdges = useMemo(
    () =>
      model.edgeRows.filter((edge) =>
        visibleRelationshipProfiles.has(edge.profile),
      ),
    [model.edgeRows, visibleRelationshipProfiles],
  );
  const visibleModelProfileCount = useMemo(
    () =>
      [...model.profileColor.keys()].filter((profile) =>
        visibleRelationshipProfiles.has(profile),
      ).length,
    [model.profileColor, visibleRelationshipProfiles],
  );
  const rankedGroups = useMemo(() => {
    const byProfile = new Map<string, typeof visibleEdges>();
    for (const edge of visibleEdges) {
      const group = byProfile.get(edge.profile) ?? [];
      group.push(edge);
      byProfile.set(edge.profile, group);
    }
    return [...byProfile.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([profile, profileEdges]) => ({
        profile,
        edges: profileEdges
          .sort(
            (a, b) =>
              (Number.isFinite(b.weight) ? b.weight : Number.NEGATIVE_INFINITY) -
                (Number.isFinite(a.weight) ? a.weight : Number.NEGATIVE_INFINITY) ||
              b.edge_count - a.edge_count,
          )
          .slice(0, 100),
        unit:
          catalogByProfile.get(profile)?.weight_unit ||
          catalogByProfile.get(profile)?.weight_column ||
          "edge count",
      }));
  }, [visibleEdges, catalogByProfile]);
  const rankedEdgeCount = rankedGroups.reduce(
    (count, group) => count + group.edges.length,
    0,
  );

  // Counters now come from the MODEL (canvas truth) via the CanvasStats chip;
  // the graph_metrics dataset stays attached for agents/tools but is no
  // longer the UI's source.

  // Details panel: open by default on wide viewports, closed on narrow
  // (overlay style) so the graph canvas owns the entire visible area.
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceTriggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!local.selection.nodeId && !local.selection.edgeId) return;
    if (typeof window !== "undefined" && window.innerWidth < 900) {
      setDetailsOpen(true);
      setEvidenceOpen(false);
    }
  }, [local.selection.nodeId, local.selection.edgeId]);

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
    if (
      !adding &&
      model.edgeRows.some(
        (edge) =>
          edge.id === local.selection.edgeId && edge.profile === profileId,
      )
    ) {
      onClearSelection();
    }
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
    return (
      <>
        {loadError ? (
          <div className="ge-load-error" role="alert">
            Relationship load failed: {loadError}. Re-enter the address to retry.
          </div>
        ) : null}
        <EmptySeedCard seedInvestigate={seedInvestigate} onBrowseAtlas={onBrowseAtlas} />
      </>
    );
  }

  const expandTarget = local.selection.nodeId ? "selected node" : "seed";

  return (
    <>
      {loadError ? (
        <div className="ge-load-error" role="alert">
          <span>Relationships failed to load: {loadError}</span>
          <button
            type="button"
            className="ge-btn"
            onClick={() =>
              refetchSeed({
                profiles: appliedProfiles,
                windowDays: Number(server.investigate?.window_days) || local.windowDays,
                maxNeighbors:
                  Number(server.investigate?.max_neighbors) || local.maxNeighbors,
              })
            }
          >
            Retry applied scope
          </button>
        </div>
      ) : null}
      <FilterBar
        windowDays={local.windowDays}
        maxNeighbors={local.maxNeighbors}
        expandDepth={local.expandDepth}
        limits={local.limits}
        expandTarget={expandTarget}
        canExpand={Boolean(local.selection.nodeId || seedId)}
        detailsOpen={detailsOpen}
        leftSlot={
          <div className="ge-seedcell">
            <span className="ge-seedline-label">
              Seed{seedKind ? ` · ${seedKind}` : ""}
            </span>
            <span className="ge-seedline-addr" title={seedId}>
              {seedId.startsWith("0x") ? shortAddr(seedId, 8, 6) : seedId}
            </span>
            <button
              type="button"
              className="ge-seedline-copy"
              onClick={() => navigator.clipboard?.writeText(seedId)}
              title="Copy seed id"
            >
              Copy
            </button>
          </div>
        }
        endSlot={modeSwitch}
        accessorySlot={
          <EvidenceTrigger
            scope={server.investigate?.scope}
            datasets="relationship nodes, edges, and metrics"
            open={evidenceOpen}
            onOpen={() => {
              setDetailsOpen(false);
              setEvidenceOpen(true);
            }}
            buttonRef={evidenceTriggerRef}
          />
        }
        statusSlot={controlsStale ? (
          <span className="ge-pending-chip" role="status" title={`Showing applied relationships for ${Number(server.investigate?.window_days) || "?"}d and ${Number(server.investigate?.max_neighbors) || "?"} neighbours`}>
            Applied results · {loading ? "draft pending" : "draft not applied"}
          </span>
        ) : null}
        edgeTypesSlot={
          <EdgeTypesMenu
            catalog={server.catalog ?? []}
            activeProfiles={local.investigateProfiles}
            statusFilter={local.statusFilter}
            onStatusFilterChange={(filter) => dispatch({ type: "SET_STATUS_FILTER", filter })}
            onToggle={toggleProfile}
            onToggleGroup={toggleGroup}
          />
        }
        onWindowChange={(days) => {
          dispatch({ type: "SET_WINDOW", days });
          scheduleRefetch();
        }}
        onMaxNeighborsChange={(value) => {
          dispatch({ type: "SET_MAX_NEIGHBORS", value });
          scheduleRefetch();
        }}
        onExpandDepthChange={(depth) => dispatch({ type: "SET_EXPAND_DEPTH", depth })}
        onExpand={() => expandNode(local.selection.nodeId || seedId)}
        onToggleDetails={() => {
          setEvidenceOpen(false);
          setDetailsOpen((v) => !v);
        }}
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
      <div
        className={`ge-body ge-body--relationships ${
          detailsOpen ? "details-open" : "details-closed"
        }`}
      >
        <main className={`ge-canvas${controlsStale ? " is-stale" : ""}`}>
          <GraphCanvas
            model={model}
            stateKey="relationships:investigate"
            selectedNodeId={local.selection.nodeId}
            selectedEdgeId={local.selection.edgeId}
            seedNodeId={seedId}
            emptyHint="No nodes in this window — widen the time window or add profiles."
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onExpandNode={expandNode}
            onViewClick={onClearSelection}
            visibleProfiles={visibleRelationshipProfiles}
            onToggleProfileVisibility={toggleProfile}
            fallbackNodeActionLabel="Investigate from here"
            stats={{
              // Canvas-truth values (NOT server graph_metrics — those can
              // disagree when the status filter trims profiles or hydration
              // is mid-flight, and the chip's tooltip says "on canvas").
              nodeCount: model.n,
              edgeCount: visibleEdges.length,
              hopsUsed: server.investigate?.hops_used ?? 0,
              maxHops: local.limits.max_hops,
              activeProfileCount: visibleModelProfileCount,
              catalogSize: server.catalog?.length ?? 0,
            }}
          />
        </main>
        <section className="ge-ranked-table" aria-label="Ranked relationships">
          <header>
            <div>
              <strong>Ranked neighbours</strong>
              <span>{rankedEdgeCount} visible relationships</span>
            </div>
            <small>ranked within profile + unit</small>
          </header>
          <div className="ge-ranked-table__rows" role="list">
            {rankedGroups.map((group) => (
              <section key={group.profile} className="ge-ranked-table__group">
                <h3>
                  <span>{group.profile}</span>
                  <small>{group.unit}</small>
                </h3>
                {group.edges.map((edge, index) => (
                  <button
                    type="button"
                    role="listitem"
                    key={edge.id}
                    className={local.selection.edgeId === edge.id ? "is-selected" : ""}
                    onClick={() => onSelectEdge(edge.id)}
                    title={`${edge.source} → ${edge.target}\n${edge.profile}`}
                  >
                    <span className="ge-ranked-table__rank">{index + 1}</span>
                    <span className="ge-ranked-table__edge">
                      <strong>{shortAddr(edge.source, 6, 4)} → {shortAddr(edge.target, 6, 4)}</strong>
                      <small>{edge.edge_count.toLocaleString()} source row{edge.edge_count === 1 ? "" : "s"}</small>
                    </span>
                    <span className="ge-ranked-table__weight">
                      {Number.isFinite(edge.weight)
                        ? edge.weight.toLocaleString(undefined, {
                            maximumFractionDigits: 2,
                          })
                        : "unknown"}
                    </span>
                  </button>
                ))}
              </section>
            ))}
            {!rankedEdgeCount ? (
              <p>No relationships match the applied profile selection.</p>
            ) : null}
          </div>
        </section>
        <DetailsPanel
          nodes={parsedNodes}
          edges={visibleEdges}
          selectedNodeId={local.selection.nodeId}
          selectedEdgeId={local.selection.edgeId}
          seedNodeId={seedId}
          nodeRoles={server.node_roles ?? {}}
          catalog={server.catalog ?? []}
          suggestions={server.suggested_next_hops ?? []}
          nodeEvidence={parseEvidenceRows(nodeEvidence?.rows)}
          edgeEvidence={parseEvidenceRows(edgeEvidence?.rows)}
          evidenceExpectation={evidenceExpectation}
          onExpand={expandNode}
          onRecenter={seedInvestigate}
          onApplyHop={(profileId) => {
            if (!local.investigateProfiles.includes(profileId)) {
              toggleProfile(profileId, true);
            }
          }}
          onSelectNode={onSelectNode}
          onClose={() => setDetailsOpen(false)}
        />
      </div>
      {evidenceOpen ? (
        <EvidencePanel
          scope={server.investigate?.scope}
          datasets="relationship nodes, edges, and metrics"
          onClose={() => setEvidenceOpen(false)}
          openerRef={evidenceTriggerRef}
        />
      ) : null}
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
