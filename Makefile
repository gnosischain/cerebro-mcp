.PHONY: build-ui build-ui-report build-ui-metric-lab build-ui-portfolio build-ui-graph-explorer build-ui-data-catalog build-ui-contract-explorer build-ui-model-lineage install dev test serve-catalog

# Serve the mini-apps over HTTP so they open in a browser (Data Catalog at
# /app/data_catalog). Reuses .env for ClickHouse/SEMANTIC config — the only
# extra is an auth token (defaults to "dev"; override: `make serve-catalog MCP_AUTH_TOKEN=…`).
# Binds port 8010 by default (NOT 8000 — that commonly collides with other local
# FastAPI/uvicorn dev servers bound to 127.0.0.1, which silently win localhost
# routing and return {"detail":"Not Found"}). Override: `make serve-catalog FASTMCP_PORT=…`.
serve-catalog:
	@echo "→ open: http://localhost:$${FASTMCP_PORT:-8010}/app/data_catalog?token=$${MCP_AUTH_TOKEN:-dev}"
	MCP_AUTH_TOKEN=$${MCP_AUTH_TOKEN:-dev} FASTMCP_PORT=$${FASTMCP_PORT:-8010} uv run cerebro-mcp --sse

# Each per-app target builds AND copies into src/cerebro_mcp/static/ so that
# `make build-ui-<app>` is self-contained — running it and restarting the MCP
# server is enough for the app's new bundle to take effect. The top-level
# build-ui target simply fans out to all per-app targets.

build-ui: build-ui-report build-ui-metric-lab build-ui-portfolio build-ui-graph-explorer build-ui-data-catalog build-ui-contract-explorer build-ui-model-lineage

build-ui-report:
	cd ui && npm ci && CEREBRO_UI_ENTRY=report npm run build
	cp ui/dist/index.html src/cerebro_mcp/static/report.html

build-ui-metric-lab:
	cd ui && CEREBRO_UI_ENTRY=metricLab npm run build
	cp ui/dist/metric-lab.html src/cerebro_mcp/static/metric_lab.html

build-ui-portfolio:
	cd ui && CEREBRO_UI_ENTRY=portfolio npm run build
	cp ui/dist/portfolio.html src/cerebro_mcp/static/portfolio.html

build-ui-graph-explorer:
	cd ui && CEREBRO_UI_ENTRY=graphExplorer npm run build
	cp ui/dist/graph-explorer.html src/cerebro_mcp/static/graph_explorer.html

# Data Catalog is a SPLIT bundle: a small HTML shell + hashed JS/CSS/woff2 under
# dist/assets/, copied to static/assets/ and served (cacheable) at
# /app/data_catalog/assets/. Clean stale hashed files on each build.
build-ui-data-catalog:
	rm -rf ui/dist/assets
	cd ui && CEREBRO_UI_ENTRY=dataCatalog npm run build
	cp ui/dist/data-catalog.html src/cerebro_mcp/static/data_catalog.html
	rm -rf src/cerebro_mcp/static/assets
	mkdir -p src/cerebro_mcp/static/assets
	cp -R ui/dist/assets/. src/cerebro_mcp/static/assets/

build-ui-contract-explorer:
	cd ui && CEREBRO_UI_ENTRY=contractExplorer npm run build
	cp ui/dist/contract-explorer.html src/cerebro_mcp/static/contract_explorer.html

build-ui-model-lineage:
	cd ui && CEREBRO_UI_ENTRY=modelLineage npm run build
	cp ui/dist/model-lineage.html src/cerebro_mcp/static/model_lineage.html

install: build-ui
	pip install -e .

dev:
	cd ui && npm run dev

test:
	pytest tests/ -q
