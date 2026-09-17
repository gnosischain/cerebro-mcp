// Colour key for the liquidity heatmap, rendered as HTML rather than an
// ECharts visualMap so every swatch is labelled with a real L range (the
// classes come from the shared depth-scale ladder) and the current-tick path
// is explained next to it.

import type { DepthScale } from "../../cow-explorer/model/depthFootprintScale";
import { liquidityClassLabels, liquidityRamp } from "../model/liquidityScale";

export function LiquidityLegend({ scale, isDark }: { scale: DepthScale; isDark: boolean }) {
  const ramp = liquidityRamp(isDark);
  const labels = liquidityClassLabels(scale);
  return (
    <div className="plx-legend">
      <table aria-label="Liquidity colour key">
        <thead>
          <tr>
            <th className="plx-legend__unit" scope="col">active liquidity (L)</th>
            {labels.map((label) => (
              <th key={label} scope="col">{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">cells</th>
            {labels.map((label, index) => (
              <td key={label}>
                <i
                  style={{ background: ramp[Math.min(index, ramp.length - 1)].fill }}
                  aria-label={`L ${label}, ${scale.counts[index] ?? 0} cells`}
                />
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      <span className="plx-legend__note">
        <i className="plx-legend__dash" aria-hidden="true" /> current tick
      </span>
    </div>
  );
}
