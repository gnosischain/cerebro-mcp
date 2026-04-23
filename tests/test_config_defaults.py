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
