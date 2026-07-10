// Pure statistics helpers for the Metric Lab analysis panel.
// No React / ECharts imports — unit-testable in isolation.

export interface ColumnStats {
  name: string;
  count: number;
  nulls: number;
  min: number;
  max: number;
  mean: number;
  median: number;
  stddev: number;
}

export interface CorrelationMatrix {
  cols: string[];
  matrix: number[][];
  /** Listwise-complete sample size the matrix was computed over. */
  n: number;
}

export interface FitResult {
  slope: number;
  intercept: number;
  r2: number;
}

/** A column is numeric if every non-null value coerces to a finite number
 * (sampling the first 10 informative values for speed). */
export function isNumericColumn(rows: unknown[][], idx: number): boolean {
  let seen = 0;
  for (const row of rows) {
    const v = row[idx];
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "number") {
      seen++;
    } else if (typeof v === "string" && !isNaN(Number(v))) {
      seen++;
    } else {
      return false;
    }
    if (seen >= 10) return true;
  }
  return seen > 0;
}

export function numericColumnIndexes(rows: unknown[][], columns: string[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < columns.length; i++) {
    if (isNumericColumn(rows, i)) out.push(i);
  }
  return out;
}

export function computeColumnStats(rows: unknown[][], columns: string[]): ColumnStats[] {
  const out: ColumnStats[] = [];
  for (let idx = 0; idx < columns.length; idx++) {
    if (!isNumericColumn(rows, idx)) continue;
    const vals: number[] = [];
    let nulls = 0;
    for (const row of rows) {
      const v = row[idx];
      if (v === null || v === undefined || v === "") {
        nulls++;
        continue;
      }
      const n = Number(v);
      if (Number.isFinite(n)) vals.push(n);
      else nulls++;
    }
    if (vals.length === 0) continue;
    const sorted = [...vals].sort((a, b) => a - b);
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const variance = vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length;
    const mid = Math.floor(sorted.length / 2);
    const median =
      sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
    out.push({
      name: columns[idx],
      count: vals.length,
      nulls,
      min: sorted[0],
      max: sorted[sorted.length - 1],
      mean,
      median,
      stddev: Math.sqrt(variance),
    });
  }
  return out;
}

/** Pearson correlation over two aligned numeric vectors. NaN-safe: returns 0
 * for degenerate (zero-variance or empty) inputs. */
export function pearson(xs: number[], ys: number[]): number {
  const n = Math.min(xs.length, ys.length);
  if (n < 2) return 0;
  let sx = 0;
  let sy = 0;
  for (let i = 0; i < n; i++) {
    sx += xs[i];
    sy += ys[i];
  }
  const mx = sx / n;
  const my = sy / n;
  let cov = 0;
  let vx = 0;
  let vy = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - mx;
    const dy = ys[i] - my;
    cov += dx * dy;
    vx += dx * dx;
    vy += dy * dy;
  }
  const denom = Math.sqrt(vx * vy);
  if (denom === 0) return 0;
  return cov / denom;
}

/** Rank transform with average ranks for ties (Spearman's convention). */
function ranks(values: number[]): number[] {
  const idx = values.map((_, i) => i).sort((a, b) => values[a] - values[b]);
  const out = new Array<number>(values.length);
  let i = 0;
  while (i < idx.length) {
    let j = i;
    while (j + 1 < idx.length && values[idx[j + 1]] === values[idx[i]]) j++;
    const avgRank = (i + j) / 2 + 1; // ranks are 1-based
    for (let k = i; k <= j; k++) out[idx[k]] = avgRank;
    i = j + 1;
  }
  return out;
}

/** Spearman rank correlation = Pearson over rank-transformed vectors. */
export function spearman(xs: number[], ys: number[]): number {
  const n = Math.min(xs.length, ys.length);
  if (n < 2) return 0;
  return pearson(ranks(xs.slice(0, n)), ranks(ys.slice(0, n)));
}

/** Ordinary least squares fit y = slope*x + intercept, with R². */
export function olsFit(xs: number[], ys: number[]): FitResult {
  const n = Math.min(xs.length, ys.length);
  if (n < 2) return { slope: 0, intercept: 0, r2: 0 };
  let sx = 0;
  let sy = 0;
  for (let i = 0; i < n; i++) {
    sx += xs[i];
    sy += ys[i];
  }
  const mx = sx / n;
  const my = sy / n;
  let sxy = 0;
  let sxx = 0;
  for (let i = 0; i < n; i++) {
    sxy += (xs[i] - mx) * (ys[i] - my);
    sxx += (xs[i] - mx) ** 2;
  }
  if (sxx === 0) return { slope: 0, intercept: my, r2: 0 };
  const slope = sxy / sxx;
  const intercept = my - slope * mx;
  const r = pearson(xs.slice(0, n), ys.slice(0, n));
  return { slope, intercept, r2: r * r };
}

/** Pairwise-complete numeric vectors for two columns — rows where BOTH parse
 * as finite numbers. Used by the scatter drill-in. */
export function pairwiseVectors(
  rows: unknown[][],
  columns: string[],
  xCol: string,
  yCol: string,
): { xs: number[]; ys: number[] } {
  const xi = columns.indexOf(xCol);
  const yi = columns.indexOf(yCol);
  const xs: number[] = [];
  const ys: number[] = [];
  if (xi < 0 || yi < 0) return { xs, ys };
  for (const row of rows) {
    const x = Number(row[xi]);
    const y = Number(row[yi]);
    if (Number.isFinite(x) && Number.isFinite(y)) {
      xs.push(x);
      ys.push(y);
    }
  }
  return { xs, ys };
}

/** Correlation matrix over every numeric column, listwise-complete (a row
 * enters only when ALL numeric columns parse). */
export function correlationMatrix(
  rows: unknown[][],
  columns: string[],
  method: "pearson" | "spearman" = "pearson",
): CorrelationMatrix {
  const numericIdx = numericColumnIndexes(rows, columns);
  const vectors: number[][] = numericIdx.map(() => []);
  for (const row of rows) {
    let allValid = true;
    const tmp: number[] = [];
    for (const idx of numericIdx) {
      const n = Number(row[idx]);
      if (!Number.isFinite(n)) {
        allValid = false;
        break;
      }
      tmp.push(n);
    }
    if (!allValid) continue;
    for (let k = 0; k < tmp.length; k++) vectors[k].push(tmp[k]);
  }
  const corr = method === "spearman" ? spearman : pearson;
  const n = numericIdx.length;
  const matrix: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i; j < n; j++) {
      if (i === j) {
        matrix[i][j] = 1;
        continue;
      }
      const r = corr(vectors[i], vectors[j]);
      matrix[i][j] = r;
      matrix[j][i] = r;
    }
  }
  return {
    cols: numericIdx.map((i) => columns[i]),
    matrix,
    n: vectors[0]?.length ?? 0,
  };
}

export function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(2) + "k";
  if (Math.abs(n) < 0.01 && n !== 0) return n.toExponential(2);
  return n.toFixed(2);
}

/** Diverging cell background for a correlation value: red (neg) → blue (pos). */
export function corrColor(r: number): string {
  const a = Math.min(1, Math.abs(r));
  if (r >= 0) return `rgba(99, 179, 237, ${a * 0.55})`;
  return `rgba(252, 129, 129, ${a * 0.55})`;
}
