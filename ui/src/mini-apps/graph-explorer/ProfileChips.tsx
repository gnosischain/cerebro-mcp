// Investigate-mode chip strip: per-profile toggles grouped by sector.
// ADDING a profile is a data operation (the parent refetches the seed with
// the union); REMOVING is a pure client-side filter (reducer
// investigateProfiles) — the parent decides which path applies.

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
    <nav className="ge-chip-strip" aria-label="Edge types">
      <span
        className="ge-chip-strip-caption"
        title="Click a chip to add or remove that relationship type from the graph. The bold sector label toggles the whole group."
      >
        Edge types
      </span>
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
