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

const ENTRY_MAP: Record<string, { html: string; out: string }> = {
  report:           { html: "index.html",            out: "index.html" },
  metricLab:        { html: "metric-lab.html",       out: "metric-lab.html" },
  portfolio:        { html: "portfolio.html",        out: "portfolio.html" },
  graphExplorer:    { html: "graph-explorer.html",   out: "graph-explorer.html" },
  contractExplorer: { html: "contract-explorer.html", out: "contract-explorer.html" },
  modelLineage:     { html: "model-lineage.html",    out: "model-lineage.html" },
  dataCatalog:      { html: "data-catalog.html",     out: "data-catalog.html" },
};

const entryName = process.env.CEREBRO_UI_ENTRY ?? "report";
const entry = ENTRY_MAP[entryName];
if (!entry) {
  throw new Error(
    `Unknown CEREBRO_UI_ENTRY=${entryName}. Valid: ${Object.keys(ENTRY_MAP).join(", ")}`,
  );
}

// The Data Catalog ships as a SPLIT bundle (hashed JS/CSS/woff2 emitted as
// separate cacheable assets under /app/data_catalog/assets/) instead of one
// inlined HTML — so the immutable code+fonts cache across visits while only the
// tiny token-bearing HTML shell is re-fetched. All other apps stay single-file.
const isSplit = entryName === "dataCatalog";

export default defineConfig({
  base: isSplit ? "/app/data_catalog/" : "./",
  plugins: [react(), tailwindcss(), ...(isSplit ? [] : [viteSingleFile()])],
  build: {
    target: "es2020",
    outDir: "dist",
    emptyOutDir: entryName === "report",
    assetsInlineLimit: isSplit ? 0 : undefined,
    rollupOptions: {
      input: resolve(__dirname, entry.html),
    },
  },
});
