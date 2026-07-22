// Governance-named wrapper over the shared CSV exporter. Filenames follow
// `governance_<scope>_<datasetKey>_<timestamp>.csv`; the timestamp is passed
// in by the caller so tests stay deterministic.

import { buildCsv, downloadCsv } from "../../shared/csvExport";

function slug(text: string): string {
  return text.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "all";
}

export function governanceCsvFilename(
  scope: string,
  datasetKey: string,
  timestamp: string,
  truncated = false,
): string {
  const suffix = truncated ? "_truncated" : "";
  return `governance_${slug(scope)}_${slug(datasetKey)}_${slug(timestamp)}${suffix}.csv`;
}

export function exportGovernanceCsv(args: {
  scope: string;
  datasetKey: string;
  timestamp: string;
  columns: string[];
  rows: unknown[][];
  columnTypes?: Array<string | undefined>;
  truncated?: boolean;
}): string {
  const filename = governanceCsvFilename(
    args.scope, args.datasetKey, args.timestamp, args.truncated ?? false,
  );
  downloadCsv(filename, buildCsv(args.columns, args.rows, args.columnTypes));
  return filename;
}
