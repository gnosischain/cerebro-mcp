import re
from typing import Optional
from pydantic import ConfigDict, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Web3 RPC / ABI resolution ---
    GNOSIS_RPC_URL: str = "https://rpc.gnosischain.com"
    GNOSIS_ARCHIVE_RPC_URL: str = ""
    BLOCKSCOUT_API_BASE_URL: str = "https://gnosis.blockscout.com/api/v2"
    RPC_TIMEOUT_SECONDS: int = 15
    RPC_MAX_RETRIES: int = 3
    ABI_CACHE_TTL_SECONDS: int = 3600
    ABI_CACHE_MAX_ENTRIES: int = 512

    # --- RPC scan engine (bulk log/call/storage/code/trace scans) ---
    # Off by default: the scan engine writes results into ClickHouse scratch
    # tables, which needs grants the read-only deployment user may not have:
    #   GRANT CREATE DATABASE, CREATE TABLE, INSERT, DROP TABLE, SELECT
    #   ON scratch.* TO <cerebro user>
    RPC_SCAN_ENABLED: bool = False
    RPC_SCAN_SCRATCH_DATABASE: str = "scratch"
    RPC_SCAN_SCRATCH_TTL_DAYS: int = 7           # registry-driven DROP policy
    RPC_SCAN_JOB_TTL_SECONDS: int = 3600         # terminal jobs evicted from memory
    RPC_SCAN_MAX_CONCURRENT_JOBS: int = 2
    RPC_SCAN_RPC_TIMEOUT_SECONDS: int = 60
    RPC_SCAN_LOG_INIT_CHUNK_BLOCKS: int = 10_000
    RPC_SCAN_ADDRESS_BATCH: int = 600            # addresses per indexed-topic chunk
    RPC_SCAN_MULTICALL_BATCH: int = 600
    RPC_SCAN_MULTICALL_ADDRESS: str = "0xcA11bde05977b3631167028862bE2a173976CA11"
    RPC_SCAN_STORAGE_WORKERS: int = 30
    RPC_SCAN_CODE_WORKERS: int = 30
    RPC_SCAN_TRACE_WORKERS: int = 8
    RPC_SCAN_TRACE_BLOCKS_PER_CALL: int = 100    # node cap on trace_filter
    RPC_SCAN_TRACE_MAX_RANGE_BLOCKS: int = 200_000
    RPC_SCAN_PREFER_ARCHIVE_FOR_LOGS: bool = True
    RPC_SCAN_INSERT_BATCH_ROWS: int = 5_000
    RPC_SCAN_INSERT_FLUSH_SECONDS: float = 5.0
    RPC_SCAN_INSERT_MAX_RETRIES: int = 3
    RPC_SCAN_MAX_ROWS_PER_JOB: int = 5_000_000
    RPC_SCAN_MAX_ADDRESSES: int = 500_000        # cap on a resolved address set
    RPC_SCAN_SYNC_WAIT_MAX_SECONDS: int = 25
    RPC_SCAN_MAX_INLINE_ADDRESSES: int = 500
    # Engine-side (non-indexed arg) log filtering guardrail: a post-filtered
    # scan must be tight (contracts/topic0 + bounded window) to be allowed.
    RPC_SCAN_UNINDEXED_FILTER_MAX_BLOCKS: int = 250_000

    # ClickHouse connection
    CLICKHOUSE_HOST: str = "localhost"
    CLICKHOUSE_PORT: int = 8443
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = ""
    CLICKHOUSE_SECURE: bool = True

    # dbt manifest source (local path takes precedence when configured)
    DBT_MANIFEST_URL: Optional[str] = (
        "https://gnosischain.github.io/dbt-cerebro/manifest.json"
    )
    DBT_MANIFEST_PATH: str = ""

    # dbt catalog source (local path takes precedence when configured)
    DBT_CATALOG_URL: Optional[str] = (
        "https://gnosischain.github.io/dbt-cerebro/catalog.json"
    )
    DBT_CATALOG_PATH: str = ""

    # External Docs index source
    DOCS_BASE_URL: str = "https://docs.analytics.gnosis.io/"
    DOCS_SEARCH_INDEX_URL: Optional[str] = (
        "https://docs.analytics.gnosis.io/search/search_index.json"
    )
    DOCS_SEARCH_INDEX_PATH: str = ""
    DOCS_REFRESH_INTERVAL_SECONDS: int = 3600
    GNOSIS_CHAIN_DOCS_LLM_URL: Optional[str] = "https://docs.gnosischain.com/llms.txt"

    # Semantic artifacts
    SEMANTIC_ENABLED: bool = False
    SEMANTIC_REGISTRY_URL: Optional[str] = (
        "https://gnosischain.github.io/dbt-cerebro/semantic_registry.json"
    )
    SEMANTIC_REGISTRY_PATH: str = ""
    SEMANTIC_DOCS_INDEX_URL: Optional[str] = (
        "https://gnosischain.github.io/dbt-cerebro/semantic_docs_index.json"
    )
    SEMANTIC_DOCS_INDEX_PATH: str = ""
    # Optional graph-catalog sidecar (WS4). When present and consistent with the
    # registry, graph profiles are reconstructed from it 1:1; otherwise the
    # runtime falls back to live discovery off the registry, so a missing or
    # stale catalog never breaks the server.
    SEMANTIC_GRAPH_CATALOG_URL: Optional[str] = (
        "https://gnosischain.github.io/dbt-cerebro/semantic_graph_catalog.json"
    )
    SEMANTIC_GRAPH_CATALOG_PATH: str = ""
    SEMANTIC_REFRESH_INTERVAL_SECONDS: int = 300
    # When True, every semantic call also stats the local manifest /
    # catalog files and triggers an immediate force_reload if either
    # is newer than the in-memory snapshot's load timestamp. Closes
    # the `manifest_hash_mismatch` window after a local `dbt build`
    # during semantic-layer authoring loops, without waiting for the
    # 300-second TTL. No-op for deployed instances that load the
    # registry over HTTPS (the helper short-circuits when no local
    # candidate is on disk). Flip to False to disable if a regression
    # surfaces in production. See `_local_artifacts_advanced` in
    # `tools/semantic.py`.
    SEMANTIC_AUTOLOAD_ON_LOCAL_MTIME: bool = True

    # Dashboard builder
    DASHBOARD_BUILDER_ENABLED: bool = False
    METRICS_DASHBOARD_PATH: str = ""

    # Lean-core tool surface (Phase 3). When True, tools classified
    # tier="advanced" in tools/tool_meta.py are dropped from the model-facing
    # `list_tools` (the app-only drop still applies too); only the ~17 core
    # tools stay visible by default. OFF until measured — flip on after a
    # transcript replay confirms the core set covers common follow-ups.
    # Advanced tools remain callable and can be un-hidden at runtime via
    # `load_tools([...])`.
    LEAN_CORE_ENABLED: bool = False

    # Custom tools (MCP Toolbox pattern)
    CUSTOM_TOOLS_ENABLED: bool = False
    CUSTOM_TOOLS_PATH: str = ""
    CLICKHOUSE_AGENT_SKILLS_PATH: str = "src/cerebro_mcp/static/clickhouse_agent_skills"

    # Safety limits
    MAX_ROWS: int = 10000
    CLICKHOUSE_VERIFY: bool = True
    CLICKHOUSE_CONNECT_TIMEOUT: int = 30
    CLICKHOUSE_SEND_RECEIVE_TIMEOUT: int = 300
    CLICKHOUSE_QUERY_TIMEOUT_SECONDS: Optional[int] = None
    QUERY_TIMEOUT_SECONDS: int = 30
    MAX_QUERY_LENGTH: int = 10000
    TOOL_RESULT_MAX_ROWS: int = 200
    TOOL_RESULT_MAX_CHARS: Optional[int] = None
    TOOL_RESPONSE_MAX_CHARS: int = 40_000
    TOOL_SUMMARY_BUDGET_RATIO: float = 0.9
    ASYNC_RESULT_PAGE_SIZE: int = 200
    ASYNC_RESULT_MEMORY_THRESHOLD_BYTES: int = 5_000_000
    ASYNC_RESULT_DIR: str = ".cerebro/query_results"
    CEREBRO_RESEARCH_DIR: str = ".cerebro/research_projects"
    RESEARCH_PAGE_SIZE_DEFAULT: int = 20
    RESEARCH_PAGE_SIZE_MAX: int = 100
    ALLOW_INSECURE_REMOTE_TRANSPORT: bool = False

    # Security / audit
    MCP_SECURITY_POLICY_MODE: str = "log_only"  # future: "warn", "enforce"
    MCP_SECURITY_LOG_DIR: str = ".cerebro/security_audit"
    MCP_EXPECTED_MANIFEST_SHA256: str = ""  # optional pin, empty = disabled

    # Manifest refresh
    MANIFEST_REFRESH_INTERVAL_SECONDS: int = 300

    # Graph Explorer BFS expansion limits. The renderer (Cosmos GL / WebGL)
    # comfortably handles tens of thousands of nodes, so the cap mostly guards
    # query cost, not the UI. SEED cap bounds the initial multi-profile load;
    # EXPANSION cap bounds cumulative BFS growth; PER_HOP_BUDGET bounds how many
    # new nodes a single hop round may add so one dense frontier can't consume
    # the whole budget in one step.
    GRAPH_EXPLORER_SEED_NODE_CAP: int = 3000
    GRAPH_EXPLORER_BFS_NODE_CAP: int = 15000
    GRAPH_EXPLORER_BFS_PER_HOP_BUDGET: int = 3000

    # Phase 1: column-scoped schema injection. Tables narrower than the
    # threshold are injected verbatim into LLM prompts; wider tables are
    # BM25-scoped to the top K columns plus key/date columns.
    SQL_COMPILER_FULL_SCHEMA_THRESHOLD: int = 30
    SQL_COMPILER_TOP_COLUMNS: int = 20

    # Phase 2: DuckDB + Parquet simulation sandbox. CH data is exported
    # into a private DuckDB instance per sandbox so simulator agents can
    # run UPDATE/INSERT/DELETE on a snapshot without touching production.
    # Off by default — opt-in for local laptop use; deployed env keeps
    # this off to avoid provisioning persistent storage.
    SANDBOX_ENABLED: bool = False
    SANDBOX_ROOT: str = ".cerebro/sandboxes"
    SANDBOX_MAX_CONCURRENT: int = 4         # LRU-evicted past this
    SANDBOX_TTL_SECONDS: int = 1800         # 30 min idle → swept
    SANDBOX_MAX_BYTES_PER_EXPORT: int = 2 * 1024 * 1024 * 1024   # 2 GB

    # Phase 3: resumable gated workflows. SQLite event log records every
    # phase transition / LLM call / gate flip so a workflow that's
    # interrupted (Anthropic 529, network blip, kill -9) can be replayed
    # without losing the queries it already ran.
    #
    # WORKFLOW_RESUME_TOOLS_ENABLED only gates the user-facing
    # `list_resumable_workflows` / `get_workflow_resume_hint` /
    # `recompute_workflow_resume_hint` tool registrations. The
    # underlying event store is initialized at server start and used
    # unconditionally by `workflow_registry`, `workflow_runner`, and
    # the `*_resume.py` handlers (research, quarterly review,
    # storyteller). Disabling this flag does NOT disable those writes.
    WORKFLOW_RESUME_TOOLS_ENABLED: bool = False
    # EVENT_STORE_PATH must resolve to a path the server process can
    # open and write. The default lives under the project working dir,
    # which is fine for local dev. In containers with
    # `read_only_root_filesystem=true`, override this to a writable
    # location (`/tmp/cerebro_state.db` for ephemeral; a PVC-backed
    # path if you need workflow resumability across pod restarts).
    # Leaving the default in a read-only container will hang or error
    # every workflow tool call.
    EVENT_STORE_PATH: str = ".cerebro/cerebro_state.db"
    # Workflows older than this in `running` / `waiting_gate` state are
    # marked `orphaned` on server startup. 24h covers a long quarterly
    # review without nuking a workflow that's legitimately paused for
    # human review overnight.
    WORKFLOW_ORPHAN_AGE_SECONDS: int = 24 * 60 * 60
    # Compress event payloads larger than this with gzip before insert.
    # LLM message-history payloads can be 10-100 KB per turn.
    EVENT_PAYLOAD_COMPRESSION_THRESHOLD_BYTES: int = 4096
    # Cap on workers in the parallel fan-out runner (analyst sub-tasks).
    WORKFLOW_MAX_PARALLEL: int = 8

    # Agent enforcement settings
    ENFORCE_CHART_PRECONDITIONS: bool = True
    MIN_MODELS_DETAILED: int = 3      # get_model_details calls required
    MIN_TABLES_VERIFIED: int = 1      # describe_table calls required
    MIN_CHARTS_FOR_REPORT: int = 3
    REQUIRE_CHART_DIVERSITY: bool = True
    MIN_STATISTICAL_QUERIES: int = 1  # hard gate: queries using quantiles/stddev/corr
    MIN_CORRELATION_QUERIES: int = 1  # hard gate: corr/regression queries for multi-metric reports
    MIN_EXPLORATORY_QUERIES: int = 2  # hard gate: execute_query calls before report
    REQUIRE_DIMENSIONAL_BREAKDOWN: bool = True  # hard gate: at least 1 chart with series_field or pie/treemap/heatmap/sankey
    REQUIRE_RELATIONAL_CHART: bool = True       # hard gate: at least 1 scatter/heatmap chart OR correlation query

    # Quality discipline gates (apply to every chart's SQL at report time).
    # Each is a hard reject when True; agent can override via chart metadata
    # (override_reason / description carrying explicit acknowledgment).
    ENFORCE_STOCK_FLOW_DISCIPLINE: bool = True
    ENFORCE_RESIDUAL_BUCKET_DISCLOSURE: bool = True
    ENFORCE_STATIONARITY_ON_CORRELATIONS: bool = True
    ENFORCE_AGGREGATOR_VOLUME_DEDUP: bool = True
    ENFORCE_DISCOVERED_MODEL_COVERAGE: bool = True

    # Stock-measure column names recognised by the stock/flow heuristic.
    # Extend in env if domain-specific stock columns appear (e.g. wave-2 metrics).
    STOCK_MEASURE_COLUMNS_EXTRA: list[str] = []

    # Report serving
    REPORT_SERVER_PORT: int = 0  # 0 = disabled; set to e.g. 8765 for HTTP serving
    REPORT_BASE_URL: str = ""   # Override full URL prefix for deployed setups

    # Grafana dashboard publishing. Off by default — opt-in feature that
    # writes to an external Grafana instance via its HTTP API. When
    # GRAFANA_TOOLS_ENABLED is False the tools are not registered at all.
    GRAFANA_TOOLS_ENABLED: bool = False
    GRAFANA_URL: str = ""
    GRAFANA_API_TOKEN: str = ""
    GRAFANA_CLICKHOUSE_DATASOURCE_UID: str = ""
    GRAFANA_CLICKHOUSE_DATASOURCE_TYPE: str = "grafana-clickhouse-datasource"
    GRAFANA_FOLDER_UID: str = ""             # empty == omit folderUid from payload
    GRAFANA_REQUEST_TIMEOUT_SECONDS: float = 20.0
    GRAFANA_SCHEMA_VERSION: int = 41
    GRAFANA_MIN_REFRESH_SECONDS: int = 60
    GRAFANA_MAX_PANELS: int = 30

    # Thinking / performance tracing
    THINKING_MODE_ENABLED: bool = True
    THINKING_ALWAYS_ON: bool = True
    THINKING_LOG_DIR: str = ".cerebro/logs"
    THINKING_LOG_RETENTION_DAYS: int = 30

    # Databases accessible via the MCP server
    ALLOWED_DATABASES: list[str] = [
        "execution",
        "execution_live",
        "consensus",
        "crawlers_data",
        "nebula",
        "nebula_discv4",
        "dbt",
    ]

    @model_validator(mode="after")
    def _allow_scratch_database(self) -> "Settings":
        """Expose the RPC-scan scratch DB to the read-only query tools.

        The scan engine writes into RPC_SCAN_SCRATCH_DATABASE; analysis then
        continues through the normal execute_query path, which validates
        against ALLOWED_DATABASES (clients/clickhouse.py:_validate_database).
        """
        if self.RPC_SCAN_ENABLED:
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", self.RPC_SCAN_SCRATCH_DATABASE):
                raise ValueError(
                    "RPC_SCAN_SCRATCH_DATABASE must be a plain identifier "
                    "(letters, digits, underscores)"
                )
            if self.RPC_SCAN_SCRATCH_DATABASE not in self.ALLOWED_DATABASES:
                self.ALLOWED_DATABASES = [
                    *self.ALLOWED_DATABASES,
                    self.RPC_SCAN_SCRATCH_DATABASE,
                ]
        return self

    @property
    def effective_query_timeout_seconds(self) -> int:
        return (
            self.CLICKHOUSE_QUERY_TIMEOUT_SECONDS
            if self.CLICKHOUSE_QUERY_TIMEOUT_SECONDS is not None
            else self.QUERY_TIMEOUT_SECONDS
        )

    @property
    def using_legacy_query_timeout(self) -> bool:
        return self.CLICKHOUSE_QUERY_TIMEOUT_SECONDS is None

    @property
    def effective_tool_result_max_chars(self) -> int:
        return (
            self.TOOL_RESULT_MAX_CHARS
            if self.TOOL_RESULT_MAX_CHARS is not None
            else self.TOOL_RESPONSE_MAX_CHARS
        )

    @property
    def using_legacy_tool_result_budget(self) -> bool:
        return self.TOOL_RESULT_MAX_CHARS is None

    @property
    def effective_summary_max_chars(self) -> int:
        return int(
            self.effective_tool_result_max_chars * self.TOOL_SUMMARY_BUDGET_RATIO
        )



settings = Settings()
