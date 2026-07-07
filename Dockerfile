# Stage 1: Build the React UI
FROM node:20-slim AS ui-builder

WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ .
# Build every mini-app entry (mirrors `make build-ui`), not just the report app.
# `report` must run first — it is the only entry that empties dist/ (see
# vite.config.ts `emptyOutDir`); every later entry accumulates into the same
# dist/. Building them here means CI always ships fresh bundles, so the served
# mini-apps can never drift from source the way committed artifacts can.
RUN CEREBRO_UI_ENTRY=report          npm run build \
 && CEREBRO_UI_ENTRY=metricLab       npm run build \
 && CEREBRO_UI_ENTRY=portfolio       npm run build \
 && CEREBRO_UI_ENTRY=graphExplorer   npm run build \
 && CEREBRO_UI_ENTRY=contractExplorer npm run build \
 && CEREBRO_UI_ENTRY=modelLineage    npm run build \
 && CEREBRO_UI_ENTRY=dataCatalog     npm run build

# Stage 2: Build the Python package
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

# Overwrite the committed UI bundles with the freshly built ones so the image
# is authoritative and never ships a stale mini-app. Drop the committed split
# assets/ first (hashed filenames change per build) before copying the fresh set.
RUN rm -rf src/cerebro_mcp/static/assets
COPY --from=ui-builder /ui/dist/index.html             src/cerebro_mcp/static/report.html
COPY --from=ui-builder /ui/dist/metric-lab.html        src/cerebro_mcp/static/metric_lab.html
COPY --from=ui-builder /ui/dist/portfolio.html         src/cerebro_mcp/static/portfolio.html
COPY --from=ui-builder /ui/dist/graph-explorer.html    src/cerebro_mcp/static/graph_explorer.html
COPY --from=ui-builder /ui/dist/contract-explorer.html src/cerebro_mcp/static/contract_explorer.html
COPY --from=ui-builder /ui/dist/model-lineage.html     src/cerebro_mcp/static/model_lineage.html
COPY --from=ui-builder /ui/dist/data-catalog.html      src/cerebro_mcp/static/data_catalog.html
COPY --from=ui-builder /ui/dist/assets/                src/cerebro_mcp/static/assets/

RUN pip install --no-cache-dir . && \
    useradd -r -u 1000 cerebro && \
    mkdir -p /data/reports /data/logs /data/saved-queries /data/research_projects && \
    chown -R cerebro:cerebro /data

ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000
ENV CEREBRO_REPORT_DIR=/data/reports
ENV THINKING_LOG_DIR=/data/logs
ENV CEREBRO_SAVED_QUERIES_DIR=/data/saved-queries
ENV CEREBRO_RESEARCH_DIR=/data/research_projects

EXPOSE 8000
USER cerebro

ENTRYPOINT ["cerebro-mcp", "--sse"]
