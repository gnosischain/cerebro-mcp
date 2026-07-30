"""`system_status` must make a disabled subsystem legible.

The whole point of the Semantic Layer section is that its absence is otherwise
SILENT. With `SEMANTIC_ENABLED` off, `SemanticLoader.load()` returns None, every
semantic tool answers "Semantic snapshot unavailable", `search_graph_catalog`
returns zero rows and the Graph Explorer draws no relationships — while
ClickHouse is connected, the manifest is loaded and the docs index is loaded,
because those use different loaders that the flag does not gate.

A deployment that merely forgot the environment variable therefore looked
identical to a healthy one in every line of this report. Diagnosing it took an
A/B against a second server. See ``default-off-flag-fails-silently``.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.config import settings


@pytest.fixture
def status_text(monkeypatch):
    """Render the status report with the flag forced to a given value."""

    def render(enabled: bool) -> str:
        monkeypatch.setattr(settings, "SEMANTIC_ENABLED", enabled, raising=False)
        from mcp.server.fastmcp import FastMCP

        from cerebro_mcp.tools.analytics import metadata

        # A ClickHouse stub is enough: the Semantic Layer section reads settings
        # and the snapshot, never the database. The connectivity section degrades
        # on its own, which is exactly the "everything else looks fine" backdrop
        # this report has to cut through.
        class _CH:
            def __getattr__(self, _name):
                def _fail(*a, **kw):
                    raise RuntimeError("no clickhouse in this test")
                return _fail

        mcp = FastMCP("status-test")
        metadata.register_metadata_tools(mcp, _CH())
        fn = None
        for tool in mcp._tool_manager._tools.values():
            if tool.name == "system_status":
                fn = tool.fn
                break
        assert fn is not None, "system_status is not registered"
        return fn()

    return render


def test_status_reports_the_semantic_flag_when_disabled(status_text):
    """The disabled branch must NAME the variable. A status line that says
    "unavailable" without saying which knob turns it on sends the reader to the
    code; this one is read by whoever is holding a broken deployment."""
    out = status_text(False)
    assert "## Semantic Layer" in out
    assert "SEMANTIC_ENABLED:** False" in out
    assert "SEMANTIC_ENABLED=true" in out, "the fix must be named, not implied"
    # And it must say WHAT is off, so the reader connects it to the symptom they
    # actually have (an empty Graph Explorer) rather than to an abstract flag.
    lowered = out.lower()
    assert "graph" in lowered and "relationship" in lowered


def test_status_reports_the_semantic_flag_when_enabled(status_text):
    out = status_text(True)
    assert "## Semantic Layer" in out
    assert "SEMANTIC_ENABLED:** True" in out
    # Enabled is not the same as loaded — the artifact can still fail to fetch,
    # which is the OTHER way this subsystem goes quiet (a swallowed loader error).
    assert "loaded" in out.lower()


def test_status_distinguishes_disabled_from_failed_to_load(status_text):
    """Two different faults, two different fixes: set the variable, or go look at
    why the artifact did not fetch. Collapsing them into one message is what makes
    the second one take a session to find."""
    disabled = status_text(False)
    enabled = status_text(True)
    assert "DISABLED" in disabled
    assert "DISABLED" not in enabled


def test_status_prints_both_artifact_sources(status_text):
    """The registry and the graph catalog are separate artifacts. Printing only
    one leaves the other's misconfiguration invisible."""
    out = status_text(True)
    assert "Registry source:" in out
    assert "Graph catalog source:" in out


def test_the_other_health_sections_still_render(status_text):
    """Guards the insertion point: the Semantic Layer section sits between Docs
    Index and Config, and a malformed edit there would truncate the report
    without failing anything else."""
    out = status_text(False)
    for section in ("## ClickHouse Connectivity", "## Manifest", "## Docs Index",
                    "## Semantic Layer", "## Event Store", "## Config"):
        assert section in out, f"{section} missing from the status report"
    assert out.index("## Docs Index") < out.index("## Semantic Layer")
    assert out.index("## Semantic Layer") < out.index("## Event Store")
    assert out.index("## Event Store") < out.index("## Config")


# --- Event store ---------------------------------------------------------
#
# Same failure class as the semantic flag, one layer down. Event-log writes are
# deliberately silenced so observability never breaks a tool — which also meant
# a wedged or unwritable store looked exactly like a healthy one. It stranded a
# storyteller pipeline that had passed every gate; see
# tests/test_event_store_write_deadline.py.


def test_status_reports_a_healthy_event_store(status_text, tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "EVENT_STORE_PATH", str(tmp_path / "state.db"), raising=False
    )
    from cerebro_mcp.workflow import event_store_sync as es

    es._reset_write_state()
    out = status_text(False)
    assert "## Event Store" in out
    assert "**Writable:** yes" in out
    assert "**State:** healthy" in out


def test_status_reports_an_unwritable_event_store_path(status_text, monkeypatch):
    """Must name the variable, like the semantic branch does — the reader is
    holding a broken deployment, not browsing."""
    monkeypatch.setattr(
        settings,
        "EVENT_STORE_PATH",
        "/proc/definitely-not-writable/state.db",
        raising=False,
    )
    from cerebro_mcp.workflow import event_store_sync as es

    es._reset_write_state()
    out = status_text(False)
    assert "**Writable:** NO" in out
    assert "EVENT_STORE_PATH" in out


def test_status_reports_a_degraded_event_store(status_text, tmp_path, monkeypatch):
    """A store that timed out and paused writes must say so. This is the state
    the stranded pipeline was in, with nothing anywhere reporting it."""
    import time

    monkeypatch.setattr(
        settings, "EVENT_STORE_PATH", str(tmp_path / "state.db"), raising=False
    )
    monkeypatch.setattr(
        settings, "EVENT_STORE_WRITE_TIMEOUT_SECONDS", 0.2, raising=False
    )
    monkeypatch.setattr(
        settings, "EVENT_STORE_DEGRADED_COOLDOWN_SECONDS", 30.0, raising=False
    )
    from cerebro_mcp.workflow import event_store_sync as es

    es._reset_write_state()
    es._reset_bootstrap_cache()
    es.create_workflow_safe("wf", "storyteller")
    monkeypatch.setattr(es, "_connect", lambda: time.sleep(30))
    es.append_event_safe("wf", "phase_advanced", {})

    out = status_text(False)
    assert "**State:** DEGRADED" in out
    assert "deadline" in out

    es._reset_write_state()
