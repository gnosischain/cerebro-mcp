import type { DatasetDescriptor } from "../shared/miniAppTypes";
import type { HydratedDataset } from "../shared/useHydratedDatasets";
import type { GraphExplorerViewState } from "./types";

export interface CaseExportInput {
  viewId: string;
  server: GraphExplorerViewState;
  datasets: Record<string, HydratedDataset | undefined>;
  descriptors: Record<string, DatasetDescriptor> | undefined;
  analyst?: string;
  now?: Date;
}

export interface CaseExportFile {
  path: string;
  content: string | Uint8Array;
}

function json(value: unknown): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function safeName(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "item";
}

function csvCell(value: unknown): string {
  if (value == null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function csv(dataset: HydratedDataset): string {
  const lines = [dataset.columns.map(csvCell).join(",")];
  for (const row of dataset.rows) lines.push(row.map(csvCell).join(","));
  return `${lines.join("\r\n")}\r\n`;
}

function scopes(server: GraphExplorerViewState): Record<string, unknown>[] {
  const values = [
    server.atlas?.scope,
    server.atlas_preview?.scope,
    server.investigate?.scope,
    server.flows?.scope,
    server.timeline?.forensic_scope,
    server.transactions?.scope,
    server.focus_scope,
  ];
  const byId = new Map<string, Record<string, unknown>>();
  for (const value of values) {
    if (!value?.scope_id) continue;
    byId.set(value.scope_id, value as unknown as Record<string, unknown>);
  }
  return [...byId.values()];
}

export function buildCaseExportFiles(input: CaseExportInput): CaseExportFile[] {
  const createdAt = (input.now ?? new Date()).toISOString();
  const caseId = `graph-explorer-${createdAt.replace(/[:.]/g, "-")}`;
  const diagnostics =
    typeof window === "undefined" ? undefined : window.__MINI_APP_DIAGNOSTICS__;
  const evidenceScopes = scopes(input.server);
  const files: CaseExportFile[] = [];
  const limitations = new Set<string>();
  for (const scope of evidenceScopes) {
    for (const value of [...((scope.residuals as string[]) ?? []), ...((scope.warnings as string[]) ?? [])]) {
      if (value) limitations.add(String(value));
    }
    const scopeId = String(scope.scope_id ?? "scope");
    files.push({
      path: `forensic-scopes/${safeName(scopeId)}.json`,
      content: json(scope),
    });
  }

  const exportedDatasets: string[] = [];
  for (const [key, dataset] of Object.entries(input.datasets).sort(([a], [b]) => a.localeCompare(b))) {
    if (!dataset) continue;
    exportedDatasets.push(key);
    files.push({ path: `datasets/${safeName(key)}.csv`, content: csv(dataset) });
    const descriptor = input.descriptors?.[key];
    if (descriptor?.sql) {
      files.push({ path: `queries/${safeName(key)}.sql`, content: `${descriptor.sql.trim()}\n` });
    }
  }

  const transactionScope = input.server.transactions?.scope;
  const rawReceiptDataset = input.datasets.tx_raw_receipts;
  const rawLogDataset = input.datasets.tx_raw_logs;
  if (rawReceiptDataset) {
    const hashIndex = rawReceiptDataset.columns.indexOf("tx_hash");
    const receiptIndex = rawReceiptDataset.columns.indexOf("receipt_json");
    for (const row of rawReceiptDataset.rows) {
      const hash = String(row[hashIndex] ?? "receipt");
      const raw = String(row[receiptIndex] ?? "{}");
      let receipt: Record<string, unknown> | null = null;
      try {
        receipt = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        limitations.add(`Receipt ${hash} was retained but could not be parsed during export.`);
      }
      files.push({
        path: `receipts/${safeName(hash)}.json`,
        content: receipt ? json(receipt) : `${raw}\n`,
      });
      if (receipt && Array.isArray(receipt.logs)) {
        files.push({
          path: `raw-logs/${safeName(hash)}.json`,
          content: json(receipt.logs),
        });
      }
    }
  } else {
    files.push({
      path: "receipts/README.md",
      content: "Raw RPC receipt payloads were not retained by this applied scope. Do not treat decoded rows as a substitute for raw evidence.\n",
    });
    limitations.add("Raw RPC receipt payloads were unavailable to case export.");
  }
  if (!rawLogDataset && !rawReceiptDataset) {
    files.push({
      path: "raw-logs/README.md",
      content: "Raw log payloads were not retained by this applied scope.\n",
    });
    limitations.add("Raw log payloads were unavailable to case export.");
  }
  files.push({
    path: "screenshots/README.md",
    content: "Screenshots must be captured by the analyst at export time; none were generated automatically.\n",
  });

  const caseRecord = {
    schema_version: 1,
    case_id: caseId,
    analyst: input.analyst?.trim() || "not supplied",
    created_at: createdAt,
    chain_id: 100,
    view_id: input.viewId,
    task_mode: input.server.mode,
    selection: input.server.selection,
    subjects: evidenceScopes.flatMap((scope) => {
      const predicate = scope.predicate as { subjects?: string[] } | undefined;
      return predicate?.subjects ?? [];
    }),
    application: {
      commit: diagnostics?.app_commit ?? transactionScope?.app_commit ?? "unknown",
      served_bundle_sha256: diagnostics?.bundle_sha256 ?? "unknown",
      served_bundle_mtime: diagnostics?.bundle_mtime ?? "unknown",
    },
    dbt: {
      manifest_sha256:
        diagnostics?.dbt_manifest_sha256 ?? transactionScope?.dbt_manifest_sha256 ?? "unknown",
    },
    forensic_scope_ids: evidenceScopes.map((scope) => scope.scope_id),
    datasets: exportedDatasets,
  };
  files.push({ path: "case.json", content: json(caseRecord) });
  files.push({
    path: "limitations.md",
    content: `# Limitations\n\n${[...limitations].map((value) => `- ${value}`).join("\n") || "- None reported."}\n`,
  });
  files.push({
    path: "manifest.sha256",
    content: `${caseRecord.dbt.manifest_sha256}\n`,
  });
  return files;
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function u16(value: number): Uint8Array {
  return Uint8Array.of(value & 0xff, (value >>> 8) & 0xff);
}

function u32(value: number): Uint8Array {
  return Uint8Array.of(
    value & 0xff,
    (value >>> 8) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 24) & 0xff,
  );
}

function concat(chunks: Uint8Array[]): Uint8Array {
  const output = new Uint8Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.length;
  }
  return output;
}

/** Build a deterministic, uncompressed ZIP without adding a runtime archive dependency. */
export function buildStoredZip(files: CaseExportFile[]): Uint8Array {
  const encoder = new TextEncoder();
  const local: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    const name = encoder.encode(file.path);
    const body = typeof file.content === "string" ? encoder.encode(file.content) : file.content;
    const checksum = crc32(body);
    const localHeader = concat([
      u32(0x04034b50), u16(20), u16(0x0800), u16(0), u16(0), u16(0),
      u32(checksum), u32(body.length), u32(body.length), u16(name.length), u16(0), name,
    ]);
    local.push(localHeader, body);
    central.push(concat([
      u32(0x02014b50), u16(20), u16(20), u16(0x0800), u16(0), u16(0), u16(0),
      u32(checksum), u32(body.length), u32(body.length), u16(name.length), u16(0),
      u16(0), u16(0), u16(0), u32(0), u32(offset), name,
    ]));
    offset += localHeader.length + body.length;
  }
  const centralBytes = concat(central);
  const end = concat([
    u32(0x06054b50), u16(0), u16(0), u16(files.length), u16(files.length),
    u32(centralBytes.length), u32(offset), u16(0),
  ]);
  return concat([...local, centralBytes, end]);
}

export function downloadCaseExport(input: CaseExportInput): void {
  const files = buildCaseExportFiles(input);
  const zip = buildStoredZip(files);
  const zipBuffer = zip.buffer.slice(
    zip.byteOffset,
    zip.byteOffset + zip.byteLength,
  ) as ArrayBuffer;
  const href = URL.createObjectURL(
    new Blob([zipBuffer], { type: "application/zip" }),
  );
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = `${JSON.parse(String(files.find((file) => file.path === "case.json")?.content)).case_id}.zip`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(href), 0);
}
