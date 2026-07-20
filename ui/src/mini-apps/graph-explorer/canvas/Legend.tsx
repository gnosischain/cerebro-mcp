// Bottom-left legend: node-kind visibility toggles + edge-by-profile colors.
// Presentational — hiddenKinds / open state live in GraphCanvas.

import { useMemo } from "react";
import type { GraphModel } from "../model/parseRows";
import { COLOR_BY_KIND, FALLBACK_COLOR } from "../model/palette";

interface Props {
  model: GraphModel;
  hiddenKinds: Set<string>;
  onToggleKind: (kind: string) => void;
  /** Edge profiles (token / relationship) hidden from the canvas. */
  hiddenProfiles: Set<string>;
  onToggleProfile: (profile: string) => void;
  open: boolean;
  onToggleOpen: () => void;
  /** Structural graph chrome owns the toggle; the strip renders only items. */
  showToggle?: boolean;
}

export function Legend({
  model,
  hiddenKinds,
  onToggleKind,
  hiddenProfiles,
  onToggleProfile,
  open,
  onToggleOpen,
  showToggle = true,
}: Props) {
  // Kinds actually present in the current graph.
  const presentKinds = useMemo(() => {
    const set = new Set<string>();
    model.nodeRows.forEach((n) => set.add(n.kind));
    return Array.from(set).sort();
  }, [model]);

  // Profiles present (for the edge-color legend).
  const presentProfiles = useMemo(
    () =>
      Array.from(model.profileColor.entries()).sort((a, b) =>
        a[0].localeCompare(b[0]),
      ),
    [model],
  );

  return (
    <div className={`ge-legend ${open ? "open" : "collapsed"}`}>
      {showToggle ? (
        <button type="button" className="ge-legend-toggle" onClick={onToggleOpen}>
          {open ? "Legend ▾" : "Legend ▸"}
        </button>
      ) : null}
      {open && (
        <div className="ge-legend-body">
          <div className="ge-legend-section">
            <div className="ge-legend-title">Node kinds (click to toggle)</div>
            {presentKinds.map((kind) => {
              const hidden = hiddenKinds.has(kind);
              return (
                <button
                  key={kind}
                  type="button"
                  className={`ge-legend-item ${hidden ? "off" : ""}`}
                  onClick={() => onToggleKind(kind)}
                >
                  <span
                    className="ge-legend-swatch"
                    style={{ background: COLOR_BY_KIND[kind] ?? FALLBACK_COLOR }}
                  />
                  {kind}
                </button>
              );
            })}
          </div>
          {presentProfiles.length > 0 && (
            <div className="ge-legend-section">
              <div className="ge-legend-title">Edge types (click to toggle)</div>
              {presentProfiles.map(([profile, color]) => {
                const hidden = hiddenProfiles.has(profile);
                return (
                  <button
                    key={profile}
                    type="button"
                    className={`ge-legend-item ${hidden ? "off" : ""}`}
                    onClick={() => onToggleProfile(profile)}
                    title={`Show / hide ${profile} edges`}
                  >
                    <span
                      className="ge-legend-swatch line"
                      style={{ background: color }}
                    />
                    {profile}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
