from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from mcp.server.fastmcp import FastMCP

from cerebro_mcp.clients.clickhouse import ExecutedQuery
from cerebro_mcp.prompts.templates import register_prompts
from cerebro_mcp.models.research import (
    EvidenceRef,
    PeerReviewResult,
    ResearchMemoryEntry,
)
from cerebro_mcp.research.store import ResearchStore
from cerebro_mcp.models.tool import QueryResult
from cerebro_mcp.tools.research import research as research_mod
from cerebro_mcp.tools.analytics.query import register_query_tools
from cerebro_mcp.tools.research.research import register_research_tools
from cerebro_mcp.tools.governance.session_state import state


def _fake_query_result() -> QueryResult:
    return QueryResult(
        sql="SELECT number FROM numbers(3)",
        database="dbt",
        columns=["number"],
        rows=[[0], [1]],
        row_count=3,
        rows_returned=2,
        truncated=True,
        fetch_mode="rows",
        elapsed_seconds=0.1,
        warnings=[],
        summary_markdown="preview",
    )


def test_execute_query_persists_research_snapshot_without_session_nudges(tmp_path):
    store = ResearchStore(str(tmp_path))
    project = store.create_project("Test hypothesis", "Test scope")

    mcp = FastMCP("research-query")
    ch = MagicMock()
    ch.run_query.return_value = ExecutedQuery(
        sql="SELECT number FROM numbers(3)",
        executed_sql="SELECT number FROM numbers(3) LIMIT 3",
        database="dbt",
        columns=["number"],
        rows=[[0], [1], [2]],
        row_count=3,
        elapsed_seconds=0.1,
        fetch_mode="rows",
        warnings=[],
    )
    ch.build_query_result.return_value = _fake_query_result()
    register_query_tools(mcp, ch, store)

    state.reset()
    fn = mcp._tool_manager._tools["execute_query"].fn
    result = fn(
        sql="SELECT number FROM numbers(3)",
        research_project_id=project.project_id,
        persist_result=True,
        max_rows=2,
    )

    assert isinstance(result, QueryResult)
    assert result.result_ref_id is not None
    assert store.artifact_exists(project.project_id, "query_result", result.result_ref_id)
    assert state.execute_query_count == 0


def test_research_store_lock_preserves_parallel_memory_writes(tmp_path):
    store = ResearchStore(str(tmp_path))
    project = store.create_project("Test hypothesis", "Test scope")

    def add_entry(idx: int) -> None:
        store.append_memory(
            project.project_id,
            ResearchMemoryEntry(
                id=f"mem_{idx}",
                kind="note",
                statement=f"entry {idx}",
            ),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(add_entry, range(6)))

    memory = store.list_memory(project.project_id)
    assert len(memory) == 6


def test_research_workflow_lifecycle_and_pagination(tmp_path, monkeypatch):
    store = ResearchStore(str(tmp_path))
    mcp = FastMCP("research-tools")
    ch = MagicMock()
    register_research_tools(mcp, ch, store)

    start = mcp._tool_manager._tools["start_research_project"].fn
    plan = mcp._tool_manager._tools["plan_research_phase"].fn
    execute = mcp._tool_manager._tools["execute_research_phase"].fn
    attach = mcp._tool_manager._tools["attach_research_evidence"].fn
    get_evidence = mcp._tool_manager._tools["get_research_evidence"].fn
    record_finding = mcp._tool_manager._tools["record_research_finding"].fn
    verify = mcp._tool_manager._tools["verify_research_phase"].fn
    prepare_review = mcp._tool_manager._tools["prepare_peer_review"].fn
    record_review = mcp._tool_manager._tools["record_peer_review"].fn
    publish = mcp._tool_manager._tools["publish_research_report"].fn
    summary = mcp._tool_manager._tools["get_research_project"].fn

    project = start("Quantiles explain validator churn", "Validator analysis")
    assert project.current_phase == "mapping"

    assert plan(project.project_id, "mapping", "Map the relevant tables.").status == "planned"
    assert execute(project.project_id, "mapping").current_phase == "hypothesis"
    assert plan(project.project_id, "hypothesis", "Define the hypothesis.").status == "planned"
    assert execute(project.project_id, "hypothesis").current_phase == "execution"
    assert plan(project.project_id, "execution", "Run the evidence queries.").status == "planned"
    assert execute(project.project_id, "execution").current_phase == "verification"

    ref_one = store.save_query_result_artifact(
        project_id=project.project_id,
        title="median check",
        sql="SELECT quantiles(0.5)(validator_count) FROM foo",
        database="dbt",
        columns=["p50"],
        rows=[[10]],
        row_count=1,
    )
    ref_two = store.save_query_result_artifact(
        project_id=project.project_id,
        title="secondary check",
        sql="SELECT quantiles(0.5)(validator_count) FROM foo WHERE day > today() - 7",
        database="dbt",
        columns=["p50"],
        rows=[[11]],
        row_count=1,
    )
    first_evidence = attach(project.project_id, "query_result", ref_one, "execution")
    second_evidence = attach(project.project_id, "query_result", ref_two, "execution")
    assert isinstance(first_evidence, EvidenceRef)
    assert isinstance(second_evidence, EvidenceRef)

    evidence_page = get_evidence(project.project_id, phase="execution", page_size=1)
    assert len(evidence_page.evidence) == 1
    assert evidence_page.next_page_token is not None
    next_page = get_evidence(
        project.project_id,
        phase="execution",
        page_size=1,
        page_token=evidence_page.next_page_token,
    )
    assert len(next_page.evidence) == 1

    finding = record_finding(
        project.project_id,
        "Median validator count is stable",
        "Median validator count stayed within expected bounds.",
        [first_evidence],
        0.8,
    )
    assert finding.title == "Median validator count is stable"

    verification = verify(project.project_id)
    assert verification.overall_status == "passed"

    packet = prepare_review(project.project_id)
    assert packet.project_id == project.project_id
    assert packet.findings

    review = record_review(
        project.project_id,
        PeerReviewResult(
            project_id=project.project_id,
            overall_decision="accepted",
            accepted_claims=["Median validator count stayed stable."],
        ),
    )
    assert review.overall_decision == "accepted"

    monkeypatch.setattr(
        research_mod,
        "create_report_artifact",
        lambda title, content_markdown, **_: {
            "report_id": "report-1234",
            "file_uri": "/tmp/report.html",
            "structured": {"title": title},
            "reply_text": "ok",
            "chart_count": 1,
        },
    )
    published = publish(
        project.project_id,
        "Validator churn study",
        "## Summary\n{{chart:chart_1}}",
    )
    assert published.report_id == "report-1234"

    project_summary = summary(project.project_id)
    assert project_summary.status == "completed"
    assert project_summary.current_phase == "publication"


def test_conduct_research_peer_review_prompt_injects_schema():
    mcp = FastMCP("research-prompts")
    register_prompts(mcp)

    fn = mcp._prompt_manager._prompts["conduct_research_peer_review"].fn
    messages = fn('{"project_id":"rp_123"}')
    text = messages[0].content.text

    assert len(messages) == 1
    assert "record_peer_review" in text
    assert "overall_decision" in text
