"""Tests for the security hardening module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cerebro_mcp.security import (
    TOOL_RISK_REGISTRY,
    RiskClass,
    _canonical_hash,
    assess_tool_call,
    detect_suspicious_flags,
    get_risk_classes,
    primary_risk_class,
    write_audit_event,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _security_tmp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect security audit logs to a temp directory and reset module state."""
    import cerebro_mcp.security as sec

    monkeypatch.setattr(
        "cerebro_mcp.config.settings.MCP_SECURITY_LOG_DIR", str(tmp_path)
    )
    # Reset module-level file handle state
    monkeypatch.setattr(sec, "_audit_file_handle", None)
    monkeypatch.setattr(sec, "_audit_current_date", "")
    yield tmp_path


# ---------------------------------------------------------------------------
# Risk registry tests
# ---------------------------------------------------------------------------

class TestRiskRegistry:
    def test_known_tools_have_risk_classes(self):
        for tool_name, risk_classes in TOOL_RISK_REGISTRY.items():
            assert len(risk_classes) > 0, f"{tool_name} has empty risk classes"
            for rc in risk_classes:
                assert isinstance(rc, RiskClass), (
                    f"{tool_name} has non-RiskClass: {rc}"
                )

    def test_unknown_tool_defaults_to_read_only(self):
        assert get_risk_classes("nonexistent_tool_xyz") == frozenset(
            {RiskClass.READ_ONLY}
        )

    def test_scaffold_has_workspace_and_subprocess(self):
        classes = get_risk_classes("scaffold_dashboard_tab")
        assert RiskClass.WORKSPACE_WRITE in classes
        assert RiskClass.SUBPROCESS in classes

    def test_app_only_tools(self):
        for name in ("get_mini_app_rows", "get_mini_app_state"):
            classes = get_risk_classes(name)
            assert RiskClass.APP_ONLY in classes, f"{name} not app_only"

    def test_save_query_is_server_state_write(self):
        assert RiskClass.SERVER_STATE_WRITE in get_risk_classes("save_query")

    def test_execute_query_is_read_only(self):
        assert get_risk_classes("execute_query") == frozenset({RiskClass.READ_ONLY})

    def test_generate_report_is_server_state_write(self):
        assert RiskClass.SERVER_STATE_WRITE in get_risk_classes("generate_report")

    def test_storyteller_tools_are_server_state_write(self):
        for name in (
            "storyteller_start_session",
            "storyteller_record_context_brief",
            "storyteller_generate_story_report",
        ):
            assert RiskClass.SERVER_STATE_WRITE in get_risk_classes(name), (
                f"{name} not server_state_write"
            )

    def test_research_tools_are_server_state_write(self):
        for name in (
            "start_research_project",
            "record_research_finding",
            "publish_research_report",
        ):
            assert RiskClass.SERVER_STATE_WRITE in get_risk_classes(name), (
                f"{name} not server_state_write"
            )


class TestPrimaryRiskClass:
    def test_scaffold_returns_subprocess(self):
        assert primary_risk_class("scaffold_dashboard_tab") == RiskClass.SUBPROCESS

    def test_app_only_returns_app_only(self):
        assert primary_risk_class("get_mini_app_rows") == RiskClass.APP_ONLY

    def test_server_write_returns_server_state_write(self):
        assert primary_risk_class("save_query") == RiskClass.SERVER_STATE_WRITE

    def test_read_only_returns_read_only(self):
        assert primary_risk_class("execute_query") == RiskClass.READ_ONLY

    def test_unknown_returns_read_only(self):
        assert primary_risk_class("unknown_tool_abc") == RiskClass.READ_ONLY


# ---------------------------------------------------------------------------
# Suspicious flag tests
# ---------------------------------------------------------------------------

class TestSuspiciousFlags:
    def test_app_only_always_flagged_stdio(self):
        flags = detect_suspicious_flags(
            "get_mini_app_rows",
            frozenset({RiskClass.APP_ONLY}),
            "stdio",
        )
        assert "app_only_tool_called" in flags

    def test_app_only_always_flagged_sse(self):
        flags = detect_suspicious_flags(
            "get_mini_app_rows",
            frozenset({RiskClass.APP_ONLY}),
            "sse",
        )
        assert "app_only_tool_called" in flags

    def test_workspace_write_via_sse_flagged(self):
        flags = detect_suspicious_flags(
            "scaffold_dashboard_tab",
            frozenset({RiskClass.WORKSPACE_WRITE, RiskClass.SUBPROCESS}),
            "sse",
        )
        assert "workspace_write_via_sse" in flags

    def test_workspace_write_via_stdio_not_flagged(self):
        flags = detect_suspicious_flags(
            "scaffold_dashboard_tab",
            frozenset({RiskClass.WORKSPACE_WRITE, RiskClass.SUBPROCESS}),
            "stdio",
        )
        assert "workspace_write_via_sse" not in flags

    def test_unknown_tool_flagged(self):
        flags = detect_suspicious_flags(
            "not_a_real_tool",
            frozenset({RiskClass.READ_ONLY}),
            "stdio",
        )
        assert "unknown_tool" in flags

    def test_read_only_stdio_no_flags(self):
        flags = detect_suspicious_flags(
            "execute_query",
            frozenset({RiskClass.READ_ONLY}),
            "stdio",
        )
        assert flags == []


# ---------------------------------------------------------------------------
# Canonical hashing tests
# ---------------------------------------------------------------------------

class TestCanonicalHash:
    def test_deterministic(self):
        payload = {"sql": "SELECT 1", "database": "dbt"}
        h1 = _canonical_hash(payload)
        h2 = _canonical_hash(payload)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_redacts_sensitive_before_hashing(self):
        """Same hash regardless of actual secret value."""
        h1 = _canonical_hash({"api_key": "secret_a", "sql": "SELECT 1"})
        h2 = _canonical_hash({"api_key": "secret_b", "sql": "SELECT 1"})
        assert h1 == h2

    def test_different_payloads_different_hash(self):
        h1 = _canonical_hash({"sql": "SELECT 1"})
        h2 = _canonical_hash({"sql": "SELECT 2"})
        assert h1 != h2

    def test_empty_payload(self):
        h = _canonical_hash({})
        assert len(h) == 64


# ---------------------------------------------------------------------------
# Audit writer tests
# ---------------------------------------------------------------------------

class TestAuditWriter:
    def test_creates_daily_file(self, _security_tmp_dir: Path):
        event = {"tool_name": "test_tool", "success": True}
        write_audit_event(event)

        files = list(_security_tmp_dir.glob("security_audit_*.jsonl"))
        assert len(files) == 1
        assert files[0].name.startswith("security_audit_")
        assert files[0].name.endswith(".jsonl")

    def test_parseable_json(self, _security_tmp_dir: Path):
        event = {"tool_name": "test_tool", "flags": ["a", "b"]}
        write_audit_event(event)

        files = list(_security_tmp_dir.glob("security_audit_*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["tool_name"] == "test_tool"
        assert parsed["flags"] == ["a", "b"]

    def test_multiple_events(self, _security_tmp_dir: Path):
        for i in range(5):
            write_audit_event({"index": i})

        files = list(_security_tmp_dir.glob("security_audit_*.jsonl"))
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 5

    def test_all_expected_fields(self, _security_tmp_dir: Path):
        event = {
            "timestamp": "2026-04-10T00:00:00+00:00",
            "transport": "stdio",
            "auth_present": False,
            "tool_name": "execute_query",
            "risk_class": "read_only",
            "visibility": "public",
            "redacted_arg_summary": '{"sql":"SELECT 1"}',
            "arg_hash": "abc123",
            "result_hash": "def456",
            "duration_ms": 42,
            "success": True,
            "suspicious_flags": [],
        }
        write_audit_event(event)

        files = list(_security_tmp_dir.glob("security_audit_*.jsonl"))
        parsed = json.loads(files[0].read_text().strip())
        for key in event:
            assert key in parsed, f"Missing field: {key}"


# ---------------------------------------------------------------------------
# Integration: assess_tool_call
# ---------------------------------------------------------------------------

class TestAssessToolCall:
    def test_writes_audit_event(
        self, _security_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("CEREBRO_TRANSPORT", "stdio")
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)

        assess_tool_call(
            tool_name="execute_query",
            arguments={"sql": "SELECT 1", "database": "dbt"},
            result="some result",
            success=True,
            duration_ms=100,
        )

        files = list(_security_tmp_dir.glob("security_audit_*.jsonl"))
        assert len(files) == 1
        parsed = json.loads(files[0].read_text().strip())
        assert parsed["tool_name"] == "execute_query"
        assert parsed["risk_class"] == "read_only"
        assert parsed["success"] is True
        assert parsed["suspicious_flags"] == []
        assert len(parsed["arg_hash"]) == 64

    def test_flags_app_only_tool(
        self, _security_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("CEREBRO_TRANSPORT", "stdio")

        assess_tool_call(
            tool_name="get_mini_app_rows",
            arguments={"view_id": "v1", "dataset_key": "dk"},
            result=None,
            success=True,
            duration_ms=10,
        )

        files = list(_security_tmp_dir.glob("security_audit_*.jsonl"))
        parsed = json.loads(files[0].read_text().strip())
        assert "app_only_tool_called" in parsed["suspicious_flags"]
        assert parsed["risk_class"] == "app_only"

    def test_flags_unknown_tool(
        self, _security_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("CEREBRO_TRANSPORT", "stdio")

        assess_tool_call(
            tool_name="definitely_not_registered",
            arguments={},
            result=None,
            success=True,
            duration_ms=1,
        )

        files = list(_security_tmp_dir.glob("security_audit_*.jsonl"))
        parsed = json.loads(files[0].read_text().strip())
        assert "unknown_tool" in parsed["suspicious_flags"]

    def test_high_risk_tool_records_error(
        self, _security_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("CEREBRO_TRANSPORT", "sse")

        assess_tool_call(
            tool_name="scaffold_dashboard_tab",
            arguments={"blueprint_json": "{}"},
            result=None,
            success=False,
            duration_ms=50,
            error="Feature disabled",
        )

        files = list(_security_tmp_dir.glob("security_audit_*.jsonl"))
        parsed = json.loads(files[0].read_text().strip())
        assert parsed["risk_class"] == "subprocess"
        assert parsed["success"] is False
        assert parsed["error"] == "Feature disabled"
        assert "workspace_write_via_sse" in parsed["suspicious_flags"]

    @patch("cerebro_mcp.observability.observe_security_high_risk_call")
    @patch("cerebro_mcp.observability.observe_security_suspicious_call")
    @patch("cerebro_mcp.observability.observe_security_app_only_call")
    def test_increments_prometheus_counters(
        self,
        mock_app_only,
        mock_suspicious,
        mock_high_risk,
        _security_tmp_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("CEREBRO_TRANSPORT", "sse")

        assess_tool_call(
            tool_name="get_mini_app_rows",
            arguments={"view_id": "v1", "dataset_key": "dk"},
            result=None,
            success=True,
            duration_ms=5,
        )

        # app_only is high-risk (not read_only), so high_risk counter fires
        mock_high_risk.assert_called_once()
        # app_only flag detected
        mock_suspicious.assert_called()
        flag_types = [
            call.kwargs["flag_type"] for call in mock_suspicious.call_args_list
        ]
        assert "app_only_tool_called" in flag_types
        # app_only counter fires
        mock_app_only.assert_called_once_with(
            tool_name="get_mini_app_rows", transport="sse"
        )
