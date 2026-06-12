import pytest

from cerebro_mcp.config import Settings


def test_research_dir_defaults_to_local_path(monkeypatch):
    monkeypatch.delenv("CEREBRO_RESEARCH_DIR", raising=False)
    monkeypatch.delenv("DOCS_BASE_URL", raising=False)
    monkeypatch.delenv("GNOSIS_CHAIN_DOCS_LLM_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.CEREBRO_RESEARCH_DIR == ".cerebro/research_projects"
    assert settings.DOCS_BASE_URL == "https://docs.analytics.gnosis.io/"
    assert settings.GNOSIS_CHAIN_DOCS_LLM_URL == "https://docs.gnosischain.com/llms.txt"


def test_research_dir_env_override_wins(monkeypatch):
    monkeypatch.setenv("CEREBRO_RESEARCH_DIR", "/data/research_projects")
    monkeypatch.setenv("DOCS_BASE_URL", "https://preview.docs.analytics.gnosis.io/")
    monkeypatch.setenv("GNOSIS_CHAIN_DOCS_LLM_URL", "https://docs.gnosischain.com/llms.txt")

    settings = Settings(_env_file=None)

    assert settings.CEREBRO_RESEARCH_DIR == "/data/research_projects"
    assert settings.DOCS_BASE_URL == "https://preview.docs.analytics.gnosis.io/"
    assert settings.GNOSIS_CHAIN_DOCS_LLM_URL == "https://docs.gnosischain.com/llms.txt"


def test_rpc_scan_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RPC_SCAN_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.RPC_SCAN_ENABLED is False
    assert "scratch" not in settings.ALLOWED_DATABASES


def test_rpc_scan_enabled_allows_scratch_database(monkeypatch):
    monkeypatch.delenv("RPC_SCAN_SCRATCH_DATABASE", raising=False)
    settings = Settings(_env_file=None, RPC_SCAN_ENABLED=True)
    assert settings.ALLOWED_DATABASES[-1] == "scratch"
    # Idempotent: an env-supplied list that already has it isn't duplicated.
    settings2 = Settings(
        _env_file=None, RPC_SCAN_ENABLED=True,
        ALLOWED_DATABASES=["dbt", "scratch"],
    )
    assert settings2.ALLOWED_DATABASES.count("scratch") == 1


def test_rpc_scan_invalid_scratch_database_rejected():
    with pytest.raises(Exception, match="plain identifier"):
        Settings(
            _env_file=None, RPC_SCAN_ENABLED=True,
            RPC_SCAN_SCRATCH_DATABASE="bad name; DROP",
        )


def test_rpc_scan_defaults(monkeypatch):
    for key in ("RPC_SCAN_ADDRESS_BATCH", "RPC_SCAN_MULTICALL_BATCH"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.RPC_SCAN_ADDRESS_BATCH == 600
    assert settings.RPC_SCAN_MULTICALL_BATCH == 600
    assert settings.RPC_SCAN_TRACE_BLOCKS_PER_CALL == 100
    assert settings.RPC_SCAN_SYNC_WAIT_MAX_SECONDS == 25
    assert settings.RPC_SCAN_MAX_INLINE_ADDRESSES == 500
