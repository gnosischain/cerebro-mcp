// Tiny inline sparkline — a PLAIN SVG polyline, deliberately not ECharts:
// KPI tiles render up to ~6 of these per row and a full chart runtime per
// tile would be absurd. Decorative only (aria-hidden); the tile's value and
// the real charts below carry the information.

export interface SparkLineProps {
  values: number[];
  width?: number;
  height?: number;
  /** Stroke color; defaults to currentColor so the tile's text color rules. */
  stroke?: string;
}

/** Pure polyline-point math (exported for unit tests): maps `values` onto a
 * `width`x`height` viewBox with 1px vertical padding. A flat series (or a
 * single point) renders as a midline instead of collapsing to NaN. Non-finite
 * entries are skipped rather than poisoning the whole line. */
export function sparkPoints(values: number[], width: number, height: number): string {
  const finite = values.filter((value) => Number.isFinite(value));
  if (finite.length === 0) return "";
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const span = max - min;
  const pad = 1;
  const usable = height - pad * 2;
  const points: string[] = [];
  const n = values.length;
  let index = 0;
  for (const value of values) {
    if (!Number.isFinite(value)) {
      index += 1;
      continue;
    }
    const x = n <= 1 ? width / 2 : (index / (n - 1)) * width;
    const y = span === 0 ? height / 2 : pad + (1 - (value - min) / span) * usable;
    points.push(`${round2(x)},${round2(y)}`);
    index += 1;
  }
  // A single usable point still needs a visible mark — draw a short midline.
  if (points.length === 1) {
    const [, y] = points[0].split(",");
    return `${round2(width * 0.4)},${y} ${round2(width * 0.6)},${y}`;
  }
  return points.join(" ");
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

export function SparkLine({ values, width = 120, height = 28, stroke = "currentColor" }: SparkLineProps) {
  const points = sparkPoints(values, width, height);
  if (!points) return null;
  return (
    <svg
      className="cow-spark"
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
