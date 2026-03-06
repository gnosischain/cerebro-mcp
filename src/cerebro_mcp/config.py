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

    # dbt manifest source (URL takes precedence over local path)
    DBT_MANIFEST_URL: Optional[str] = (
        "https://gnosischain.github.io/dbt-cerebro/manifest.json"
    )
    DBT_MANIFEST_PATH: str = ""

    # Safety limits
    MAX_ROWS: int = 10000
    QUERY_TIMEOUT_SECONDS: int = 30
    MAX_QUERY_LENGTH: int = 10000

    # Databases accessible via the MCP server
    ALLOWED_DATABASES: list[str] = [
        "execution",
        "consensus",
        "crawlers_data",
        "nebula",
        "dbt",
    ]


settings = Settings()
