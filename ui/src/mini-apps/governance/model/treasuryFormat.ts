// Treasury number and date formatting. Every formatter takes `number | null`
// and renders null as a dash: `Number(null) === 0`, and that coercion has
// already shipped a "$0 NAV" once. Unknown is never zero.

export const DASH = "—";

/** Token units at the precision the magnitude deserves. Dust never rounds to
 * "0": a 1e-9 balance is a held position, and printing it as zero makes the
 * stronger claim that the treasury exited it. */
export function fmtUnits(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs >= 1000) return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (abs >= 1) return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (abs >= 1e-6) return value.toLocaleString("en-US", { maximumSignificantDigits: 3 });
  return value.toExponential(2);
}

/** Compact units for tiles and chips ("1.25M"). */
export function fmtUnitsCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e4) return `${sign}${(abs / 1e3).toFixed(1)}K`;
  return fmtUnits(value);
}

/** USD. Compact by default ("$104.90M"); `full` prints whole dollars. A
 * positive value too small to show is "< $0.01", never "$0.00". */
export function fmtUsd(value: number | null | undefined, opts: { full?: boolean } = {}): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs > 0 && abs < 0.01) return `${sign}< $0.01`;
  if (opts.full) {
    const digits = abs >= 1000 ? 0 : 2;
    return `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

/** A price: small prices keep their significant digits. */
export function fmtPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const abs = Math.abs(value);
  if (abs >= 1000) return `$${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  if (abs >= 1) return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (abs === 0) return "$0";
  return `$${value.toLocaleString("en-US", { maximumSignificantDigits: 4 })}`;
}

/** Counts. */
export function fmtCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/** A 0..1 share as a percentage. Dust shares say "< 0.1%" rather than "0.0%",
 * which would read as "none". */
export function fmtShare(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  if (value > 0 && value < 0.001) return "< 0.1%";
  return `${(value * 100).toFixed(1)}%`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** 'YYYY-MM-01' -> 'YYYY-MM' (the canonical month label; sorts and reads the
 * same in every locale). */
export function fmtMonth(bucket: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(bucket ?? "");
  return match ? `${match[1]}-${match[2]}` : bucket || DASH;
}

/** 'YYYY-MM-01' -> 'Jul 2026', for prose. */
export function fmtMonthLong(bucket: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(bucket ?? "");
  if (!match) return bucket || DASH;
  const month = Number(match[2]);
  return month >= 1 && month <= 12 ? `${MONTHS[month - 1]} ${match[1]}` : bucket;
}

/** An ISO instant as 'YYYY-MM-DD HH:MM UTC' — "UTC" only when the string says
 * so; labelling a naive stamp UTC would be an assertion, not a reading. */
export function fmtStamp(value: string): string {
  const text = String(value ?? "").trim();
  const parts = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(text);
  if (!parts) return text;
  return `${parts[1]} ${parts[2]}${text.endsWith("Z") ? " UTC" : ""}`;
}
