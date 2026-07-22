// Spec-compliant CSV export shared by mini-apps.
//
// Rules (RFC 4180 + spreadsheet-safety hardening):
//   - cells containing `"`, `,`, CR or LF are quoted; embedded quotes doubled;
//   - rows joined with CRLF; the file starts with a UTF-8 BOM so Excel decodes
//     UTF-8 correctly;
//   - formula neutralization: STRING cells whose first character is `=` `+`
//     `-` `@` TAB (0x09) or CR (0x0D) get a leading single-quote AND forced
//     quoting so spreadsheets never evaluate them. Numeric cells are emitted
//     as plain numbers untouched — a negative number is not a formula;
//   - columns typed DateTime/Date or String-like (addresses, ids) can be
//     force-quoted via `columnTypes` so spreadsheets keep them as text.

const FORMULA_LEADS = new Set(["=", "+", "-", "@", "\t", "\r"]);

function needsQuoting(text: string): boolean {
  return (
    text.includes('"') || text.includes(",") || text.includes("\r") || text.includes("\n")
  );
}

function quote(text: string): string {
  return `"${text.replace(/"/g, '""')}"`;
}

/** True for ClickHouse-ish types a spreadsheet should keep as text. */
function isTextForcedType(type: string | undefined): boolean {
  if (!type) return false;
  return /date|string|fixedstring|uuid|enum/i.test(type);
}

/** Serialize one cell. `forceQuote` additionally wraps the cell in quotes even
 * when RFC 4180 would not require it (used for DateTime/address columns). */
export function csvCell(value: unknown, forceQuote = false): string {
  if (value === null || value === undefined) return forceQuote ? '""' : "";
  if (typeof value === "number" || typeof value === "bigint") {
    // Plain numbers untouched — negative numbers are NOT formulas.
    const text = String(value);
    return forceQuote ? quote(text) : text;
  }
  if (typeof value === "boolean") {
    const text = value ? "true" : "false";
    return forceQuote ? quote(text) : text;
  }
  let text = typeof value === "string" ? value : JSON.stringify(value) ?? "";
  let mustQuote = forceQuote || needsQuoting(text);
  if (text.length > 0 && FORMULA_LEADS.has(text[0])) {
    text = `'${text}`;
    mustQuote = true;
  }
  if (!mustQuote) return text;
  return quote(text);
}

/** Build a CSV document (WITHOUT the BOM — `downloadCsv` prepends it).
 * `columnTypes`, when provided, is positional with `columns`; DateTime/String
 * -like columns are force-quoted so spreadsheets keep them as text. */
export function buildCsv(
  columns: string[],
  rows: unknown[][],
  columnTypes?: Array<string | undefined>,
): string {
  const forced = columns.map((_, i) => isTextForcedType(columnTypes?.[i]));
  const lines: string[] = [];
  lines.push(columns.map((name) => csvCell(name)).join(","));
  for (const row of rows) {
    lines.push(columns.map((_, i) => csvCell(row[i], forced[i])).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

const UTF8_BOM = "\uFEFF";

/** Trigger a browser download of `csv` (BOM prepended). No-op outside a DOM
 * environment (jsdom-safe guard on `document`). */
export function downloadCsv(filename: string, csv: string): void {
  if (typeof document === "undefined") return;
  const blob = new Blob([UTF8_BOM + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
