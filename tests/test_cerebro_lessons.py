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
    # One day of future-dating is clock skew, not an error: CI runs UTC while
    # authors do not. A record written at 01:22 CEST carries the local date,
    # which is still tomorrow in UTC — that failed main once. Anything beyond a
    # day is a real typo. The staleness ceiling below is what this test is for.
    assert age >= -1, (
        f"{lesson_id}: last_verified is {-age} days in the future — that is "
        f"past any timezone offset, so it is a typo rather than clock skew"
    )
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
        for one in [prefix] if isinstance(prefix, str) else prefix:
            assert one, f"{profile.get('name')}: empty entry in path_prefix"


def test_a_dotfile_path_resolves_to_its_profile():
    """`lstrip("./")` strips a CHARACTER SET, not a prefix, so it ate the leading
    dot off every dotfile — `.github/workflows/x.yml` arrived as `github/...` and
    could not match a `.github/` prefix however it was declared. The build layer was
    the first profile to need dotfile paths, so nothing had exercised this."""
    for path in (
        ".github/workflows/build-and-release.yml",
        "./.github/workflows/build-and-release.yml",
        ".dockerignore",
        "Dockerfile",
    ):
        names = [p["name"] for p in cerebro_lessons.profiles_for_path(path)]
        assert "build_and_gates" in names, f"{path} -> {names}"


def test_a_multi_prefix_profile_ranks_on_its_longest_match():
    """Specificity must come from the prefix that actually matched. Ranking on the
    first or shortest declared prefix would let a profile that happens to also list
    a short path outrank a genuinely deeper one."""
    matched = cerebro_lessons.profiles_for_path("Makefile")
    assert [p["name"] for p in matched] == ["build_and_gates"]


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
    ("build stage re-runs every push nothing changed", "unexported-build-stage-never-caches"),
    ("unknown identifier 47 union arm group by", "ch-union-arm-needs-own-alias"),
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


def test_stopwords_do_not_outrank_a_real_symptom_match():
    """Matching is SUBSTRING, so a two-letter stopword is nearly a wildcard.

    `no` hits "not", "none", "known", "cannot"; `is` hits "this", "exists". With
    a title hit weighted above a symptom hit, a record whose TITLE merely
    contained "is ... not" outranked the record whose SYMPTOM matched `class`,
    `border` AND `background` — for a query about a CSS rule not applying. The
    stopword filter is what stops a well-worded query being decided by its glue.
    """
    from cerebro_mcp.loaders.agent_context import _QUERY_STOPWORDS, score_lessons

    # The exact regression: glue words must not carry the query.
    hits = [l["id"] for l in cerebro_lessons.search(
        "class is applied but no border or background", limit=3)]
    assert hits[0] == "css-undefined-token-drops-rule", hits

    # A query made only of stopwords must still return something rather than
    # silently finding nothing — the filter falls back rather than emptying.
    assert score_lessons(list(cerebro_lessons.lessons.values()), "is the a", limit=3)

    # The list stays closed-class: nothing domain-bearing may be added to it, or
    # real queries start losing terms.
    for meaningful in ("sql", "usd", "vp", "id", "ttl", "final", "cte", "css"):
        assert meaningful not in _QUERY_STOPWORDS, (
            f"{meaningful!r} carries meaning in this corpus and must stay searchable"
        )
