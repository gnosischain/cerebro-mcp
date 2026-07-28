// RELATIONSHIPS: one section, one canvas.
//
// This was two modes. "Catalog" (Atlas) browsed the profile catalog and, on
// clicking a profile, replaced its own rail with a definition list and a
// forty-row evidence table — widening that column to 40-100% of the screen —
// while the canvas beside it usually showed a centred "loading" hint, because
// the preview model was built from empty arrays until a five-way scope
// agreement held. "Investigate" showed the actual graph but could only be
// reached by seeding an address or double-clicking an Atlas node.
//
// Now: a persistent 280px picker rail on the left, one canvas that ALWAYS has
// its chrome, and a single scope strip that says what is being drawn. Which
// rows the canvas draws is decided by `resolveCanvasSource` — a pure function
// with four outcomes (preview / seed / sample / nothing), each of which either
// has rows or says why it does not.

import { useEffect, useMemo, useRef, useState } from "react";
import { shortAddr } from "../../../utils/format";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import { DetailsPanel } from "../DetailsPanel";
import { FilterBar } from "../FilterBar";
import { EvidencePanel, EvidenceTrigger } from "../ForensicScopeDisclosure";
import { EdgeTypesMenu } from "../ProfileChips";
import { RelationPreviewCard } from "../RelationPreviewCard";
import { ScopeStrip } from "../ScopeStrip";
import { GraphCanvas } from "../canvas/GraphCanvas";
import {
  buildGraphModel,
  parseEvidenceRows,
  parseNodeRows,
  shortId,
} from "../model/parseRows";
import { resolveCanvasSource } from "../model/relationshipCanvasSource";
import type { RelationshipCanvasSource } from "../model/relationshipCanvasSource";
import { SECTOR_COLOR, groupProfilesBySector } from "../model/sectors";
import type { GraphAction, GraphLocalState } from "../state/graphReducer";
import type { EvidenceExpectation, GraphExplorerViewState } from "../types";

const REFETCH_DEBOUNCE_MS = 600;
const SAMPLE_DEBOUNCE_MS = 400;

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
  /** Catalog sample (REPLACE semantics; empty profile list clears it). */
  atlasNodes: HydratedDataset | undefined;
  atlasEdges: HydratedDataset | undefined;
  atlasNodeDescriptor?: DatasetDescriptor;
  atlasEdgeDescriptor?: DatasetDescriptor;
  atlasPreviewNodes: HydratedDataset | undefined;
  atlasPreviewEdges: HydratedDataset | undefined;
  atlasPreviewNodeDescriptor?: DatasetDescriptor;
  atlasPreviewEdgeDescriptor?: DatasetDescriptor;
  loadSample: (profiles: string[]) => void;
  /** Inspect-only single-profile preview. */
  loadPreview: (profile: string) => void;
  previewLoading: boolean;
  previewError: string | null;
  desiredPreviewRequestId: number;
  /** Re-query the CURRENT seed with fresh filters. */
  refetchSeed: (overrides: RefetchOverrides) => void;
  /** Seed a NEW subgraph from an address / node id. */
  seedInvestigate: (nodeId: string) => void;
  /** BFS-expand a node by the current stepper depth. */
  expandNode: (nodeId: string) => void;
  loading: boolean;
  loadError: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
}

export function RelationshipsView({
  server,
  local,
  dispatch,
  nodes,
  edges,
  nodeEvidence,
  edgeEvidence,
  evidenceExpectation,
  atlasNodes,
  atlasEdges,
  atlasNodeDescriptor,
  atlasEdgeDescriptor,
  atlasPreviewNodes,
  atlasPreviewEdges,
  atlasPreviewNodeDescriptor,
  atlasPreviewEdgeDescriptor,
  loadSample,
  loadPreview,
  previewLoading,
  previewError,
  desiredPreviewRequestId,
  refetchSeed,
  seedInvestigate,
  expandNode,
  loading,
  loadError,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
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

  // ---- Rail + panel state ----
  const [seedInput, setSeedInput] = useState("");
  const [filter, setFilter] = useState("");
  const [previewProfile, setPreviewProfile] = useState("");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceTriggerRef = useRef<HTMLButtonElement>(null);

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

  // ---- Which rows does the canvas draw? ----
  const appliedSampleProfiles = server.atlas?.selected_profiles ?? [];
  const appliedSampleKey = [...appliedSampleProfiles].sort().join("|");
  const draftSampleKey = [...local.atlasProfiles].sort().join("|");
  const sampleDraftStale = draftSampleKey !== appliedSampleKey;
  const previewState = server.atlas_preview;
  const sampleProfiles = sampleDraftStale
    ? appliedSampleProfiles
    : local.atlasProfiles;
  const source = useMemo(
    () =>
      resolveCanvasSource({
        seedId,
        seedScope: server.investigate?.scope,
        seedNodeRows: nodes?.rows,
        seedEdgeRows: edges?.rows,
        seedProfiles: modelProfiles,
        seedLoading: loading,
        seedError: loadError,
        seedControlsStale: controlsStale,

        sampleScope: server.atlas?.scope,
        sampleNodeRows: atlasNodes?.rows,
        sampleEdgeRows: atlasEdges?.rows,
        sampleNodeDescriptor: atlasNodeDescriptor,
        sampleEdgeDescriptor: atlasEdgeDescriptor,
        sampleProfiles,
        sampleLoading: loading,
        sampleError: loadError,
        sampleDraftStale,

        previewProfile,
        previewStateProfile: previewState?.profile ?? "",
        previewScope: previewState?.scope,
        previewRequestId: Number(previewState?.scope?.request_id ?? -1),
        desiredPreviewRequestId,
        previewNodeDescriptor: atlasPreviewNodeDescriptor,
        previewEdgeDescriptor: atlasPreviewEdgeDescriptor,
        previewDatasetError:
          atlasPreviewNodes?.error || atlasPreviewEdges?.error || null,
        previewLoading,
        previewError,

        datasetScopes: server.dataset_scopes,
      }),
    [
      seedId,
      server.investigate?.scope,
      server.atlas?.scope,
      server.dataset_scopes,
      nodes?.rows,
      edges?.rows,
      modelProfiles,
      loading,
      loadError,
      controlsStale,
      atlasNodes?.rows,
      atlasEdges?.rows,
      atlasNodeDescriptor,
      atlasEdgeDescriptor,
      sampleProfiles,
      sampleDraftStale,
      previewProfile,
      previewState,
      desiredPreviewRequestId,
      atlasPreviewNodeDescriptor,
      atlasPreviewEdgeDescriptor,
      atlasPreviewNodes?.error,
      atlasPreviewEdges?.error,
      previewLoading,
      previewError,
    ],
  );

  const model = useMemo(
    () =>
      buildGraphModel(source.nodeRows, source.edgeRows, source.profiles, {
        profileSelectionPhase: source.scope ? "applied" : "unresolved",
      }),
    [source],
  );
  const parsedNodes = useMemo(
    () => parseNodeRows(source.nodeRows),
    [source.nodeRows],
  );

  // Seed-branch visibility machinery (legend + ranked table + canvas agree).
  // Preview and sample draw exactly what the server returned for the profiles
  // they asked about, so they are not additionally trimmed here.
  const visibleRelationshipProfiles = useMemo(() => {
    if (source.kind !== "seed") return new Set(model.profileColor.keys());
    return !hasAppliedProfileScope && effectiveProfiles.length === 0
      ? new Set(model.profileColor.keys())
      : new Set(effectiveProfiles);
  }, [effectiveProfiles, hasAppliedProfileScope, model.profileColor, source.kind]);
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

  // Debounced REPLACE-semantics sample load. The ref starts at the CURRENT
  // selection so adoption echoes never refire what the server already loaded;
  // only user toggles do.
  const profilesKey = local.atlasProfiles.join("|");
  const lastRequestedRef = useRef(profilesKey);
  useEffect(() => {
    if (profilesKey === lastRequestedRef.current) return;
    const timer = window.setTimeout(() => {
      lastRequestedRef.current = localRef.current.atlasProfiles.join("|");
      loadSample(localRef.current.atlasProfiles);
    }, SAMPLE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profilesKey]);

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

  // ---- Rail: the relationship-type catalog ----
  const sectors = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const visible = (server.catalog ?? []).filter((p) => {
      if (local.statusFilter !== "all" && p.semantic_status !== local.statusFilter)
        return false;
      if (!needle) return true;
      const hay = [p.profile, p.module, p.description, ...p.question_synonyms]
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
    return groupProfilesBySector(visible);
  }, [server.catalog, filter, local.statusFilter]);

  const activeSet = new Set(local.atlasProfiles);
  const previewCard = (server.catalog ?? []).find(
    (p) => p.profile === previewProfile,
  );
  const beginPreview = (profile: string) => {
    setPreviewProfile(profile);
    loadPreview(profile);
  };
  const leavePreview = () => setPreviewProfile("");
  const applyPreview = () => {
    if (!previewCard || source.blocker) return;
    if (!activeSet.has(previewCard.profile)) {
      dispatch({ type: "TOGGLE_ATLAS_PROFILE", profile: previewCard.profile });
    }
    leavePreview();
  };
  const submitSeed = () => {
    const trimmed = seedInput.trim();
    if (trimmed) seedInvestigate(trimmed);
  };

  const selectedNodeId = local.selection.nodeId;
  const expandTarget = selectedNodeId ? "selected node" : "seed";
  const activeScope =
    source.kind === "preview"
      ? source.scope
      : source.kind === "sample"
        ? server.atlas?.scope
        : server.investigate?.scope;
  const evidenceDatasets =
    source.kind === "preview"
      ? `Relationship preview for ${source.previewProfile}`
      : source.kind === "sample"
        ? "applied catalog sample nodes and edges"
        : "relationship nodes, edges, and metrics";
  // Ranked table + details describe an INVESTIGATION. A preview is an
  // inspect-only sample of ONE relation, so neither is shown for it — which is
  // what Catalog did too, and it keeps preview rows out of a panel whose
  // evidence is resolved against the applied scope.
  const showAnalysisPanes = source.kind !== "preview";

  return (
    <>
      {loadError ? (
        <div className="ge-load-error" role="alert">
          <span>Relationships failed to load: {loadError}</span>
          <button
            type="button"
            className="ge-btn"
            onClick={() =>
              seedId
                ? refetchSeed({
                    profiles: appliedProfiles,
                    windowDays:
                      Number(server.investigate?.window_days) || local.windowDays,
                    maxNeighbors:
                      Number(server.investigate?.max_neighbors) ||
                      local.maxNeighbors,
                  })
                : loadSample(local.atlasProfiles)
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
        canExpand={Boolean(selectedNodeId || seedId)}
        detailsOpen={detailsOpen}
        leftSlot={
          seedId ? (
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
          ) : (
            <div className="ge-seedcell">
              <span className="ge-seedline-label">No seed</span>
              <span className="ge-seedline-addr">browsing relationship types</span>
            </div>
          )
        }
        accessorySlot={
          <EvidenceTrigger
            scope={activeScope}
            datasets={evidenceDatasets}
            open={evidenceOpen}
            onOpen={() => {
              setDetailsOpen(false);
              setEvidenceOpen(true);
            }}
            buttonRef={evidenceTriggerRef}
          />
        }
        edgeTypesSlot={
          <EdgeTypesMenu
            catalog={server.catalog ?? []}
            activeProfiles={local.investigateProfiles}
            statusFilter={local.statusFilter}
            onStatusFilterChange={(next) =>
              dispatch({ type: "SET_STATUS_FILTER", filter: next })
            }
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
        onExpand={() => expandNode(selectedNodeId || seedId)}
        onToggleDetails={() => {
          setEvidenceOpen(false);
          setDetailsOpen((v) => !v);
        }}
      />

      <div
        className={`ge-body ge-body--relationships ${
          detailsOpen ? "details-open" : "details-closed"
        }${showAnalysisPanes ? "" : " no-analysis"}`}
      >
        <aside className="ge-rel-rail" aria-label="Relationship picker">
          <div className="ge-catalog-seed">
            <span className="ge-catalog-seed-label">Start from an address</span>
            <div className="ge-catalog-seed-row">
              <input
                type="text"
                value={seedInput}
                onChange={(e) => setSeedInput(e.target.value)}
                placeholder="0x… address, Safe, or avatar"
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitSeed();
                }}
              />
              <button
                type="button"
                className="ge-btn primary"
                onClick={submitSeed}
                disabled={!seedInput.trim()}
              >
                Go →
              </button>
            </div>
          </div>

          {previewCard ? (
            <RelationPreviewCard
              profile={previewCard}
              scope={source.scope}
              windowDays={previewState?.window_days ?? local.windowDays}
              ready={!source.blocker}
              problem={
                source.blocker?.reason === "failed" ? source.blocker.detail : null
              }
              applied={activeSet.has(previewCard.profile)}
              onBack={leavePreview}
              onApply={applyPreview}
              onRetry={() => loadPreview(previewCard.profile)}
              evidenceOpen={evidenceOpen}
              onOpenEvidence={() => {
                setDetailsOpen(false);
                setEvidenceOpen(true);
              }}
              evidenceTriggerRef={evidenceTriggerRef}
              evidenceDatasets={evidenceDatasets}
            />
          ) : (
            <>
              <div className="ge-atlas-filter">
                <input
                  type="text"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Filter relationship types…"
                />
                <span className="ge-catalog-count">
                  {local.atlasProfiles.length} on
                </span>
              </div>
              <div className="ge-atlas-list">
                {sectors.map(([sector, profiles]) => (
                  <div key={sector} className="ge-atlas-group">
                    <div
                      className="ge-atlas-group-head"
                      style={{ color: SECTOR_COLOR[sector] ?? "var(--text-muted)" }}
                    >
                      {sector}
                    </div>
                    {profiles.map((p) => (
                      <button
                        type="button"
                        key={p.profile}
                        className={`ge-atlas-item ${
                          previewProfile === p.profile ? "is-preview" : ""
                        } ${activeSet.has(p.profile) ? "is-applied" : ""}`}
                        title={`${p.description || p.profile}\n${p.source_kind} → ${p.target_kind}`}
                        onClick={() => beginPreview(p.profile)}
                      >
                        <span
                          className={`ge-dot ${p.semantic_status === "approved" ? "ge-dot-approved" : "ge-dot-candidate"}`}
                          aria-hidden
                        />
                        <span className="ge-atlas-item-name">{p.profile}</span>
                        <span className="ge-atlas-item-state">
                          {activeSet.has(p.profile) ? "added" : "preview"}
                        </span>
                      </button>
                    ))}
                  </div>
                ))}
                {!sectors.length ? (
                  <div className="ge-catalog-empty">
                    No relationship types match — clear the filter or relax the
                    status toggle.
                  </div>
                ) : null}
              </div>
            </>
          )}
        </aside>

        <main className={`ge-canvas${source.stale ? " is-stale" : ""}`}>
          <ScopeStrip
            source={source}
            scope={activeScope}
            windowDays={local.windowDays}
            appliedWindowDays={Number(server.investigate?.window_days) || null}
            statusFilter={local.statusFilter}
            trimmedCount={source.kind === "seed" ? trimmedCount : 0}
            onClearStatusFilter={() =>
              dispatch({ type: "SET_STATUS_FILTER", filter: "all" })
            }
            onLeavePreview={leavePreview}
          />
          <GraphCanvas
            model={model}
            stateKey={
              source.kind === "preview"
                ? `relationships:preview:${source.previewProfile}`
                : `relationships:${source.kind}`
            }
            selectedNodeId={selectedNodeId}
            selectedEdgeId={local.selection.edgeId}
            seedNodeId={seedId || undefined}
            emptyHint={
              <CanvasEmptyState source={source} onSubmitSeed={seedInvestigate} />
            }
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onExpandNode={source.kind === "seed" ? expandNode : seedInvestigate}
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
          {selectedNodeId &&
          model.idToIndex.has(selectedNodeId) &&
          source.kind !== "seed" ? (
            <div className="ge-atlas-popover">
              <span className="ge-atlas-popover-id" title={selectedNodeId}>
                {shortId(selectedNodeId)}
              </span>
              <button
                type="button"
                className="ge-btn primary"
                onClick={() => seedInvestigate(selectedNodeId)}
              >
                Investigate {shortId(selectedNodeId)} →
              </button>
            </div>
          ) : null}
        </main>

        {showAnalysisPanes ? (
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
                      className={
                        local.selection.edgeId === edge.id ? "is-selected" : ""
                      }
                      onClick={() => onSelectEdge(edge.id)}
                      title={`${edge.source} → ${edge.target}\n${edge.profile}`}
                    >
                      <span className="ge-ranked-table__rank">{index + 1}</span>
                      <span className="ge-ranked-table__edge">
                        <strong>
                          {shortAddr(edge.source, 6, 4)} → {shortAddr(edge.target, 6, 4)}
                        </strong>
                        <small>
                          {edge.edge_count.toLocaleString()} source row
                          {edge.edge_count === 1 ? "" : "s"}
                        </small>
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
        ) : null}

        {showAnalysisPanes ? (
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
        ) : null}
      </div>

      {evidenceOpen ? (
        <EvidencePanel
          scope={activeScope}
          datasets={evidenceDatasets}
          onClose={() => setEvidenceOpen(false)}
          openerRef={evidenceTriggerRef}
        />
      ) : null}
    </>
  );
}

/** Centre-stage empty state. A node, not a string, so the primary recovery
 * action sits where the user is already looking instead of only being
 * described. `.ge-placeholder` is `pointer-events: none`; `.ge-placeholder__body`
 * re-enables them so this input is actually usable. */
function CanvasEmptyState({
  source,
  onSubmitSeed,
}: {
  source: RelationshipCanvasSource;
  onSubmitSeed: (nodeId: string) => void;
}) {
  const [value, setValue] = useState("");
  const blocker = source.blocker;
  if (!blocker) return null;

  const submit = () => {
    const trimmed = value.trim();
    if (trimmed) onSubmitSeed(trimmed);
  };

  const heading =
    blocker.reason === "loading"
      ? "Loading…"
      : blocker.reason === "failed"
        ? "That could not be loaded"
        : blocker.reason === "stale-scope"
          ? "Waiting for the answering query"
          : blocker.reason === "no-rows"
            ? "Nothing in this scope"
            : "Investigate an address";

  return (
    <div className="ge-empty-card">
      <h2>{heading}</h2>
      <p>{blocker.detail}</p>
      {blocker.reason === "nothing-chosen" ? (
        <div className="ge-catalog-seed-row">
          <input
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="0x… (EVM address, Safe, or any avatar)"
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
          />
          <button
            type="button"
            className="ge-btn primary"
            onClick={submit}
            disabled={!value.trim()}
          >
            Explore →
          </button>
        </div>
      ) : null}
    </div>
  );
}
