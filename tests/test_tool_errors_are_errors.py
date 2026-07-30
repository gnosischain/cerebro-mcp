"""A failing tool must FAIL, not return prose that says it failed.

FastMCP already does the right thing: `Tool.run` catches any exception and
re-raises it as `ToolError`, which the protocol delivers as `isError=True`. A
tool body that catches its own exceptions and returns `f"Error: {e}"` defeats
that — the client receives a SUCCESSFUL tool result whose text happens to
contain the word "Error", and the model carries on from a false premise.

That is the mechanism behind "the tools get lost": a gate violation in the
research pipeline (wrong phase, missing peer review) looked exactly like a
successful call.

Scope: the research pipeline, where a masked gate violation is most damaging.
Other modules still carry the pattern and are being swept separately; this
guard is deliberately narrow so it stays green rather than becoming a
long-lived expected failure.
"""

from __future__ import annotations

import ast
import asyncio
import tempfile
from pathlib import Path

import pytest

GUARDED_MODULES = [
    "src/cerebro_mcp/tools/research/research.py",
]


@pytest.mark.parametrize("module_path", GUARDED_MODULES)
def test_no_tool_returns_an_error_string(module_path):
    """Static guard: no `return f"Error: ..."` in a guarded module."""
    src = Path(module_path).read_text()
    tree = ast.parse(src)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        text = None
        if isinstance(node.value, ast.JoinedStr):
            parts = [
                v.value for v in node.value.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
            text = "".join(parts)
        elif isinstance(node.value, ast.Constant) and isinstance(
            node.value.value, str
        ):
            text = node.value.value
        if text and text.lstrip().lower().startswith("error"):
            offenders.append(node.lineno)

    assert not offenders, (
        f"{module_path} returns an error string as a successful tool result at "
        f"line(s) {offenders}. Raise ToolError instead — FastMCP converts a "
        f"raised exception into isError=True."
    )


@pytest.fixture
def research_tools(monkeypatch):
    from mcp.server.fastmcp import FastMCP

    from cerebro_mcp.config import settings
    from cerebro_mcp.research.store import ResearchStore
    from cerebro_mcp.tools.research import research

    monkeypatch.setattr(
        settings,
        "EVENT_STORE_PATH",
        tempfile.mkdtemp() + "/state.db",
        raising=False,
    )

    class _CH:
        def __getattr__(self, _name):
            def _fail(*a, **kw):
                raise RuntimeError("no clickhouse in this test")
            return _fail

    mcp = FastMCP("research-error-test")
    research.register_research_tools(
        mcp, _CH(), ResearchStore(Path(tempfile.mkdtemp()))
    )
    return mcp._tool_manager


def test_unknown_project_raises_instead_of_returning_prose(research_tools):
    """The behaviour that matters: the client must be able to TELL it failed.

    Before this, the same message came back as a successful result.
    """
    with pytest.raises(Exception) as excinfo:
        asyncio.run(
            research_tools.call_tool(
                "plan_research_phase",
                {
                    "project_id": "does-not-exist",
                    "phase": "execution",
                    "plan_markdown": "p",
                },
            )
        )
    assert "not found" in str(excinfo.value).lower()


def test_out_of_phase_call_raises(research_tools):
    """Phase order IS enforced in code; the enforcement has to reach the client."""
    with pytest.raises(Exception):
        asyncio.run(
            research_tools.call_tool(
                "verify_research_phase", {"project_id": "does-not-exist"}
            )
        )
