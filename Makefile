.PHONY: build-ui build-ui-report build-ui-metric-lab build-ui-portfolio build-ui-graph-explorer build-ui-data-catalog build-ui-cow-explorer build-ui-governance build-ui-contract-explorer build-ui-model-lineage build-ui-report-studio install dev test serve-catalog status-catalog stop-catalog restart-catalog gen-catalog distill-templates

# Serve the mini-apps over HTTP so they open in a browser (Data Catalog at
# /app/data_catalog). Reuses .env for ClickHouse/SEMANTIC config — the only
# extra is an auth token (defaults to "dev"; override: `make serve-catalog MCP_AUTH_TOKEN=…`).
# Binds port 8010 by default (NOT 8000 — that commonly collides with other local
# FastAPI/uvicorn dev servers bound to 127.0.0.1, which silently win localhost
# routing and return {"detail":"Not Found"}). Override: `make serve-catalog FASTMCP_PORT=…`.
serve-catalog:
	MCP_AUTH_TOKEN=$${MCP_AUTH_TOKEN:-dev} FASTMCP_PORT=$${FASTMCP_PORT:-8010} python3 scripts/dev/catalog_server.py serve

status-catalog:
	MCP_AUTH_TOKEN=$${MCP_AUTH_TOKEN:-dev} FASTMCP_PORT=$${FASTMCP_PORT:-8010} python3 scripts/dev/catalog_server.py status

stop-catalog:
	MCP_AUTH_TOKEN=$${MCP_AUTH_TOKEN:-dev} FASTMCP_PORT=$${FASTMCP_PORT:-8010} python3 scripts/dev/catalog_server.py stop

restart-catalog:
	MCP_AUTH_TOKEN=$${MCP_AUTH_TOKEN:-dev} FASTMCP_PORT=$${FASTMCP_PORT:-8010} python3 scripts/dev/catalog_server.py restart

# Each per-app target builds AND copies into src/cerebro_mcp/static/ so that
# `make build-ui-<app>` is self-contained — running it and restarting the MCP
# server is enough for the app's new bundle to take effect. The top-level
# build-ui target simply fans out to all per-app targets.

build-ui: build-ui-report build-ui-metric-lab build-ui-portfolio build-ui-graph-explorer build-ui-data-catalog build-ui-cow-explorer build-ui-governance build-ui-contract-explorer build-ui-model-lineage build-ui-report-studio

build-ui-report:
	cd ui && npm ci && CEREBRO_UI_ENTRY=report npm run build
	cp ui/dist/index.html src/cerebro_mcp/static/report.html

build-ui-metric-lab:
	cd ui && CEREBRO_UI_ENTRY=metricLab npm run build
	cp ui/dist/metric-lab.html src/cerebro_mcp/static/metric_lab.html

# Instruction-template catalog: catalog/templates/*.md -> catalog.gen.json
# (consumed by BOTH the Template Gallery UI and the benchmarks templates suite).
gen-catalog:
	python3 scripts/dev/gen_instruction_catalog.py

# Distill the latest templates benchmark run into benchmarks.gen.json for the UI.
distill-templates:
	python3 -m benchmarks.distill_templates

build-ui-report-studio: gen-catalog
	cd ui && CEREBRO_UI_ENTRY=reportStudio npm run build
	cp ui/dist/report-studio.html src/cerebro_mcp/static/report_studio.html

build-ui-portfolio:
	cd ui && CEREBRO_UI_ENTRY=portfolio npm run build
	cp ui/dist/portfolio.html src/cerebro_mcp/static/portfolio.html

build-ui-graph-explorer:
	rm -rf ui/dist-graph-explorer-inline ui/dist-graph-explorer-web
	cd ui && CEREBRO_UI_ENTRY=graphExplorer CEREBRO_UI_OUT_DIR=dist-graph-explorer-inline npm run build
	cp ui/dist-graph-explorer-inline/graph-explorer.html src/cerebro_mcp/static/graph_explorer.html
	cd ui && CEREBRO_UI_ENTRY=graphExplorerWeb CEREBRO_UI_OUT_DIR=dist-graph-explorer-web npm run build
        # Dev-fixture leak gate. Uses grep, not rg: make runs recipes under
        # /bin/sh, where rg is frequently absent — and `! rg …` turns rg's
        # "command not found" (127) into a PASS, so the gate silently stopped
        # guarding anything. grep -E is POSIX and always present.
	! grep -rqE '0xv1c|ge_force_flows|ge_force_timeline' ui/dist-graph-explorer-inline ui/dist-graph-explorer-web
	cp ui/dist-graph-explorer-web/graph-explorer.html src/cerebro_mcp/static/graph_explorer_web.html
	rm -rf src/cerebro_mcp/static/assets/graph_explorer
	mkdir -p src/cerebro_mcp/static/assets/graph_explorer
	cp -R ui/dist-graph-explorer-web/assets/. src/cerebro_mcp/static/assets/graph_explorer/

# Split bundles use isolated Vite output and packaged asset namespaces. Building
# one app can never remove another app's hashed assets.
build-ui-data-catalog:
	rm -rf ui/dist-data-catalog
	cd ui && CEREBRO_UI_ENTRY=dataCatalog CEREBRO_UI_OUT_DIR=dist-data-catalog npm run build
	cp ui/dist-data-catalog/data-catalog.html src/cerebro_mcp/static/data_catalog.html
	rm -rf src/cerebro_mcp/static/assets/data_catalog
	mkdir -p src/cerebro_mcp/static/assets/data_catalog
	cp -R ui/dist-data-catalog/assets/. src/cerebro_mcp/static/assets/data_catalog/

build-ui-cow-explorer:
	rm -rf ui/dist-cow-explorer
	cd ui && CEREBRO_UI_ENTRY=cowExplorer CEREBRO_UI_OUT_DIR=dist-cow-explorer npm run build
	cp ui/dist-cow-explorer/cow-explorer.html src/cerebro_mcp/static/cow_explorer.html
	rm -rf src/cerebro_mcp/static/assets/cow_explorer
	mkdir -p src/cerebro_mcp/static/assets/cow_explorer
	cp -R ui/dist-cow-explorer/assets/. src/cerebro_mcp/static/assets/cow_explorer/

build-ui-governance:
	rm -rf ui/dist-governance
	cd ui && CEREBRO_UI_ENTRY=governance CEREBRO_UI_OUT_DIR=dist-governance npm run build
	cp ui/dist-governance/governance.html src/cerebro_mcp/static/governance.html
	rm -rf src/cerebro_mcp/static/assets/governance
	mkdir -p src/cerebro_mcp/static/assets/governance
	cp -R ui/dist-governance/assets/. src/cerebro_mcp/static/assets/governance/

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

# ---- benchmarks (run via python -m, NEVER pytest — see benchmarks/README.md) ----

bench-check:              ## regression gate: pytest + deterministic correctness suites (no ClickHouse). The CI + pre-push gate.
	uv run pytest tests/ -q
	uv run python -m benchmarks.run --suite search
	uv run python -m benchmarks.run --suite workflows
	uv run python -m benchmarks.run --suite semantic

bench-latency:            ## per-tool latency, deterministic fake ClickHouse (laptop-safe)
	uv run python -m benchmarks.run --suite latency

bench-latency-real:       ## per-tool latency against real ClickHouse
	CEREBRO_EVAL_CLICKHOUSE=1 uv run python -m benchmarks.run --suite latency

bench-workflows:          ## scripted SOP workflows (gate compliance + call/char cost)
	uv run python -m benchmarks.run --suite workflows

bench-search:             ## search/routing quality (hit@k, MRR, route correctness)
	uv run python -m benchmarks.run --suite search

bench-semantic:           ## semantic layer (runtime, routing cache, planner, SQL goldens, E2E, coverage)
	uv run python -m benchmarks.run --suite semantic

bench-load:               ## SSE load/concurrency sweep (spawns a local server; needs real ClickHouse)
	CEREBRO_EVAL_CLICKHOUSE=1 uv run python -m benchmarks.run --suite load

bench-compare:            ## make bench-compare BASE=results/a.json CAND=results/b.json
	uv run python -m benchmarks.compare $(BASE) $(CAND)

bench-report:             ## regenerate the HTML dashboard from all result files
	uv run python -m benchmarks.report
