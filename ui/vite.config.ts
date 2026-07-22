import { defineConfig } from "vite";
import { resolve } from "path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Each cerebro mini app is a self-contained single-file HTML bundle.
// `vite-plugin-singlefile` cannot inline more than one entry at a time, so
// the Makefile invokes this config once per app — selecting the entry via
// the CEREBRO_UI_ENTRY env var.
//
// Valid values:
//   report           (default — original report renderer)
//   metricLab
//   portfolio
//   graphExplorer
//   contractExplorer
//   modelLineage
//   cowExplorer
//   governance

const ENTRY_MAP: Record<string, { html: string; out: string }> = {
  report:           { html: "index.html",            out: "index.html" },
  metricLab:        { html: "metric-lab.html",       out: "metric-lab.html" },
  portfolio:        { html: "portfolio.html",        out: "portfolio.html" },
  graphExplorer:    { html: "graph-explorer.html",   out: "graph-explorer.html" },
  graphExplorerWeb: { html: "graph-explorer.html",   out: "graph-explorer.html" },
  contractExplorer: { html: "contract-explorer.html", out: "contract-explorer.html" },
  modelLineage:     { html: "model-lineage.html",    out: "model-lineage.html" },
  dataCatalog:      { html: "data-catalog.html",     out: "data-catalog.html" },
  reportStudio:     { html: "report-studio.html",    out: "report-studio.html" },
  cowExplorer:      { html: "cow-explorer.html",     out: "cow-explorer.html" },
  governance:       { html: "governance.html",       out: "governance.html" },
};

const entryName = process.env.CEREBRO_UI_ENTRY ?? "report";
const entry = ENTRY_MAP[entryName];
if (!entry) {
  throw new Error(
    `Unknown CEREBRO_UI_ENTRY=${entryName}. Valid: ${Object.keys(ENTRY_MAP).join(", ")}`,
  );
}

// Chart-heavy apps can ship as split bundles (hashed JS/CSS assets under
// /app/{app_id}/assets/) so immutable code and fonts cache across visits while
// only the tiny token-bearing HTML shell is re-fetched.
const SPLIT_BASE: Record<string, string> = {
  dataCatalog: "/app/data_catalog/",
  cowExplorer: "/app/cow_explorer/",
  graphExplorerWeb: "/app/graph_explorer/",
  governance: "/app/governance/",
};
const splitBase = SPLIT_BASE[entryName];
const isSplit = Boolean(splitBase);

export default defineConfig({
  base: splitBase ?? "./",
  plugins: [
    react(),
    tailwindcss(),
    ...(isSplit ? [] : [viteSingleFile()]),
  ],
  build: {
    target: "es2020",
    outDir: process.env.CEREBRO_UI_OUT_DIR ?? "dist",
    emptyOutDir: entryName === "report",
    assetsInlineLimit: isSplit ? 0 : undefined,
    rollupOptions: {
      input: resolve(__dirname, entry.html),
    },
  },
});
