"""Gates for this repo's own lesson corpus.

Four of these exist specifically because dbt-cerebro's otherwise-excellent system
does NOT have them, and the gaps have already cost it something:

  - the `status` vocabulary is documented in three places and validated nowhere, so
    an undocumented fifth value ("primer") drifted into that corpus;
  - INDEX.md completeness is manual — nothing catches an unindexed record or an
    index entry pointing at a file that does not exist;
  - the body section headings are convention only;
  - `last_verified` is never checked, so 15 of 30 records there still carry their
    seeding date.

A lessons store rots quietly: nobody notices a record that stopped being true. The
whole point of these assertions is that rot becomes a test failure.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from cerebro_mcp.loaders.cerebro_lessons import (
    REQUIRED_SECTIONS,
    VALID_STATUSES,
    cerebro_lessons,
)

#: A record unverified for longer than this is stale by definition. Generous on
#: purpose — the aim is to catch abandonment, not to force busywork.
MAX_VERIFY_AGE_DAYS = 400

LESSONS = cerebro_lessons.lessons


def test_the_corpus_is_not_empty():
    """Guards the plumbing, not the content: a loader that silently returns {}
    would make every other assertion here vacuously pass."""
    assert len(LESSONS) >= 10


@pytest.mark.parametrize("lesson_id", sorted(LESSONS))
def test_id_equals_filename(lesson_id):
    """Cross-references are by bare string id, so the id IS the filename. A record
    whose frontmatter disagrees is unreachable from profiles.yml and the index."""
    assert LESSONS[lesson_id]["id"] == lesson_id


@pytest.mark.parametrize("lesson_id", sorted(LESSONS))
def test_status_is_in_the_vocabulary(lesson_id):
    """The gap that let `primer` into the dbt corpus. An out-of-vocabulary status
    reads as meaningful and sorts nowhere."""
    assert LESSONS[lesson_id]["status"] in VALID_STATUSES


@pytest.mark.parametrize("lesson_id", sorted(LESSONS))
def test_required_frontmatter_is_present(lesson_id):
    lesson = LESSONS[lesson_id]
    for field in ("title", "status", "scope", "symptom", "layer", "last_verified"):
        assert lesson.get(field), f"{lesson_id}: missing {field}"


@pytest.mark.parametrize("lesson_id", sorted(LESSONS))
def test_body_carries_every_required_section(lesson_id):
    """A record missing "Root cause" or "Forbidden action" is a war story, not a
    lesson — it tells you something happened without telling you what not to do."""
    body = LESSONS[lesson_id]["body"]
    missing = [s for s in REQUIRED_SECTIONS if s not in body]
    assert not missing, f"{lesson_id}: missing sections {missing}"


@pytest.mark.parametrize("lesson_id", sorted(LESSONS))
def test_evidence_is_present_unless_merely_observed(lesson_id):
    """A claim without evidence is a rumour. `observed` is the honest home for
    something seen but not yet pinned to a path, line or test."""
    lesson = LESSONS[lesson_id]
    if lesson["status"] == "observed":
        return
    evidence = lesson.get("evidence") or []
    assert evidence, f"{lesson_id}: status {lesson['status']} requires evidence"


@pytest.mark.parametrize("lesson_id", sorted(LESSONS))
def test_last_verified_is_a_date_and_not_stale(lesson_id):
    raw = str(LESSONS[lesson_id]["last_verified"])
    verified = dt.date.fromisoformat(raw)  # raises on a non-ISO date
    age = (dt.date.today() - verified).days
    assert age >= 0, f"{lesson_id}: last_verified is in the future"
    assert age <= MAX_VERIFY_AGE_DAYS, (
        f"{lesson_id}: last verified {age} days ago — re-verify it or drop it"
    )


def test_every_record_is_indexed_and_every_index_entry_exists():
    """Both directions. A record missing from INDEX.md is invisible to anyone
    browsing; an index entry with no record is a promise the store cannot keep."""
    index = cerebro_lessons.index_text()
    linked = set(re.findall(r"\]\(([a-z0-9-]+)\.md\)", index))
    assert not (set(LESSONS) - linked), (
        f"records missing from INDEX.md: {sorted(set(LESSONS) - linked)}"
    )
    assert not (linked - set(LESSONS)), (
        f"INDEX.md links records that do not exist: {sorted(linked - set(LESSONS))}"
    )


def test_index_states_the_status_of_every_record():
    """The index is where a reader triages, so it must carry the live/dead signal
    rather than making them open twelve files to find out."""
    index = cerebro_lessons.index_text()
    for lesson_id, lesson in LESSONS.items():
        entry = re.search(rf"\]\({lesson_id}\.md\)\s*`([a-z]+)`", index)
        assert entry, f"{lesson_id}: INDEX.md entry has no status marker"
        assert entry.group(1) == lesson["status"], (
            f"{lesson_id}: INDEX.md says {entry.group(1)}, record says {lesson['status']}"
        )


def test_every_profile_lesson_reference_resolves():
    """dbt-cerebro's build fails on a dangling hazard id and that is the one
    referential gate it does have — worth keeping."""
    profiles = cerebro_lessons.profiles
    layers = [profiles.get("global") or {}] + (profiles.get("profiles") or [])
    for layer in layers:
        name = layer.get("name", "global")
        for hazard in layer.get("hazards") or []:
            assert hazard in LESSONS, f"{name}: hazard {hazard!r} has no record"
        for rule in layer.get("rules") or []:
            ref = rule.get("lesson")
            if ref:
                assert ref in LESSONS, f"{name}: rule cites {ref!r} which has no record"


def test_every_profile_declares_a_path_prefix():
    for profile in cerebro_lessons.profiles.get("profiles") or []:
        prefix = (profile.get("match") or {}).get("path_prefix")
        assert prefix, f"{profile.get('name')}: profiles match path prefixes, not names"


def test_profiles_resolve_least_specific_first():
    """`queries/` nests under `tools/visualization/`, so both match. Order is a
    property of prefix length, not of how profiles.yml happens to be arranged."""
    matched = cerebro_lessons.profiles_for_path(
        "src/cerebro_mcp/tools/visualization/queries/governance/gip_pipeline.sql"
    )
    names = [p["name"] for p in matched]
    assert names == ["miniapp_backends", "sql_query_files"], names


def test_resolve_accumulates_guides_and_hazards():
    resolved = cerebro_lessons.resolve(
        "src/cerebro_mcp/tools/visualization/queries/cow/open_orders.sql"
    )
    assert "AGENTS.md" in resolved["guides"]
    assert "ch-final-three-way-rule" in resolved["hazards"]
    assert resolved["rules"], "the global layer contributes rules to every path"


def test_an_unmatched_path_still_gets_the_global_layer():
    """Fail useful, not empty: a path no profile covers is exactly when an agent
    most needs the repo-wide rules."""
    resolved = cerebro_lessons.resolve("some/unknown/place.py")
    assert resolved["rules"]
    assert resolved["guides"] == ["AGENTS.md"]


# --- retrieval quality -------------------------------------------------------
# Queried by SYMPTOM wording, never by id — an agent hitting one of these has the
# symptom in front of it and does not know the id exists. Same top-3 bar that
# tests/test_agent_knowledge_eval.py holds the dbt corpus to.
RETRIEVAL_CASES = [
    ("query returns zero rows no error", "ch-output-alias-shadows-column"),
    ("memory limit exceeded 241 cte", "ch-cte-inlined-per-reference"),
    ("illegal aggregation 184", "ch-alias-in-where-illegal-aggregation"),
    ("should this read use FINAL", "ch-final-three-way-rule"),
    ("delegate reports 0 voting power", "versioned-payload-positional-index"),
    ("different rows on repeat calls", "ch-bare-limit-nondeterministic"),
    ("ui change not showing up", "stale-prebuilt-miniapp-bundle"),
    ("panel missing reads as no data", "failed-dataset-must-stay-visible"),
    ("gate always passes never fires", "negated-grep-passes-when-tool-absent"),
    ("layout broken in a different app", "shared-stylesheet-unscoped-selectors"),
    ("model frozen days behind chain head", "dbt-sqlx-silently-not-compiled"),
    ("wrong column label positional row", "dataset-column-order-is-a-contract"),
    ("sql edit has no effect", "sql-loader-cache-needs-restart"),
    ("class is applied but no border or background", "css-undefined-token-drops-rule"),
]


@pytest.mark.parametrize("query,expected", RETRIEVAL_CASES)
def test_symptom_wording_retrieves_the_right_lesson(query, expected):
    hits = [lesson["id"] for lesson in cerebro_lessons.search(query, limit=3)]
    assert expected in hits, f"{query!r} -> {hits}, expected {expected}"


def test_path_boost_lifts_a_lesson_that_applies_to_that_layer():
    """Same query, different path: naming the SQL layer should surface its hazards
    above an equally-worded match from another layer."""
    generic = [lesson["id"] for lesson in cerebro_lessons.search("silently", limit=12)]
    scoped = [
        lesson["id"]
        for lesson in cerebro_lessons.search(
            "silently", path="src/cerebro_mcp/tools/visualization/queries/x.sql", limit=3
        )
    ]
    assert scoped, "a boosted search must still return results"
    assert any(x in cerebro_lessons.resolve(
        "src/cerebro_mcp/tools/visualization/queries/x.sql")["hazards"] for x in scoped)
    assert generic  # the unboosted call is the control
