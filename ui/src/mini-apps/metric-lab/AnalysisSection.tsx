// Analysis tab: summary statistics + correlation matrix with a
// click-a-cell → scatter drill-in (OLS trendline + r/ρ/R²/n caption).

import { useMemo, useState } from "react";
import { CollapsibleSection } from "../shared/CollapsibleSection";
import { SegmentedControl } from "../shared/SegmentedControl";
import { WarningBanner } from "../shared/WarningBanner";
import { ChartCard } from "../../components/ChartCard";
import type { EChartsOption } from "echarts";
import {
  computeColumnStats,
  corrColor,
  correlationMatrix,
  fmtNum,
  olsFit,
  pairwiseVectors,
  pearson,
  spearman,
} from "./analysis";
import { buildXYScatterOption } from "./chartOptions";
import type { CorrMethod } from "./types";

interface AnalysisSectionProps {
  rows: unknown[][];
  columns: string[];
  estimates: boolean;
  sampleSourceRows: number | null;
  truncated: boolean;
  unvalidatedMetrics: string[];
}

export function AnalysisSection({
  rows,
  columns,
  estimates,
  sampleSourceRows,
  truncated,
  unvalidatedMetrics,
}: AnalysisSectionProps) {
  const [method, setMethod] = useState<CorrMethod>("pearson");
  const [drill, setDrill] = useState<{ x: string; y: string } | null>(null);

  const stats = useMemo(() => computeColumnStats(rows, columns), [rows, columns]);
  const corr = useMemo(
    () => correlationMatrix(rows, columns, method),
    [rows, columns, method],
  );

  const caveats: string[] = [];
  if (estimates) {
    caveats.push(
      `Statistics computed on a random sample of ${rows.length.toLocaleString()}${
        sampleSourceRows ? ` of ${sampleSourceRows.toLocaleString()} source rows` : " rows"
      } — treat correlations as estimates.`,
    );
  }
  if (truncated) {
    caveats.push(
      "Row hydration was capped at 5,000 rows — statistics cover the loaded subset.",
    );
  }
  if (unvalidatedMetrics.length > 0) {
    caveats.push(
      `Unvalidated (candidate-tier) metrics loaded: ${unvalidatedMetrics.join(", ")} — definitions not yet approved, treat results as estimates.`,
    );
  }

  const drillOption: EChartsOption | null = useMemo(() => {
    if (!drill) return null;
    return buildXYScatterOption(rows, columns, drill.x, drill.y, true);
  }, [drill, rows, columns]);

  const drillStats = useMemo(() => {
    if (!drill) return null;
    const { xs, ys } = pairwiseVectors(rows, columns, drill.x, drill.y);
    if (xs.length < 2) return null;
    const fit = olsFit(xs, ys);
    return {
      r: pearson(xs, ys),
      rho: spearman(xs, ys),
      r2: fit.r2,
      n: xs.length,
    };
  }, [drill, rows, columns]);

  // NOTE: after all hooks — React hooks must run unconditionally.
  if (stats.length === 0) {
    return (
      <div className="mlab-analysis">
        <div className="mlab-empty">
          No numeric columns detected — analysis tools are unavailable for this dataset.
        </div>
      </div>
    );
  }

  return (
    <div className="mlab-analysis">
      {caveats.length > 0 && <WarningBanner warnings={caveats} />}

      {corr.cols.length >= 2 && (
        <>
          <div className="mlab-analysis-head">
            <h3 className="mlab-subhead">
              Correlations (n={corr.n.toLocaleString()})
            </h3>
            <SegmentedControl<CorrMethod>
              ariaLabel="Correlation method"
              size="sm"
              value={method}
              onChange={setMethod}
              options={[
                { value: "pearson", label: "Pearson" },
                { value: "spearman", label: "Spearman" },
              ]}
            />
            <span className="mlab-hint">Click a cell to open the pair as a scatter with a fitted line.</span>
          </div>
          <div className="mini-app-table-wrap">
            <table className="mlab-corr">
              <thead>
                <tr>
                  <th className="mlab-corr-rowlabel"></th>
                  {corr.cols.map((c, j) => (
                    // Numbered columns — truncated names were ambiguous
                    // ("EXECUTION_TX…" ×3). Numbers map to the row labels.
                    <th key={c} title={c} className="mlab-corr-colnum">
                      {j + 1}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {corr.cols.map((rowCol, i) => (
                  <tr key={rowCol}>
                    <td className="mlab-corr-rowlabel" title={rowCol}>
                      <span className="mlab-corr-num">{i + 1}</span>
                      {rowCol.length > 28 ? rowCol.slice(0, 26) + "…" : rowCol}
                    </td>
                    {corr.matrix[i].map((r, j) => (
                      <td
                        key={j}
                        className={`mlab-corr-cell${i !== j ? " is-clickable" : ""}`}
                        style={{ background: corrColor(r) }}
                        title={`${rowCol} × ${corr.cols[j]}: ${method === "pearson" ? "r" : "ρ"} = ${r.toFixed(4)} — click to plot`}
                        onClick={() => {
                          if (i !== j) setDrill({ x: corr.cols[j], y: rowCol });
                        }}
                      >
                        {i === j ? "—" : r.toFixed(2)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {drill && drillOption && (
        <div className="mlab-drill">
          <div className="mlab-drill-head">
            <h3 className="mlab-subhead">
              {drill.y} vs {drill.x}
            </h3>
            {drillStats && (
              <span className="mlab-drill-stats">
                r = {drillStats.r.toFixed(3)} · ρ = {drillStats.rho.toFixed(3)} · R² ={" "}
                {drillStats.r2.toFixed(3)} · n = {drillStats.n.toLocaleString()}
              </span>
            )}
            <button type="button" className="mlab-toggle" onClick={() => setDrill(null)}>
              close
            </button>
          </div>
          <ChartCard
            chartId="metric-lab-corr"
            spec={{ ...drillOption, _cerebro_height: "380px" } as EChartsOption}
            hideId
          />
        </div>
      )}

      <CollapsibleSection
        title={`Summary statistics (${stats.length} column${stats.length === 1 ? "" : "s"})`}
        tone="subtle"
      >
        <div className="mini-app-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Column</th>
                <th>Count</th>
                <th>Nulls</th>
                <th>Min</th>
                <th>Median</th>
                <th>Mean</th>
                <th>Max</th>
                <th>Stddev</th>
              </tr>
            </thead>
            <tbody>
              {stats.map((s) => (
                <tr key={s.name}>
                  <td>{s.name}</td>
                  <td>{s.count.toLocaleString()}</td>
                  <td>{s.nulls.toLocaleString()}</td>
                  <td>{fmtNum(s.min)}</td>
                  <td>{fmtNum(s.median)}</td>
                  <td>{fmtNum(s.mean)}</td>
                  <td>{fmtNum(s.max)}</td>
                  <td>{fmtNum(s.stddev)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CollapsibleSection>
    </div>
  );
}
