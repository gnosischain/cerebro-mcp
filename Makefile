.PHONY: build-ui build-ui-report build-ui-token-explorer build-ui-metric-lab install dev

build-ui: build-ui-report build-ui-token-explorer build-ui-metric-lab
	cp ui/dist/index.html src/cerebro_mcp/static/report.html
	cp ui/dist/token-explorer.html src/cerebro_mcp/static/token_explorer.html
	cp ui/dist/metric-lab.html src/cerebro_mcp/static/metric_lab.html

build-ui-report:
	cd ui && npm ci && CEREBRO_UI_ENTRY=report npm run build

build-ui-token-explorer:
	cd ui && CEREBRO_UI_ENTRY=tokenExplorer npm run build

build-ui-metric-lab:
	cd ui && CEREBRO_UI_ENTRY=metricLab npm run build

install: build-ui
	pip install -e .

dev:
	cd ui && npm run dev
