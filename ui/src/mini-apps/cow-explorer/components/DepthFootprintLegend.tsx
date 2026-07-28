// Colour key for the depth footprint, rendered as HTML rather than an ECharts
// visualMap. A visualMap colours ONE ramp; this chart has two (bid and ask)
// plus an imbalance mark, and the key has to say which half of a cell is which
// side. An HTML matrix says both in a fifth of the vertical space, themes off
// CSS custom properties, and is testable in jsdom.
//
// It also retires the defect that made the previous chart undecodable: the old
// visualMap carried `formatter: () => ""`, because the cell values were
// percentile ranks and ranks have no units. Every swatch here is labelled with
// a real base-unit range.

import type { DepthScale } from "../model/depthFootprintScale";
import { rampFor } from "../model/depthFootprintScale";

export interface DepthFootprintLegendProps {
  scale: DepthScale;
  baseSymbol: string;
  isDark: boolean;
}

export function DepthFootprintLegend({ scale, baseSymbol, isDark }: DepthFootprintLegendProps) {
  const labels = scale.labels;
  const askRamp = rampFor("ask", isDark);
  const bidRamp = rampFor("bid", isDark);
  const row = (name: string, half: string, ramp: ReturnType<typeof rampFor>) => (
    <tr>
      <th scope="row">
        {name}
        <small>{half}</small>
      </th>
      {labels.map((label, i) => (
        <td key={label}>
          <i
            style={{ background: ramp[Math.min(i, ramp.length - 1)].fill }}
            aria-label={`${name} ${label} ${baseSymbol}, ${scale.counts[i] ?? 0} cells`}
          />
        </td>
      ))}
    </tr>
  );
  return (
    <div className="cow-fp-legend">
      <table aria-label="Depth footprint colour key">
        <thead>
          <tr>
            <th className="cow-fp-legend__unit" scope="col">
              resting depth ({baseSymbol})
            </th>
            {labels.map((label) => (
              <th key={label} scope="col">{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {row("bids", "left half", bidRamp)}
          {row("asks", "right half", askRamp)}
        </tbody>
      </table>
      <span className="cow-fp-legend__note">
        <i className="cow-fp-legend__outline" aria-hidden="true" /> 3:1 imbalance
        <i className="cow-fp-legend__dash" aria-hidden="true" /> market price
      </span>
    </div>
  );
}
