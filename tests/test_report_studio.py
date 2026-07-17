"""Tests for Report Studio backend helpers (Phase 0: resolver, serializer,
atomic writes, chart-record TTL). The tool suite grows here with B1/B2."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta

import pytest

from cerebro_mcp.tools.visualization import charts


def _make_report(
    report_dir,
    *,
    kind: str = "report",
    slug: str = "test-report",
    title: str = "Test Report",
    report_id: str | None = None,
    ts: str = "20260716T000000Z",
) -> tuple[str, "os.PathLike"]:
    rid = report_id or str(uuid.uuid4())
    prefix = {
        "report": "cerebro_report",
        "research": "cerebro_research",
        "case_study": "cerebro_case_study",
    }[kind]
    path = report_dir / f"{prefix}_{ts}_{slug}_{rid}.html"
    blob = charts._serialize_report_data(
        {"title": title, "timestamp": ts, "charts": {}, "sections_html": "<p>hi</p>"}
    )
    path.write_text(
        "<!DOCTYPE html><html><body><div id='root'></div>"
        f'<script id="report-data" type="application/json">{blob}</script>'
        "</body></html>",
        encoding="utf-8",
    )
    return rid, path


@pytest.fixture
def report_dir(tmp_path, monkeypatch):
    d = tmp_path / "reports"
    d.mkdir()
    monkeypatch.setenv("CEREBRO_REPORT_DIR", str(d))
    yield d


# ---------------------------------------------------------------------------
# resolve_report_id
# ---------------------------------------------------------------------------


def test_resolve_full_uuid(report_dir):
    rid, path = _make_report(report_dir)
    got_id, got_path = charts.resolve_report_id(rid)
    assert got_id == rid
    assert got_path == path


def test_resolve_short_prefix(report_dir):
    rid, path = _make_report(report_dir)
    got_id, got_path = charts.resolve_report_id(rid[:8])
    assert got_id == rid
    assert got_path == path


def test_resolve_hyphen_stripped_prefix(report_dir):
    rid, _ = _make_report(report_dir)
    # 10 chars spanning the first hyphen of the UUID
    ref = rid.replace("-", "")[:10]
    got_id, _ = charts.resolve_report_id(ref)
    assert got_id == rid


def test_resolve_ambiguous_prefix_lists_candidates(report_dir):
    shared = "abcdef12"
    rid_a = shared + "-0000-4000-8000-000000000001"
    rid_b = shared + "-0000-4000-8000-000000000002"
    _make_report(report_dir, report_id=rid_a, slug="one")
    _make_report(report_dir, report_id=rid_b, slug="two")
    with pytest.raises(charts.ReportRefError) as exc:
        charts.resolve_report_id(shared)
    assert sorted(exc.value.candidates) == sorted([rid_a, rid_b])


def test_resolve_not_found(report_dir):
    _make_report(report_dir)
    with pytest.raises(charts.ReportRefError, match="No report matches"):
        charts.resolve_report_id("deadbeef" * 2)


@pytest.mark.parametrize(
    "bad_ref",
    ["", "short", "*", "../../etc/passwd", "cerebro_report_*", "zzzzzzzz", "abc?1234"],
)
def test_resolve_rejects_invalid_refs(report_dir, bad_ref):
    _make_report(report_dir)
    with pytest.raises(charts.ReportRefError):
        charts.resolve_report_id(bad_ref)


def test_resolve_excludes_symlinks(report_dir, tmp_path):
    rid, path = _make_report(report_dir)
    # A symlink named like a valid report pointing outside the dir must be
    # invisible to the resolver (and thus to preview/delete/rename).
    outside = tmp_path / "outside.html"
    outside.write_text("secret", encoding="utf-8")
    link_id = str(uuid.uuid4())
    link = report_dir / f"cerebro_report_20260716T000000Z_evil_{link_id}.html"
    link.symlink_to(outside)
    with pytest.raises(charts.ReportRefError):
        charts.resolve_report_id(link_id)
    # The real report still resolves.
    assert charts.resolve_report_id(rid)[0] == rid


def test_is_managed_report_file_rejects_outsiders(report_dir, tmp_path):
    outside = tmp_path / f"cerebro_report_20260716T000000Z_x_{uuid.uuid4()}.html"
    outside.write_text("x", encoding="utf-8")
    assert charts._is_managed_report_file(outside) is False


def test_is_managed_report_file_rejects_non_uuid_names(report_dir):
    junk = report_dir / "cerebro_report_20260716T000000Z_x_notauuid.html"
    junk.write_text("x", encoding="utf-8")
    assert charts._is_managed_report_file(junk) is False


# ---------------------------------------------------------------------------
# Script-safe serialization + shared report-data regex
# ---------------------------------------------------------------------------


def test_serialize_report_data_escapes_script_close():
    hostile = '</script><script>alert(1)</script>'
    blob = charts._serialize_report_data({"title": hostile})
    assert "</script>" not in blob
    assert json.loads(blob) == {"title": hostile}


def test_report_data_regex_extract_replace_roundtrip():
    original = {"title": "Before", "charts": {}, "sections_html": "<p>x</p>"}
    html = (
        "<html><body>"
        f'<script id="report-data" type="application/json">'
        f"{charts._serialize_report_data(original)}</script></body></html>"
    )
    assert charts._extract_structured_from_html(html) == original

    updated = dict(original, title='After </script> attack')
    new_blob = charts._serialize_report_data(updated)
    new_html, n = charts._REPORT_DATA_RE.subn(
        lambda m: m.group(1) + new_blob + m.group(3), html, count=1
    )
    assert n == 1
    assert charts._extract_structured_from_html(new_html) == updated


def test_build_standalone_html_survives_hostile_title():
    html = charts._build_standalone_html(
        'Evil </script><script>alert(1)</script>', "2026-07-16", {}, "<p>x</p>"
    )
    structured = charts._extract_structured_from_html(html)
    assert structured is not None
    assert structured["title"].startswith("Evil ")
    # The embedded blob must not contain a literal </script> terminator.
    m = charts._REPORT_DATA_RE.search(html)
    assert m and "</script>" not in m.group(2)


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


def test_atomic_write_report_success(report_dir):
    target = report_dir / f"cerebro_report_20260716T000000Z_a_{uuid.uuid4()}.html"
    charts._atomic_write_report(target, "<html>ok</html>")
    assert target.read_text(encoding="utf-8") == "<html>ok</html>"
    assert not list(report_dir.glob("*.tmp-*"))


def test_atomic_write_report_failure_leaves_nothing(report_dir, monkeypatch):
    target = report_dir / f"cerebro_report_20260716T000000Z_a_{uuid.uuid4()}.html"

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(charts.os, "replace", boom)
    with pytest.raises(OSError):
        charts._atomic_write_report(target, "<html>partial</html>")
    assert not target.exists()
    assert not list(report_dir.glob("*.tmp-*"))
    assert not list(report_dir.glob("*.html"))


# ---------------------------------------------------------------------------
# Chart registry TTL + snapshot
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_chart_registry():
    with charts._chart_lock:
        saved = dict(charts._chart_registry)
        charts._chart_registry.clear()
    yield
    with charts._chart_lock:
        charts._chart_registry.clear()
        charts._chart_registry.update(saved)


def test_get_chart_record_prunes_expired(clean_chart_registry):
    with charts._chart_lock:
        charts._chart_registry["chart_x"] = {
            "chart_id": "chart_x",
            "title": "Old",
            "chart_type": "line",
            "created_at": datetime.now() - charts._CHART_TTL - timedelta(minutes=1),
        }
    assert charts.get_chart_record("chart_x") is None


def test_list_chart_records_excludes_option(clean_chart_registry):
    with charts._chart_lock:
        charts._chart_registry["chart_y"] = {
            "chart_id": "chart_y",
            "title": "Fresh",
            "chart_type": "bar",
            "data_points": 12,
            "created_at": datetime.now(),
            "source": "generate_charts",
            "source_model": "api_foo",
            "option": {"huge": True},
        }
    records = charts.list_chart_records()
    assert len(records) == 1
    assert records[0]["chart_id"] == "chart_y"
    assert "option" not in records[0]


def test_forget_report_drops_cache_entry():
    rid = str(uuid.uuid4())
    with charts._REPORT_LOCK:
        charts._REPORT_CACHE[rid] = {
            "html": "<html/>",
            "structured": {},
            "expires": datetime.now(charts.timezone.utc) + timedelta(hours=1),
            "path": None,
            "title": "t",
        }
    charts.forget_report(rid)
    with charts._REPORT_LOCK:
        assert rid not in charts._REPORT_CACHE
    # No-op on absent ids.
    charts.forget_report(rid)


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "fallback", "expected"),
    [
        ("Bridges Netflow Q2 Deep Dive", "report", "bridges-netflow-q2"),
        ("!!!", "report", "report"),
        ("", "case_study", "case_study"),
        ("Sinal & Ruído", "report", "sinal-rudo"),
    ],
)
def test_slugify_title(title, fallback, expected):
    assert charts._slugify_title(title, fallback) == expected


def test_report_filename_uses_slug_helper():
    rid = str(uuid.uuid4())
    name = charts._report_filename(rid, "My Great Report Title", kind="case_study")
    assert name.startswith("cerebro_case_study_")
    assert name.endswith(f"_my-great-report_{rid}.html")
    # case_study stems have 6 "_" segments; the slug is parts[-2].
    parts = name[: -len(".html")].split("_")
    assert parts[-2] == "my-great-report"
    assert parts[-1] == rid


# ---------------------------------------------------------------------------
# Report Studio tools (B1a/B1b/B2)
# ---------------------------------------------------------------------------

import asyncio
import time
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

from cerebro_mcp.config import settings
from cerebro_mcp.tools.visualization import mini_apps, web_apps
from cerebro_mcp.tools.visualization.report_studio import (
    register_report_studio_tools,
)


@pytest.fixture
def studio(report_dir):
    mini_apps.reset_views_for_tests()
    server = FastMCP("test-studio")
    # Installs the app-only list_tools filter (same order as server.py).
    mini_apps.register_mini_app_infra(server, None)
    register_report_studio_tools(server, None)
    yield server
    mini_apps.reset_views_for_tests()


def _tool(server, name):
    return next(
        t.fn for t in server._tool_manager._tools.values() if t.name == name
    )


APP_ONLY_STUDIO_TOOLS = {
    "list_report_archive",
    "get_report_archive_entry",
    "get_report_export_info",
    "delete_report_archive_entry",
    "rename_report_archive_entry",
    "list_session_charts",
    "get_session_chart",
    "compose_report",
}


def test_studio_registration_and_visibility(studio):
    assert "report_studio" in web_apps.WEB_APP_CONFIGS
    cfg = web_apps.WEB_APP_CONFIGS["report_studio"]
    assert cfg.open_tool == "open_report_studio"
    assert APP_ONLY_STUDIO_TOOLS <= cfg.allowed_tools
    app_only = mini_apps.get_app_only_tool_names()
    assert APP_ONLY_STUDIO_TOOLS <= app_only
    names = [t.name for t in asyncio.run(studio.list_tools())]
    assert "open_report_studio" in names
    for hidden in APP_ONLY_STUDIO_TOOLS:
        assert hidden not in names, hidden


def test_open_report_studio_initial_load(studio, report_dir):
    rid, _ = _make_report(report_dir, title="Bridges Quarterly")
    open_fn = _tool(studio, "open_report_studio")
    result = open_fn()
    assert not result.isError
    sc = result.structuredContent
    assert sc["type"] == "INITIAL_LOAD"
    assert sc["app_id"] == "report_studio"
    assert sc["view_id"]  # create_view returns the id STRING
    vs = sc["view_state"]
    assert vs["screen"] == "archive"
    assert vs["archive"]["total"] == 1
    assert vs["archive"]["reports"][0]["id"] == rid
    assert vs["mutations_enabled"] is True
    assert vs["session_charts"] == {"charts": []}

    # deep link straight into the preview
    deep = open_fn(report=rid[:8]).structuredContent
    assert deep["view_state"]["screen"] == "preview"
    assert deep["view_state"]["selected_entry"]["title"] == "Bridges Quarterly"


def test_archive_list_filter_sort_and_clamps(studio, report_dir):
    r1, p1 = _make_report(report_dir, slug="alpha-report", ts="20260101T000000Z")
    time.sleep(0.02)
    r2, p2 = _make_report(
        report_dir, kind="research", slug="beta-essay", ts="20260201T000000Z"
    )
    time.sleep(0.02)
    r3, p3 = _make_report(
        report_dir, kind="case_study", slug="gamma-story", ts="20260301T000000Z"
    )
    list_fn = _tool(studio, "list_report_archive")

    newest = list_fn()
    assert [e["id"] for e in newest["reports"]] == [r3, r2, r1]
    assert newest["warning_count"] == 0

    oldest = list_fn(sort="oldest")
    assert [e["id"] for e in oldest["reports"]] == [r1, r2, r3]

    by_title = list_fn(sort="title")
    assert [e["title_hint"] for e in by_title["reports"]] == [
        "alpha report", "beta essay", "gamma story",
    ]

    research = list_fn(kind="research")
    assert [e["id"] for e in research["reports"]] == [r2]

    q = list_fn(query="gamma")
    assert [e["id"] for e in q["reports"]] == [r3]

    clamped = list_fn(limit=100000, offset=-5)
    assert clamped["limit"] == 200 and clamped["offset"] == 0

    paged = list_fn(limit=1, offset=1)
    assert paged["total"] == 3 and len(paged["reports"]) == 1


def test_archive_excludes_symlinks_and_missing_dir(studio, report_dir, tmp_path, monkeypatch):
    _make_report(report_dir)
    outside = tmp_path / "outside.html"
    outside.write_text("secret", encoding="utf-8")
    link = report_dir / f"cerebro_report_20260716T000000Z_evil_{uuid.uuid4()}.html"
    link.symlink_to(outside)
    list_fn = _tool(studio, "list_report_archive")
    assert list_fn()["total"] == 1  # symlink invisible

    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("CEREBRO_REPORT_DIR", str(missing))
    empty = list_fn()
    assert empty == {**empty, "reports": [], "total": 0}
    assert not missing.exists()  # read path must NOT mkdir


def test_get_entry_and_malformed_payload(studio, report_dir):
    rid, path = _make_report(report_dir, title="Full Title Beyond Slug Words")
    get_fn = _tool(studio, "get_report_archive_entry")
    entry = get_fn(report_ref=rid)
    assert entry["ok"] is True
    assert entry["title"] == "Full Title Beyond Slug Words"
    assert entry["kind"] == "report"
    assert entry["sections_html"] == "<p>hi</p>"
    assert entry["file"]["filename"] == path.name

    # malformed embedded JSON: still listed, preview errors structurally
    broken_id = str(uuid.uuid4())
    broken = report_dir / f"cerebro_report_20260716T000001Z_broken_{broken_id}.html"
    broken.write_text(
        '<html><body><script id="report-data" type="application/json">'
        "{not json}</script></body></html>",
        encoding="utf-8",
    )
    list_fn = _tool(studio, "list_report_archive")
    assert list_fn()["total"] == 2
    bad = get_fn(report_ref=broken_id)
    assert bad["ok"] is False and "unreadable" in bad["error"]

    ambiguous_or_missing = get_fn(report_ref="ffffffff")
    assert ambiguous_or_missing["ok"] is False


def test_export_info(studio, report_dir):
    rid, path = _make_report(report_dir)
    info = _tool(studio, "get_report_export_info")(report_ref=rid)
    assert info["ok"] is True
    assert info["path"] == str(path)
    assert "export_report" in info["hint"]
    assert info["download_url"] is None  # stdio/loopback -> no HTTP url


# --- B1b: delete + rename ---


def test_delete_confirm_roundtrip(studio, report_dir):
    rid, path = _make_report(report_dir)
    delete_fn = _tool(studio, "delete_report_archive_entry")

    first = delete_fn(report_ref=rid[:8])
    assert first == {
        "ok": False, "needs_confirm": True, "id": rid,
        "filename": path.name, "title_hint": first["title_hint"],
    }
    # UI confirms with the FULL resolved id
    second = delete_fn(report_ref=first["id"], confirm=True)
    assert second["ok"] is True and second["deleted"] == path.name
    assert not path.exists()

    gone = delete_fn(report_ref=rid, confirm=True)
    assert gone["ok"] is False and "No report matches" in gone["error"]


def test_rename_rewrites_title_slug_and_preserves_identity(studio, report_dir):
    rid, path = _make_report(
        report_dir, kind="case_study", slug="old-name", title="Old Name"
    )
    old_mtime = path.stat().st_mtime
    rename_fn = _tool(studio, "rename_report_archive_entry")

    result = rename_fn(report_ref=rid[:8], title="Brand New Story Title")
    assert result["ok"] is True, result
    assert result["title"] == "Brand New Story Title"
    new_path = report_dir / result["filename"]
    assert new_path.exists() and not path.exists()
    # case_study prefix intact, slug segment replaced, uuid unchanged
    assert new_path.name.startswith("cerebro_case_study_")
    assert new_path.stem.split("_")[-2] == "brand-new-story"
    assert new_path.stem.split("_")[-1] == rid
    # embedded title rewritten; mtime preserved for ordering
    assert charts._extract_structured_from_html(
        new_path.read_text(encoding="utf-8")
    )["title"] == "Brand New Story Title"
    assert abs(new_path.stat().st_mtime - old_mtime) < 1
    # old id still resolves after the rename
    assert charts.resolve_report_id(rid)[1] == new_path


def test_rename_hostile_title_stays_script_safe(studio, report_dir):
    rid, _ = _make_report(report_dir)
    rename_fn = _tool(studio, "rename_report_archive_entry")
    hostile = 'Evil </script><script>alert(1)</script>'
    result = rename_fn(report_ref=rid, title=hostile)
    assert result["ok"] is True
    new_path = report_dir / result["filename"]
    html = new_path.read_text(encoding="utf-8")
    structured = charts._extract_structured_from_html(html)
    assert structured["title"] == hostile
    m = charts._REPORT_DATA_RE.search(html)
    assert m and "</script>" not in m.group(2)


def test_rename_same_slug_and_collision(studio, report_dir):
    rename_fn = _tool(studio, "rename_report_archive_entry")
    # same slug -> in-place atomic replace
    rid, path = _make_report(report_dir, slug="steady-name-here", title="Steady Name Here")
    result = rename_fn(report_ref=rid, title="Steady Name Here!!!")
    assert result["ok"] is True
    assert (report_dir / result["filename"]).stem.split("_")[-2] == "steady-name-here"

    # collision -> -2 suffix
    other_id, _ = _make_report(report_dir, slug="target-slug-x", ts="20260716T000000Z")
    victim_id, _ = _make_report(report_dir, slug="victim", ts="20260716T000000Z")
    result = rename_fn(report_ref=victim_id, title="Target Slug X")
    assert result["ok"] is True
    assert result["filename"].split("_")[-2] in {"target-slug-x", "target-slug-x-2"}
    # both files still exist
    assert len(list(report_dir.glob("*target-slug-x*"))) == 2


def test_rename_validations(studio, report_dir):
    rid, _ = _make_report(report_dir)
    rename_fn = _tool(studio, "rename_report_archive_entry")
    assert rename_fn(report_ref=rid, title="")["ok"] is False
    assert rename_fn(report_ref=rid, title="x" * 201)["ok"] is False
    assert rename_fn(report_ref="zz", title="ok title")["ok"] is False


def test_mutations_disabled_flag(studio, report_dir, monkeypatch):
    rid, _ = _make_report(report_dir)
    monkeypatch.setattr(settings, "REPORT_STUDIO_ALLOW_MUTATIONS", False)
    for name, kwargs in (
        ("delete_report_archive_entry", {"report_ref": rid, "confirm": True}),
        ("rename_report_archive_entry", {"report_ref": rid, "title": "t"}),
        ("list_session_charts", {}),
        ("get_session_chart", {"chart_id": "chart_1"}),
        ("compose_report", {"title": "t", "sections": [{"markdown": "x"}]}),
    ):
        result = _tool(studio, name)(**kwargs)
        assert result["ok"] is False and "disabled" in result["error"], name
    # initial payload omits chart records + flags the UI off
    sc = _tool(studio, "open_report_studio")().structuredContent
    assert sc["view_state"]["mutations_enabled"] is False
    assert sc["view_state"]["session_charts"] is None


# --- B2: composer ---


@pytest.fixture
def seeded_charts():
    from datetime import datetime as _dt

    with charts._chart_lock:
        saved = dict(charts._chart_registry)
        charts._chart_registry.clear()
        for cid, ctype in (
            ("chart_1", "line"),
            ("chart_2", "numberDisplay"),
            ("chart_3", "numberDisplay"),
            ("chart_4", "bar"),
        ):
            charts._chart_registry[cid] = {
                "chart_id": cid,
                "title": f"T {cid}",
                "chart_type": ctype,
                "data_points": 10,
                "created_at": _dt.now(),
                "source": "test",
                "source_model": "api_x",
                "option": {"series": []},
            }
    yield
    with charts._chart_lock:
        charts._chart_registry.clear()
        charts._chart_registry.update(saved)


def test_list_and_get_session_charts(studio, seeded_charts):
    listed = _tool(studio, "list_session_charts")()
    assert listed["ok"] is True
    assert {c["chart_id"] for c in listed["charts"]} == {
        "chart_1", "chart_2", "chart_3", "chart_4",
    }
    assert all("option" not in c for c in listed["charts"])

    got = _tool(studio, "get_session_chart")(chart_id="chart_1")
    assert got["ok"] is True and got["option"] == {"series": []}
    miss = _tool(studio, "get_session_chart")(chart_id="chart_99")
    assert miss["ok"] is False


def test_compose_report_validations(studio, seeded_charts):
    compose = _tool(studio, "compose_report")
    assert compose(title="", sections=[{"markdown": "x"}])["ok"] is False
    assert compose(title="t", sections=[])["ok"] is False
    assert compose(title="t", sections=[{}])["ok"] is False
    assert (
        compose(title="t", sections=[{"markdown": "x", "charts": ["chart_1"]}])["ok"]
        is False
    )
    assert (
        compose(title="t", sections=[{"charts": ["chart_1", "chart_1"]}])["ok"]
        is False
    )
    missing = compose(title="t", sections=[{"charts": ["chart_1", "chart_99"]}])
    assert missing["ok"] is False
    assert missing["missing"] == ["chart_99"]
    assert "chart_1" in missing["available"]


def test_compose_report_generates_and_grids_kpis(studio, seeded_charts, report_dir):
    compose = _tool(studio, "compose_report")
    with patch.object(charts, "create_report_artifact") as mock_create:
        mock_create.return_value = {
            "report_id": str(uuid.uuid4()),
            "report_path": report_dir / "cerebro_report_x.html",
            "file_uri": "file:///x",
        }
        # KPIs split across two separate sections must still grid together
        result = compose(
            title="Composed",
            sections=[
                {"markdown": "## Intro"},
                {"charts": ["chart_2"]},
                {"charts": ["chart_3", "chart_1"]},
                {"charts": ["chart_4"]},
            ],
        )
        assert result["ok"] is True, result
        md = mock_create.call_args.args[1]
        # both solo KPIs gathered into ONE complete grid block
        assert "{{grid:2}}\n{{chart:chart_2}}\n{{chart:chart_3}}\n{{/grid}}" in md
        assert "{{chart:chart_1}}" in md and "{{chart:chart_4}}" in md
        kwargs = mock_create.call_args.kwargs
        assert kwargs["enforce_quality_gate"] is False
        assert kwargs["reset_session_state"] is False
        assert kwargs["presentation_mode"] == "report"


def test_compose_report_end_to_end_bypasses_gates(studio, seeded_charts, report_dir):
    """Real create_report_artifact run: no session state, gates untouched."""
    compose = _tool(studio, "compose_report")
    result = compose(
        title="E2E Composed Report",
        sections=[
            {"markdown": "## Overview\nSome prose."},
            {"charts": ["chart_2", "chart_3"]},   # 2 KPIs -> one grid
            {"charts": ["chart_1", "chart_4"]},
        ],
        subtitle="From the studio",
    )
    assert result["ok"] is True, result
    files = list(report_dir.glob("cerebro_report_*.html"))
    assert len(files) == 1
    structured = charts._extract_structured_from_html(
        files[0].read_text(encoding="utf-8")
    )
    assert structured["title"] == "E2E Composed Report"
    assert structured["subtitle"] == "From the studio"
    assert result["filename"] == files[0].name


def test_compose_chunk_large_grid(studio, report_dir):
    from datetime import datetime as _dt

    with charts._chart_lock:
        saved = dict(charts._chart_registry)
        charts._chart_registry.clear()
        for i in range(1, 7):
            charts._chart_registry[f"chart_{i}"] = {
                "chart_id": f"chart_{i}", "title": f"K{i}",
                "chart_type": "numberDisplay", "data_points": 1,
                "created_at": _dt.now(), "source": "t", "source_model": "m",
            }
    try:
        compose = _tool(studio, "compose_report")
        with patch.object(charts, "create_report_artifact") as mock_create:
            mock_create.return_value = {
                "report_id": str(uuid.uuid4()),
                "report_path": report_dir / "x.html",
                "file_uri": "file:///x",
            }
            result = compose(
                title="Six KPIs",
                sections=[{"charts": [f"chart_{i}" for i in range(1, 7)]}],
            )
            assert result["ok"] is True
            md = mock_create.call_args.args[1]
            # 6 KPIs -> grid:4 + grid:2, never grid:6 or grid:1
            assert "{{grid:4}}" in md and "{{grid:2}}" in md
            assert "{{grid:6}}" not in md and "{{grid:1}}" not in md
            assert md.count("{{/grid}}") == 2
    finally:
        with charts._chart_lock:
            charts._chart_registry.clear()
            charts._chart_registry.update(saved)


# --- create_studio_chart (composer chart creation) ---


class ChartStubCH:
    """Stub returning a small date/value table for chart creation."""

    def run_query(self, sql, database="dbt", requested_max_rows=500,
                  audience="tool", fetch_mode="auto", parameters=None):
        from cerebro_mcp.clients.clickhouse import ExecutedQuery

        rows = [[f"2026-04-{i + 1:02d}", i * 10] for i in range(5)]
        return ExecutedQuery(
            sql=sql, executed_sql=sql, database=database,
            columns=["date", "total"], rows=rows, row_count=len(rows),
            elapsed_seconds=0.01, fetch_mode="rows", warnings=[],
        )


@pytest.fixture
def studio_with_ch(report_dir, clean_chart_registry):
    mini_apps.reset_views_for_tests()
    server = FastMCP("test-studio-ch")
    mini_apps.register_mini_app_infra(server, None)
    register_report_studio_tools(server, ChartStubCH())
    yield server
    mini_apps.reset_views_for_tests()


def test_create_studio_chart_registers_record(studio_with_ch):
    create = _tool(studio_with_ch, "create_studio_chart")
    result = create(
        sql="SELECT date, sum(v) AS total FROM dbt.t GROUP BY date",
        chart_type="bar",
        title="Studio Bar",
    )
    assert result["ok"] is True, result
    chart_id = result["chart_id"]
    assert result["chart_type"] == "bar"
    assert result["data_points"] == 5

    # the record is immediately available to the composer picker
    listed = _tool(studio_with_ch, "list_session_charts")()
    assert chart_id in {c["chart_id"] for c in listed["charts"]}
    got = _tool(studio_with_ch, "get_session_chart")(chart_id=chart_id)
    assert got["ok"] is True and got["option"]

    # ...and composable end-to-end
    composed = _tool(studio_with_ch, "compose_report")(
        title="With Studio Chart",
        sections=[{"charts": [chart_id]}],
    )
    assert composed["ok"] is True, composed


def test_create_studio_chart_does_not_touch_agent_session():
    from cerebro_mcp.tools.governance.session_state import state

    with charts._chart_lock:
        saved = dict(charts._chart_registry)
        charts._chart_registry.clear()
    try:
        with state.lock:
            before = state.generate_chart_count
        result = charts.create_chart_record_from_sql(
            ChartStubCH(), "SELECT date, total FROM t", chart_type="line"
        )
        assert result["ok"] is True
        with state.lock:
            assert state.generate_chart_count == before
    finally:
        with charts._chart_lock:
            charts._chart_registry.clear()
            charts._chart_registry.update(saved)


def test_create_studio_chart_guards(studio_with_ch, monkeypatch):
    create = _tool(studio_with_ch, "create_studio_chart")
    assert create(sql="DROP TABLE t")["ok"] is False
    assert create(sql="SHOW TABLES")["ok"] is False
    assert create(sql="SELECT 1", title="x" * 201)["ok"] is False
    bad_type = create(sql="SELECT date, total FROM t", chart_type="hologram")
    assert bad_type["ok"] is False and "Unknown chart type" in bad_type["error"]

    monkeypatch.setattr(settings, "REPORT_STUDIO_ALLOW_MUTATIONS", False)
    gated = create(sql="SELECT date, total FROM t")
    assert gated["ok"] is False and "disabled" in gated["error"]


def test_create_studio_chart_without_ch(studio):
    result = _tool(studio, "create_studio_chart")(sql="SELECT 1")
    assert result["ok"] is False and "connection" in result["error"]


def test_create_studio_chart_hidden_from_model(studio_with_ch):
    assert "create_studio_chart" in mini_apps.get_app_only_tool_names()
    names = [t.name for t in asyncio.run(studio_with_ch.list_tools())]
    assert "create_studio_chart" not in names
    cfg = web_apps.WEB_APP_CONFIGS["report_studio"]
    assert "create_studio_chart" in cfg.allowed_tools
