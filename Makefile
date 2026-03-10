.PHONY: build-ui install dev

build-ui:
	cd ui && npm ci && npm run build
	cp ui/dist/index.html src/cerebro_mcp/static/report.html

install: build-ui
	pip install -e .

dev:
	cd ui && npm run dev
