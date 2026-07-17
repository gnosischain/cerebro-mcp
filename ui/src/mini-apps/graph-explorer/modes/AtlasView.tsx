// ATLAS mode: browse the semantic graph catalog as a sector-grouped rail of
// multi-select profiles; the canvas shows a REPLACE-semantics sample union
// (atlas_nodes/atlas_edges) over exactly the checked profiles. Clicking a
// node offers "Investigate →" which seeds INVESTIGATE mode from that node.
// An address input at the top seeds investigate directly.

import { useEffect, useMemo, useRef, useState } from "react";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import { GraphCanvas } from "../canvas/GraphCanvas";
import { buildGraphModel, shortId } from "../model/parseRows";
import { SECTOR_COLOR, groupProfilesBySector } from "../model/sectors";
import type { GraphAction, GraphLocalState } from "../state/graphReducer";
import type { GraphExplorerViewState } from "../types";

const SAMPLE_DEBOUNCE_MS = 400;

interface Props {
  server: GraphExplorerViewState;
  local: GraphLocalState;
  dispatch: (action: GraphAction) => void;
  atlasNodes: HydratedDataset | undefined;
  atlasEdges: HydratedDataset | undefined;
  /** Fires load_graph_atlas_sample (REPLACE semantics; empty list clears). */
  loadSample: (profiles: string[]) => void;
  /** Seeds INVESTIGATE mode from an address / node id. */
  seedInvestigate: (nodeId: string) => void;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onClearSelection: () => void;
}

export function AtlasView({
  server,
  local,
  dispatch,
  atlasNodes,
  atlasEdges,
  loadSample,
  seedInvestigate,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
}: Props) {
  const [seedInput, setSeedInput] = useState("");
  const [filter, setFilter] = useState("");

  const model = useMemo(
    () => buildGraphModel(atlasNodes?.rows, atlasEdges?.rows, local.atlasProfiles),
    [atlasNodes?.rows, atlasEdges?.rows, local.atlasProfiles],
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
  const selectedNodeId = local.selection.nodeId;

  const submitSeed = () => {
    const trimmed = seedInput.trim();
    if (!trimmed) return;
    seedInvestigate(trimmed);
  };

  return (
    <div className="ge-atlas-body">
      <aside className="ge-atlas-rail">
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
          </div>
        </div>

        <div className="ge-atlas-filter">
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter profiles…"
          />
          <span className="ge-catalog-count">{local.atlasProfiles.length} on</span>
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
                <label
                  key={p.profile}
                  className="ge-atlas-item"
                  title={`${p.description || p.profile}\n${p.source_kind} → ${p.target_kind}`}
                >
                  <input
                    type="checkbox"
                    checked={activeSet.has(p.profile)}
                    onChange={() =>
                      dispatch({ type: "TOGGLE_ATLAS_PROFILE", profile: p.profile })
                    }
                  />
                  <span
                    className={`ge-dot ${p.semantic_status === "approved" ? "ge-dot-approved" : "ge-dot-candidate"}`}
                    aria-hidden
                  />
                  <span className="ge-atlas-item-name">{p.profile}</span>
                </label>
              ))}
            </div>
          ))}
          {!sectors.length ? (
            <div className="ge-catalog-empty">
              No profiles match — clear the filter or relax the status toggle.
            </div>
          ) : null}
        </div>
      </aside>

      <main className="ge-canvas">
        <GraphCanvas
          model={model}
          selectedNodeId={selectedNodeId}
          layout={local.layout}
          emptyHint={
            local.atlasProfiles.length
              ? "Loading sample…"
              : "Check profiles on the left to sample their graphs — or seed an address above."
          }
          onSelectNode={onSelectNode}
          onSelectEdge={onSelectEdge}
          onExpandNode={(id) => seedInvestigate(id)}
          onViewClick={onClearSelection}
        />
        {selectedNodeId ? (
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
  );
}
