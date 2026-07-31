"""Tests for Report Studio backend helpers (Phase 0: resolver, serializer,
atomic writes, chart-record TTL). The tool suite grows here with B1/B2."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cerebro_mcp.tools.visualization import charts
from cerebro_mcp.runtime.mcp_server import CerebroFastMCP


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
def studio(report_dir, monkeypatch):
    # Mutations default OFF now (connector plan R10 flipped the trust
    # switch fail-closed); these tests exercise the mutation features, so
    # they opt IN the way a trusted single-user deployment does.
    from cerebro_mcp.config import settings as _settings

    monkeypatch.setattr(_settings, "REPORT_STUDIO_ALLOW_MUTATIONS", True)
    mini_apps.reset_views_for_tests()
    server = CerebroFastMCP("test-studio")
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
    assert "session_charts" not in vs  # composer surface removed (v2 pivot)

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
    ):
        result = _tool(studio, name)(**kwargs)
        assert result["ok"] is False and "disabled" in result["error"], name
    sc = _tool(studio, "open_report_studio")().structuredContent
    assert sc["view_state"]["mutations_enabled"] is False



# --- Template catalog contract (the harness + UI both trust catalog.gen.json) ---

CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "ui" / "src" / "mini-apps" / "report-studio" / "model" / "catalog.gen.json"
)
CATALOG_CATEGORIES = {
    "answer", "chart", "sector_health", "deep_dive", "narrative",
    "attribution", "forecast", "governance", "utility",
}
CATALOG_TIERS = {
    "quick_answer", "single_chart", "lite_report", "full_report", "persona_workflow",
}
CATALOG_VERIFY = {"report_file", "charts", "answer", "export"}


def _load_catalog():
    import json

    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_catalog_gen_json_contract():
    catalog = _load_catalog()
    assert catalog["schema_version"] == 1
    templates = catalog["templates"]
    assert len(templates) >= 20
    ids = [t["id"] for t in templates]
    assert len(set(ids)) == len(ids)
    import re

    placeholder = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
    for t in templates:
        assert t["category"] in CATALOG_CATEGORIES, t["id"]
        assert t["tier"] in CATALOG_TIERS, t["id"]
        assert t["benchmark"]["verify"] in CATALOG_VERIFY, t["id"]
        assert t["instructions"].strip(), t["id"]
        declared = {p["name"] for p in t["params"]}
        used = set(placeholder.findall(t["instructions"]))
        assert used == declared, f"{t['id']}: params/body drift {used ^ declared}"


def test_catalog_personas_match_server_registry():
    """Codegen keeps a literal mirror of _VALID_ROLES — drift fails here."""
    from cerebro_mcp.tools.governance.agents import _VALID_ROLES

    catalog = _load_catalog()
    for t in catalog["templates"]:
        for role in [*t["personas"], *t.get("verify_personas", [])]:
            assert role in _VALID_ROLES, f"{t['id']}: unknown persona {role}"


def test_catalog_codegen_is_fresh():
    """catalog.gen.json must be regenerated whenever catalog/templates change."""
    import subprocess
    import sys

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "dev" / "gen_instruction_catalog.py"
    )
    proc = subprocess.run(
        [sys.executable, str(script), "--check"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
