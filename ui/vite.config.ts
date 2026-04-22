import { defineConfig } from "vite";
import { resolve } from "path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Each cerebro mini app is a self-contained single-file HTML bundle.
// `vite-plugin-singlefile` cannot inline more than one entry at a time, so
// the Makefile invokes this config three times — once per app — selecting
// the entry via the CEREBRO_UI_ENTRY env var.
//
// Valid values:
//   report          (default — original report renderer)
//   tokenExplorer
//   metricLab
//   yieldOpportunities
//   portfolio

const ENTRY_MAP: Record<string, { html: string; out: string }> = {
  report:        { html: "index.html",          out: "index.html" },
  tokenExplorer: { html: "token-explorer.html", out: "token-explorer.html" },
  metricLab:     { html: "metric-lab.html",     out: "metric-lab.html" },
  yieldOpportunities: { html: "yield-opportunities.html", out: "yield-opportunities.html" },
  portfolio:     { html: "portfolio.html",      out: "portfolio.html" },
  graphExplorer: { html: "graph-explorer.html", out: "graph-explorer.html" },
  quarterlyReview: { html: "quarterly-review.html", out: "quarterly-review.html" },
};

const entryName = process.env.CEREBRO_UI_ENTRY ?? "report";
const entry = ENTRY_MAP[entryName];
if (!entry) {
  throw new Error(
    `Unknown CEREBRO_UI_ENTRY=${entryName}. Valid: ${Object.keys(ENTRY_MAP).join(", ")}`,
  );
}

export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  build: {
    target: "es2020",
    outDir: "dist",
    emptyOutDir: entryName === "report",
    rollupOptions: {
      input: resolve(__dirname, entry.html),
    },
  },
});
