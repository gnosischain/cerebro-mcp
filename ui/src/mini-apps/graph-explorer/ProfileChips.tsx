// Investigate-mode edge-type toggles, grouped by sector. Rendered inside a
// compact POPOVER (EdgeTypesMenu) instead of the old full-width chip strip —
// the strip cost a whole header row and scrolled awkwardly.
// ADDING a profile is a data operation (the parent refetches the seed with
// the union); REMOVING is a pure client-side filter (reducer
// investigateProfiles) — the parent decides which path applies.

import { useEffect, useRef, useState } from "react";
import type { ProfileCard, StatusFilter } from "./types";
import { groupProfilesBySector } from "./model/sectors";

interface Props {
  catalog: ProfileCard[];
  activeProfiles: string[];
  statusFilter: StatusFilter;
  /** Toggle a single profile. `adding` = it was not active before. */
  onToggle: (profileId: string, adding: boolean) => void;
  /** Toggle a whole sector group to `on`. */
  onToggleGroup: (profileIds: string[], on: boolean) => void;
}

export function ProfileChips({
  catalog,
  activeProfiles,
  statusFilter,
  onToggle,
  onToggleGroup,
}: Props) {
  const activeSet = new Set(activeProfiles);
  const sectors = groupProfilesBySector(catalog);

  return (
    <nav className="ge-chip-groups" aria-label="Edge types">
      {sectors.map(([sector, profiles]) => {
        const visible = profiles.filter((p) =>
          statusFilter === "all" ? true : p.semantic_status === statusFilter,
        );
        if (!visible.length) return null;
        const ids = visible.map((p) => p.profile);
        const allOn = ids.every((id) => activeSet.has(id));
        return (
          <div key={sector} className="ge-chip-group">
            <button
              type="button"
              className={`ge-chip-group-label ${allOn ? "all-on" : ""}`}
              onClick={() => onToggleGroup(ids, !allOn)}
              title={`Toggle all ${visible.length} ${sector} profile(s)`}
            >
              {sector}
            </button>
            {visible.map((p) => {
              const active = activeSet.has(p.profile);
              return (
                <button
                  key={p.profile}
                  type="button"
                  className={`ge-chip ${active ? "active" : ""} ${p.semantic_status}`}
                  onClick={() => onToggle(p.profile, !active)}
                  title={`${p.profile} — ${p.description || ""}\n${p.source_kind} → ${p.target_kind}`}
                >
                  <span className="ge-chip-dot" aria-hidden />
                  <span className="ge-chip-name">{p.profile}</span>
                </button>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
}

const STATUS_OPTIONS: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "approved", label: "Approved" },
  { value: "candidate", label: "Candidate" },
];

/** Compact "Edge types 7/12" button + dropdown panel. Hosts the sector-grouped
 * profile toggles AND the semantic-status filter (which only affects what the
 * panel lists / the canvas shows). Closes on outside click or Escape. */
export function EdgeTypesMenu({
  catalog,
  activeProfiles,
  statusFilter,
  onStatusFilterChange,
  onToggle,
  onToggleGroup,
}: Props & { onStatusFilterChange: (filter: StatusFilter) => void }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="ge-etypes" ref={wrapRef}>
      <button
        type="button"
        className={`ge-btn ge-etypes-btn ${open ? "active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="Choose which relationship types are on the graph"
      >
        Edge types
        <span className="ge-etypes-count">
          {activeProfiles.length}/{catalog.length}
        </span>
        <span aria-hidden>▾</span>
      </button>
      {open ? (
        <div className="ge-etypes-panel" role="group" aria-label="Edge types">
          <div className="ge-etypes-status" role="group" aria-label="Semantic status">
            <span className="ge-etypes-status-label">Show</span>
            {STATUS_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`ge-graph-btn ${statusFilter === o.value ? "active" : ""}`}
                onClick={() => onStatusFilterChange(o.value)}
                aria-pressed={statusFilter === o.value}
              >
                {o.label}
              </button>
            ))}
          </div>
          <ProfileChips
            catalog={catalog}
            activeProfiles={activeProfiles}
            statusFilter={statusFilter}
            onToggle={onToggle}
            onToggleGroup={onToggleGroup}
          />
        </div>
      ) : null}
    </div>
  );
}
