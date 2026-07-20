// ATLAS mode: browse the semantic graph catalog as a sector-grouped rail of
// multi-select profiles; the canvas shows a REPLACE-semantics sample union
// (atlas_nodes/atlas_edges) over exactly the checked profiles. Clicking a
// node offers "Investigate →" which seeds INVESTIGATE mode from that node.
// An address input at the top seeds investigate directly.

import { useEffect, useMemo, useRef, useState } from "react";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import { EvidencePanel, EvidenceTrigger } from "../ForensicScopeDisclosure";
import { GraphCanvas } from "../canvas/GraphCanvas";
import { buildGraphModel, shortId } from "../model/parseRows";
import { SECTOR_COLOR, groupProfilesBySector } from "../model/sectors";
import type { GraphAction, GraphLocalState } from "../state/graphReducer";
import type {
  ForensicCoverageCount,
  ForensicScope,
  GraphExplorerViewState,
} from "../types";

const SAMPLE_DEBOUNCE_MS = 400;

interface Props {
  server: GraphExplorerViewState;
  local: GraphLocalState;
  dispatch: (action: GraphAction) => void;
  atlasNodes: HydratedDataset | undefined;
  atlasEdges: HydratedDataset | undefined;
  atlasPreviewNodes: HydratedDataset | undefined;
  atlasPreviewEdges: HydratedDataset | undefined;
  atlasPreviewNodeDescriptor?: DatasetDescriptor;
  atlasPreviewEdgeDescriptor?: DatasetDescriptor;
  /** Fires load_graph_atlas_sample (REPLACE semantics; empty list clears). */
  loadSample: (profiles: string[]) => void;
  loading: boolean;
  loadError: string | null;
  /** Fires the inspect-only load_graph_atlas_preview path. */
  loadPreview: (profile: string) => void;
  previewLoading: boolean;
  previewError: string | null;
  /** Newest local intent, including a request queued behind another preview. */
  desiredPreviewRequestId: number;
  /** Seeds INVESTIGATE mode from an address / node id. */
  seedInvestigate: (nodeId: string) => void;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
}

export function relationshipWeightUnit(column: string | null | undefined): string {
  const normalized = String(column ?? "").trim().toLowerCase();
  if (!normalized) return "Unweighted relationship (edge count)";
  if (normalized.includes("usd")) return "USD value";
  if (normalized.includes("count")) return "Count";
  if (normalized.includes("percent") || normalized.includes("ratio")) return "Ratio";
  if (normalized.includes("score")) return "Score";
  if (normalized.includes("amount") || normalized.includes("balance")) {
    return "Token amount (native units)";
  }
  return normalized
    .split("_")
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function relationshipTemporalSupport(
  semantics: string | undefined,
  windowDays: number,
): string {
  switch (semantics) {
    case "event":
      return `Events in the applied ${windowDays}-day window`;
    case "state_at":
      return "State established on or before retrieval time";
    case "interval":
      return `Validity interval overlaps the applied ${windowDays}-day window`;
    case "current_snapshot":
      return "Current at retrieval; historical state unavailable";
    default:
      return "Temporal contract not declared";
  }
}

function coverageLabel(label: string, value: ForensicCoverageCount | undefined) {
  if (!value || value.shown == null) return `${label}: unknown`;
  return value.total == null
    ? `${label}: ${value.shown.toLocaleString()} shown · total unknown`
    : `${label}: ${value.shown.toLocaleString()} of ${value.total.toLocaleString()}`;
}

function scopeHorizon(scope: ForensicScope | undefined): string {
  if (!scope) return "awaiting preview";
  if (scope.data_horizon != null && String(scope.data_horizon)) {
    return String(scope.data_horizon);
  }
  const sourceHorizons = (scope.sources ?? [])
    .map((source) => source.horizon)
    .filter((horizon) => horizon != null && String(horizon));
  return sourceHorizons.length
    ? [...new Set(sourceHorizons.map(String))].join(", ")
    : "not reported (use fetched time)";
}

export function AtlasView({
  server,
  local,
  dispatch,
  atlasNodes,
  atlasEdges,
  atlasPreviewNodes,
  atlasPreviewEdges,
  atlasPreviewNodeDescriptor,
  atlasPreviewEdgeDescriptor,
  loadSample,
  loading,
  loadError,
  loadPreview,
  previewLoading,
  previewError,
  desiredPreviewRequestId,
  seedInvestigate,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
}: Props) {
  const [seedInput, setSeedInput] = useState("");
  const [filter, setFilter] = useState("");
  const [previewProfile, setPreviewProfile] = useState("");
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const evidenceTriggerRef = useRef<HTMLButtonElement>(null);
  // Entering Catalog must reveal the catalog at every supported width. Atlas
  // is the default URL mode, so relying on an explicit `?mode=atlas` made the
  // compact route open onto an empty canvas with its only workflow hidden.
  const [catalogOpen, setCatalogOpen] = useState(true);

  // The catalog is a persistent rail on desktop and an explicitly closeable
  // filter drawer at the supported compact widths. Keep the same DOM/content
  // so selection and preview state survive breakpoint changes.
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 900px)");
    const syncForBreakpoint = () => {
      if (desktop.matches) setCatalogOpen(true);
    };
    syncForBreakpoint();
    desktop.addEventListener("change", syncForBreakpoint);
    return () => desktop.removeEventListener("change", syncForBreakpoint);
  }, []);

  const appliedProfiles = server.atlas?.selected_profiles ?? [];
  const profileDraftStale =
    [...local.atlasProfiles].sort().join("|") !==
    [...appliedProfiles].sort().join("|");
  const stale = loading || profileDraftStale;
  const modelProfiles = stale ? appliedProfiles : local.atlasProfiles;
  const appliedModel = useMemo(
    () =>
      buildGraphModel(atlasNodes?.rows, atlasEdges?.rows, modelProfiles, {
        profileSelectionPhase: server.atlas?.scope?.scope_id
          ? "applied"
          : "unresolved",
      }),
    [atlasNodes?.rows, atlasEdges?.rows, modelProfiles, server.atlas?.scope?.scope_id],
  );

  // Debounced REPLACE-semantics sample load. The ref starts at the CURRENT
  // selection so adoption echoes (and re-entering atlas mode) never refire
  // what the server already loaded; only user toggles do.
  const profilesKey = local.atlasProfiles.join("|");
  const lastRequestedRef = useRef(profilesKey);
  const localRef = useRef(local);
  localRef.current = local;
  useEffect(() => {
    if (profilesKey === lastRequestedRef.current) return;
    const timer = window.setTimeout(() => {
      lastRequestedRef.current = localRef.current.atlasProfiles.join("|");
      loadSample(localRef.current.atlasProfiles);
    }, SAMPLE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profilesKey]);

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
  const preview = (server.catalog ?? []).find((p) => p.profile === previewProfile);
  const previewState = server.atlas_preview;
  const previewScope = previewState?.scope;
  const previewScopeId = String(previewScope?.scope_id ?? "");
  const previewMatchesIntent = Boolean(
    previewProfile &&
      previewState?.profile === previewProfile &&
      Number(previewScope?.request_id ?? -1) === desiredPreviewRequestId &&
      previewScopeId &&
      server.dataset_scopes?.atlas_preview_nodes === previewScopeId &&
      server.dataset_scopes?.atlas_preview_edges === previewScopeId &&
      atlasPreviewNodeDescriptor?.scope_id === previewScopeId &&
      atlasPreviewEdgeDescriptor?.scope_id === previewScopeId,
  );
  const currentPreviewScope = previewMatchesIntent ? previewScope : undefined;
  const activeEvidenceScope = previewProfile ? currentPreviewScope : server.atlas?.scope;
  const activeEvidenceDatasets = previewProfile
    ? `Relationship preview for ${previewProfile}`
    : "applied Atlas relationship nodes and edges";
  // Preview loads are capped at 25 edges (at most 50 nodes), below the
  // descriptor's 500-row inline page. Reading the descriptor rows ties the
  // rendered sample to its scope synchronously and avoids a one-render flash
  // of a previous same-profile hydration revision.
  const previewDescriptorIncomplete = Boolean(
    previewMatchesIntent &&
      ((atlasPreviewNodeDescriptor?.stats.row_count ?? 0) >
        (atlasPreviewNodeDescriptor?.preview_rows.length ?? 0) ||
        (atlasPreviewEdgeDescriptor?.stats.row_count ?? 0) >
          (atlasPreviewEdgeDescriptor?.preview_rows.length ?? 0)),
  );
  const previewDatasetError = previewMatchesIntent
    ? atlasPreviewNodes?.error || atlasPreviewEdges?.error || null
    : null;
  const previewProblem =
    (!previewLoading ? previewError : null) ||
    previewDatasetError ||
    (previewMatchesIntent && previewScope?.status === "failed"
      ? "The answering relation failed validation or query execution."
      : null);
  const previewReady = Boolean(
    previewMatchesIntent &&
      !previewLoading &&
      !previewDescriptorIncomplete &&
      !previewProblem,
  );
  const previewModel = useMemo(
    () =>
      buildGraphModel(
        previewReady ? atlasPreviewNodeDescriptor?.preview_rows : [],
        previewReady ? atlasPreviewEdgeDescriptor?.preview_rows : [],
        previewProfile ? [previewProfile] : [],
        { profileSelectionPhase: "applied" },
      ),
    [
      atlasPreviewNodeDescriptor?.preview_rows,
      atlasPreviewEdgeDescriptor?.preview_rows,
      previewProfile,
      previewReady,
    ],
  );
  const model = previewProfile ? previewModel : appliedModel;
  const previewBusy = Boolean(
    previewProfile && (previewLoading || previewDescriptorIncomplete),
  );
  const selectedNodeId = local.selection.nodeId;

  const beginPreview = (profile: string) => {
    setPreviewProfile(profile);
    loadPreview(profile);
  };

  const leavePreview = () => setPreviewProfile("");

  const applyPreview = () => {
    if (!preview || !previewReady) return;
    if (!activeSet.has(preview.profile)) {
      dispatch({ type: "TOGGLE_ATLAS_PROFILE", profile: preview.profile });
    }
    leavePreview();
  };

  const submitSeed = () => {
    const trimmed = seedInput.trim();
    if (!trimmed) return;
    seedInvestigate(trimmed);
  };

  return (
    <>
      {loadError ? (
        <div className="ge-load-error" role="alert">
          <span>Relationship sample failed: {loadError}</span>
          <button
            type="button"
            className="ge-btn"
            onClick={() => loadSample(appliedProfiles)}
          >
            Retry applied scope
          </button>
        </div>
      ) : null}
    <div className={`ge-atlas-body${preview ? " has-preview" : ""}`}>
      <details
        className="ge-atlas-drawer"
        open={catalogOpen}
        onToggle={(event) => setCatalogOpen(event.currentTarget.open)}
      >
        <summary>Relationship filters</summary>
        <aside className="ge-atlas-rail" aria-label="Relationship catalog and filters">
        <div className="ge-catalog-seed">
          <span className="ge-catalog-seed-label">Start from an address</span>
          <div className="ge-catalog-seed-row">
            <input
              type="text"
              value={seedInput}
              onChange={(e) => setSeedInput(e.target.value)}
              placeholder="0x… seed → Investigate"
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
            {stale ? (
              <span className="ge-pending-chip" role="status" title={`Showing applied relationships: ${appliedProfiles.join(", ") || "none"}`}>
                Applied results · {loading ? "draft pending" : "draft not applied"}
              </span>
            ) : null}
          </div>
        </div>

        {!preview ? <div className="ge-atlas-filter">
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter profiles…"
          />
          <span className="ge-catalog-count">{local.atlasProfiles.length} on</span>
          <EvidenceTrigger
            scope={activeEvidenceScope}
            datasets={activeEvidenceDatasets}
            open={evidenceOpen}
            onOpen={() => setEvidenceOpen(true)}
            buttonRef={evidenceTriggerRef}
          />
        </div> : null}

        {preview ? (
          <section className="ge-relation-preview" aria-label="Relationship preview">
            <div className="ge-relation-preview__head">
              <strong>{preview.profile}</strong>
              <div className="ge-relation-preview__actions">
                <EvidenceTrigger
                  scope={activeEvidenceScope}
                  datasets={activeEvidenceDatasets}
                  open={evidenceOpen}
                  onOpen={() => setEvidenceOpen(true)}
                  buttonRef={evidenceTriggerRef}
                />
                <span className={`ge-quality ge-quality--${preview.semantic_status}`}>
                  {preview.quality_tier || preview.semantic_status}
                </span>
                <button type="button" className="ge-btn" onClick={leavePreview}>
                  Catalog
                </button>
                <button
                  type="button"
                  className="ge-btn primary"
                  onClick={applyPreview}
                  disabled={!previewReady}
                >
                  {activeSet.has(preview.profile) ? "Applied graph" : "Add to graph"}
                </button>
              </div>
            </div>
            <details className="ge-relation-preview__definition">
              <summary>Definition and evidence</summary>
              <div className="ge-relation-preview__definition-body">
            <p>{preview.description || "No description supplied."}</p>
            <dl>
              <div>
                <dt>Shape</dt>
                <dd>{preview.source_kind} → {preview.target_kind}</dd>
              </div>
              <div>
                <dt>Relation</dt>
                <dd title={preview.model_name}>
                  {preview.model_name ? `dbt.${preview.model_name.replace(/^dbt\./, "")}` : "unknown"}
                </dd>
              </div>
              <div>
                <dt>Weight unit</dt>
                <dd title={preview.weight_unit || preview.weight_column || "edge_count"}>
                  {preview.weight_unit || relationshipWeightUnit(preview.weight_column)}
                </dd>
              </div>
              <div>
                <dt>Temporal support</dt>
                <dd>
                  {relationshipTemporalSupport(
                    preview.temporal_semantics,
                    previewState?.window_days ?? local.windowDays,
                  )}
                </dd>
              </div>
              <div>
                <dt>Freshness SLA</dt>
                <dd>{preview.freshness_sla || "not declared"}</dd>
              </div>
              <div>
                <dt>Coverage</dt>
                <dd>{preview.coverage_note || "not declared"}</dd>
              </div>
              <div>
                <dt>Preview status</dt>
                <dd>
                  {previewProblem
                    ? "failed"
                    : previewBusy || !previewMatchesIntent
                      ? "loading real sample"
                      : currentPreviewScope?.status ?? "unknown"}
                </dd>
              </div>
              <div>
                <dt>Sample coverage</dt>
                <dd>
                  {currentPreviewScope
                    ? `${coverageLabel("Edges", currentPreviewScope.coverage?.edges)}; ${coverageLabel("nodes", currentPreviewScope.coverage?.nodes)}`
                    : "awaiting preview"}
                </dd>
              </div>
              <div>
                <dt>Data horizon</dt>
                <dd>{scopeHorizon(currentPreviewScope)}</dd>
              </div>
              <div>
                <dt>Answering source</dt>
                <dd>
                  {currentPreviewScope?.sources?.length
                    ? currentPreviewScope.sources.map((source) => (
                        <span key={`${source.role}:${source.name}`}>
                          {source.name} · {source.role} · {source.status}
                          {source.fetched_at ? ` · fetched ${source.fetched_at}` : ""}
                        </span>
                      ))
                    : "awaiting source contract"}
                </dd>
              </div>
            </dl>
              </div>
            </details>
            <div className="ge-relation-preview__sample">
              <div className="ge-relation-preview__sample-head">
                <strong>Sample evidence</strong>
                <span>{previewReady ? `${previewModel.edgeRows.length} links` : "loading"}</span>
              </div>
              <div className="ge-relation-preview__sample-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Target</th>
                      <th>{preview.weight_unit || "Weight"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {previewModel.edgeRows.slice(0, 40).map((edge) => (
                      <tr key={edge.id}>
                        <td title={edge.source}>{edge.source}</td>
                        <td title={edge.target}>{edge.target}</td>
                        <td>
                          {Number.isFinite(edge.weight)
                            ? edge.weight.toLocaleString(undefined, {
                                maximumFractionDigits: 4,
                              })
                            : "unknown"}
                        </td>
                      </tr>
                    ))}
                    {previewReady && !previewModel.edgeRows.length ? (
                      <tr><td colSpan={3}>No rows in the declared scope.</td></tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>
            {previewProblem ? (
              <div className="ge-load-error" role="alert">
                <span>Preview failed: {previewProblem}</span>
                <button
                  type="button"
                  className="ge-btn"
                  onClick={() => loadPreview(preview.profile)}
                >
                  Retry preview
                </button>
              </div>
            ) : null}
          </section>
        ) : null}

        {!preview ? <div className="ge-atlas-list">
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
              No profiles match — clear the filter or relax the status toggle.
            </div>
          ) : null}
        </div> : null}
        </aside>
      </details>

      <main
        className={`ge-canvas${
          previewProfile ? (previewBusy || previewProblem ? " is-stale" : "") : stale ? " is-stale" : ""
        }`}
      >
        {preview ? (
          <div className="ge-applied-scope-chip" role="status">
            <strong>Preview only</strong> · {preview.profile}. This real sample is
            not part of the applied graph.
            <button type="button" className="ge-btn" onClick={leavePreview}>
              Back to applied graph
            </button>
          </div>
        ) : null}
        <GraphCanvas
          model={model}
          stateKey={
            previewProfile
              ? `relationships:atlas:preview:${previewProfile}`
              : "relationships:atlas"
          }
          selectedNodeId={selectedNodeId}
          selectedEdgeId={local.selection.edgeId}
          emptyHint={
            previewProfile
              ? previewProblem
                ? `Preview unavailable: ${previewProblem}`
                : previewBusy || !previewMatchesIntent
                  ? `Loading real ${previewProfile} preview…`
                  : "The answering relation returned no rows for this preview scope."
              : modelProfiles.length
                ? "Loading sample…"
                : "Choose a relationship profile to preview its evidence, or start from an address."
          }
          onSelectNode={onSelectNode}
          onSelectEdge={onSelectEdge}
          onExpandNode={(id) => seedInvestigate(id)}
          onViewClick={onClearSelection}
        />
        {selectedNodeId && model.idToIndex.has(selectedNodeId) ? (
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
    </div>
    {evidenceOpen ? (
      <EvidencePanel
        scope={activeEvidenceScope}
        datasets={activeEvidenceDatasets}
        onClose={() => setEvidenceOpen(false)}
        openerRef={evidenceTriggerRef}
      />
    ) : null}
    </>
  );
}
