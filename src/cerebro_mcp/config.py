from typing import Optional
from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

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
    DOCS_SEARCH_INDEX_URL: Optional[str] = (
        "https://docs.analytics.gnosis.io/search/search_index.json"
    )
    DOCS_SEARCH_INDEX_PATH: str = ""
    DOCS_REFRESH_INTERVAL_SECONDS: int = 3600

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

    # Manifest refresh
    MANIFEST_REFRESH_INTERVAL_SECONDS: int = 300

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
