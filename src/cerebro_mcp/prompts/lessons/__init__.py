"""Lesson records for THIS repo's own development.

Package data, deliberately: `docs/` ships in neither the wheel
(`pyproject.toml` -> `packages = ["src/cerebro_mcp"]`) nor the image (the
Dockerfile copies only `pyproject.toml`, `src/` and the built UI), so a store
under `docs/` would be invisible to a deployed server. Living here means the
records travel with the code and are read the same way the persona prompts are
(`importlib.resources`), with no artifact to build and no staleness window.

Not to be confused with the dbt-cerebro lesson corpus, which is a REMOTE
artifact fetched over HTTP and served by the same MCP (see
`cerebro_mcp/loaders/agent_context.py`). That one describes dbt models; these
describe cerebro-mcp itself.
"""
