.PHONY: build-ui build-ui-report build-ui-token-explorer build-ui-metric-lab build-ui-yield-opportunities build-ui-portfolio build-ui-graph-explorer install dev test

# Each per-app target builds AND copies into src/cerebro_mcp/static/ so that
# `make build-ui-<app>` is self-contained — running it and restarting the MCP
# server is enough for the app's new bundle to take effect. The top-level
# build-ui target simply fans out to all per-app targets.

build-ui: build-ui-report build-ui-token-explorer build-ui-metric-lab build-ui-yield-opportunities build-ui-portfolio build-ui-graph-explorer

build-ui-report:
	cd ui && npm ci && CEREBRO_UI_ENTRY=report npm run build
	cp ui/dist/index.html src/cerebro_mcp/static/report.html

build-ui-token-explorer:
	cd ui && CEREBRO_UI_ENTRY=tokenExplorer npm run build
	cp ui/dist/token-explorer.html src/cerebro_mcp/static/token_explorer.html

build-ui-metric-lab:
	cd ui && CEREBRO_UI_ENTRY=metricLab npm run build
	cp ui/dist/metric-lab.html src/cerebro_mcp/static/metric_lab.html

build-ui-yield-opportunities:
	cd ui && CEREBRO_UI_ENTRY=yieldOpportunities npm run build
	cp ui/dist/yield-opportunities.html src/cerebro_mcp/static/yield_opportunities.html

build-ui-portfolio:
	cd ui && CEREBRO_UI_ENTRY=portfolio npm run build
	cp ui/dist/portfolio.html src/cerebro_mcp/static/portfolio.html

build-ui-graph-explorer:
	cd ui && CEREBRO_UI_ENTRY=graphExplorer npm run build
	cp ui/dist/graph-explorer.html src/cerebro_mcp/static/graph_explorer.html

install: build-ui
	pip install -e .

dev:
	cd ui && npm run dev

test:
	pytest tests/ -q
