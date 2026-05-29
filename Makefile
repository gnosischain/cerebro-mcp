.PHONY: build-ui build-ui-report build-ui-metric-lab build-ui-portfolio build-ui-graph-explorer build-ui-contract-explorer build-ui-model-lineage install dev test

# Each per-app target builds AND copies into src/cerebro_mcp/static/ so that
# `make build-ui-<app>` is self-contained — running it and restarting the MCP
# server is enough for the app's new bundle to take effect. The top-level
# build-ui target simply fans out to all per-app targets.

build-ui: build-ui-report build-ui-metric-lab build-ui-portfolio build-ui-graph-explorer build-ui-contract-explorer build-ui-model-lineage

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
