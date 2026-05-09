from typing import Optional
from pydantic import ConfigDict
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
    SEMANTIC_REFRESH_INTERVAL_SECONDS: int = 300

    # Dashboard builder
    DASHBOARD_BUILDER_ENABLED: bool = False
    METRICS_DASHBOARD_PATH: str = ""

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

    # Thinking / performance tracing
    THINKING_MODE_ENABLED: bool = True
    THINKING_ALWAYS_ON: bool = True
    THINKING_LOG_DIR: str = ".cerebro/logs"
    THINKING_LOG_RETENTION_DAYS: int = 30

    # Databases accessible via the MCP server
    ALLOWED_DATABASES: list[str] = [
        "execution",
        "consensus",
        "crawlers_data",
        "nebula",
        "nebula_discv4",
        "dbt",
    ]

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
