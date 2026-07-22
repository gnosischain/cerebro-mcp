/** Server-reported failure for one dataset (stub-descriptor contract): a
 * failed query ships a zero-row descriptor whose provenance.coverage carries
 * `error` + `warning_codes:["query_failed"]` — the UI renders an explicit
 * error card from this instead of letting the panel vanish. */
export function datasetError(
  descriptor?: { provenance?: Record<string, unknown> },
): string {
  const coverage = descriptor?.provenance?.coverage as
    | { error?: string; warning_codes?: string[] }
    | undefined;
  if (!coverage) return "";
  if (coverage.error) return coverage.error;
  return (coverage.warning_codes ?? []).includes("query_failed")
    ? "Query failed."
    : "";
}
