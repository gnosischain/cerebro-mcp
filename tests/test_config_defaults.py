from cerebro_mcp.config import Settings


def test_research_dir_defaults_to_local_path(monkeypatch):
    monkeypatch.delenv("CEREBRO_RESEARCH_DIR", raising=False)

    settings = Settings(_env_file=None)

    assert settings.CEREBRO_RESEARCH_DIR == ".cerebro/research_projects"


def test_research_dir_env_override_wins(monkeypatch):
    monkeypatch.setenv("CEREBRO_RESEARCH_DIR", "/data/research_projects")

    settings = Settings(_env_file=None)

    assert settings.CEREBRO_RESEARCH_DIR == "/data/research_projects"
