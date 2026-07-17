// Bottom-left legend: node-kind visibility toggles + edge-by-profile colors.
// Presentational — hiddenKinds / open state live in GraphCanvas.

import { useMemo } from "react";
import type { GraphModel } from "../model/parseRows";
import { COLOR_BY_KIND, FALLBACK_COLOR } from "../model/palette";

interface Props {
  model: GraphModel;
  hiddenKinds: Set<string>;
  onToggleKind: (kind: string) => void;
  open: boolean;
  onToggleOpen: () => void;
}

export function Legend({ model, hiddenKinds, onToggleKind, open, onToggleOpen }: Props) {
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
      <button type="button" className="ge-legend-toggle" onClick={onToggleOpen}>
        {open ? "Legend ▾" : "Legend ▸"}
      </button>
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
              <div className="ge-legend-title">Edge profiles</div>
              {presentProfiles.map(([profile, color]) => (
                <div key={profile} className="ge-legend-item static">
                  <span
                    className="ge-legend-swatch line"
                    style={{ background: color }}
                  />
                  {profile}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
