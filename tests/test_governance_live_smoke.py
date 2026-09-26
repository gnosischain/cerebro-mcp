"""Opt-in live ClickHouse smoke for the Governance Explorer data contract.

SKIPPED unless ``CEREBRO_LIVE_CH_SMOKE`` is set — ``make test``/CI stay
hermetic. Runs against the real ``governance_db`` with the deployment's
``ClickHouseManager`` credentials.

Invariants use **FINAL counts only**. ``raw == FINAL`` equality is a
transient post-merge state between daily ingester runs (which re-insert
whole tables) and is NEVER asserted here.

Post-reingest assertions (``snapshot_proposals.discussion`` and
``forum_posts.raw``) are guarded: pre-reingest data skips them with a clear
notice instead of failing.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CEREBRO_LIVE_CH_SMOKE"),
    reason="live ClickHouse smoke is opt-in: set CEREBRO_LIVE_CH_SMOKE=1",
)

GOV_DB = "governance_db"

EXPECTED_TABLES = {
    "snapshot_proposals", "snapshot_votes", "snapshot_follows",
    "snapshot_space", "forum_topics", "forum_posts", "forum_users",
    "forum_categories", "forum_polls", "forum_likes",
}

#: Key columns per table (excluding the re-ingest additions, checked apart).
KEY_COLUMNS = {
    "snapshot_proposals": {
        "id", "space_id", "title", "state", "type", "author", "created_at",
        "start_at", "end_at", "snapshot_block", "scores_total", "quorum",
        "votes_count", "scores_state", "raw_json", "ingested_at",
    },
    "snapshot_votes": {
        "id", "proposal_id", "space_id", "voter", "created_at", "vp",
        "vp_state", "raw_json", "ingested_at",
    },
    "snapshot_follows": {"id", "follower", "space_id", "created_at", "ingested_at"},
    "snapshot_space": {
        "space_id", "name", "proposals_count", "followers_count",
        "votes_count", "ingested_at",
    },
    "forum_topics": {
        "id", "title", "slug", "category_id", "posts_count", "reply_count",
        "views", "like_count", "participant_count", "tags", "created_at",
        "last_posted_at", "bumped_at", "closed", "archived", "pinned",
        "ingested_at",
    },
    "forum_posts": {
        "id", "topic_id", "post_number", "user_id", "username", "created_at",
        "updated_at", "reply_to_post_number", "reply_count", "reads",
        "like_count", "cooked", "raw_json", "ingested_at",
    },
    "forum_users": {
        "id", "username", "name", "trust_level", "likes_received",
        "likes_given", "post_count", "topic_count", "days_visited",
        "ingested_at",
    },
    "forum_categories": {
        "id", "parent_id", "name", "slug", "topic_count", "post_count",
        "description", "ingested_at",
    },
    "forum_polls": {
        "post_id", "topic_id", "poll_id", "poll_name", "poll_type", "status",
        "results_visibility", "is_public", "close_at", "voters", "option_id",
        "option_html", "option_votes", "raw_json", "ingested_at",
    },
    "forum_likes": {
        "post_id", "topic_id", "post_number", "acting_user_id",
        "acting_username", "created_at", "hidden", "deleted", "raw_json",
        "ingested_at",
    },
}


@pytest.fixture(scope="module")
def ch():
    from cerebro_mcp.clients.clickhouse import ClickHouseManager

    return ClickHouseManager()


def _rows(ch, sql: str, parameters: dict | None = None) -> list[list]:
    result = ch.run_query(
        sql, GOV_DB, requested_max_rows=10_000, audience="internal",
        fetch_mode="auto", parameters=parameters,
    )
    return [list(row) for row in result.rows]


def _scalar(ch, sql: str, parameters: dict | None = None):
    rows = _rows(ch, sql, parameters)
    assert rows and rows[0], f"no rows for: {sql}"
    return rows[0][0]


def _columns(ch, table: str) -> set[str]:
    # DESCRIBE and system.* are rejected by the manager's SQL validator, so
    # introspect via a zero-row SELECT and the result's column metadata.
    result = ch.run_query(
        f"SELECT * FROM governance_db.{table} LIMIT 0", GOV_DB,
        requested_max_rows=1, audience="internal", fetch_mode="auto",
    )
    return {str(name) for name in result.columns}


def test_expected_tables_exist_with_key_columns(ch):
    missing = []
    for table in sorted(EXPECTED_TABLES):
        try:
            _columns(ch, table)
        except Exception:
            missing.append(table)
    assert not missing, f"missing governance_db tables: {missing}"
    for table, expected in KEY_COLUMNS.items():
        columns = _columns(ch, table)
        missing_columns = expected - columns
        assert not missing_columns, f"{table} missing columns: {sorted(missing_columns)}"


def test_reingest_columns_present_or_notice(ch):
    """The re-ingested DDLs add snapshot_proposals.discussion and
    forum_posts.raw — pre-reingest data skips with a notice."""
    proposal_columns = _columns(ch, "snapshot_proposals")
    post_columns = _columns(ch, "forum_posts")
    missing = []
    if "discussion" not in proposal_columns:
        missing.append("snapshot_proposals.discussion")
    if "raw" not in post_columns:
        missing.append("forum_posts.raw")
    if missing:
        pytest.skip(
            "pre-reingest schema: " + ", ".join(missing)
            + " absent — re-run after the governance_db re-ingestion"
        )


def test_final_count_invariants(ch):
    """FINAL-count sanity floors (never raw==FINAL — that is transient)."""
    proposals = int(_scalar(ch, "SELECT count() FROM governance_db.snapshot_proposals FINAL"))
    votes = int(_scalar(ch, "SELECT count() FROM governance_db.snapshot_votes FINAL"))
    follows = int(_scalar(ch, "SELECT count() FROM governance_db.snapshot_follows FINAL"))
    topics = int(_scalar(ch, "SELECT count() FROM governance_db.forum_topics FINAL"))
    posts = int(_scalar(ch, "SELECT count() FROM governance_db.forum_posts FINAL"))
    users = int(_scalar(ch, "SELECT count() FROM governance_db.forum_users FINAL"))
    categories = int(_scalar(ch, "SELECT count() FROM governance_db.forum_categories FINAL"))
    space = int(_scalar(ch, "SELECT count() FROM governance_db.snapshot_space FINAL"))
    assert proposals >= 253
    assert votes >= 48_000
    assert follows >= 12_000
    assert topics >= 880
    assert posts >= 6_800
    assert users >= 2_600
    assert categories >= 15
    assert space == 1
    poll_option_rows = int(_scalar(ch, "SELECT count() FROM governance_db.forum_polls FINAL"))
    likes = int(_scalar(ch, "SELECT count() FROM governance_db.forum_likes FINAL"))
    assert poll_option_rows >= 350
    assert likes >= 9_000
    voters = int(_scalar(
        ch, "SELECT uniqExact(lower(voter)) FROM governance_db.snapshot_votes FINAL"
    ))
    assert voters >= 6_300


def test_space_counters_match_child_final_counts(ch):
    row = _rows(ch, """
SELECT proposals_count, followers_count, votes_count
FROM governance_db.snapshot_space FINAL
ORDER BY space_id""")[0]
    space_proposals, space_followers, space_votes = (int(v) for v in row)
    proposals = int(_scalar(ch, "SELECT count() FROM governance_db.snapshot_proposals FINAL"))
    follows = int(_scalar(ch, "SELECT count() FROM governance_db.snapshot_follows FINAL"))
    votes = int(_scalar(ch, "SELECT count() FROM governance_db.snapshot_votes FINAL"))
    assert space_proposals == proposals
    assert space_followers == follows
    assert space_votes == votes


def test_posts_reference_existing_topics(ch):
    orphans = int(_scalar(ch, """
SELECT count() FROM governance_db.forum_posts FINAL
WHERE topic_id NOT IN (SELECT id FROM governance_db.forum_topics FINAL)"""))
    assert orphans == 0


def test_poll_identity_and_post_consistency(ch):
    """poll_id is the poll identity, one-to-one with (post_id, poll_name).
    Uniq-count equality alone proves neither direction, so both are checked.
    Every poll's poll-bearing post exists and agrees on the topic."""
    pairs_per_id = int(_scalar(ch, """
SELECT max(pairs) FROM (
  SELECT poll_id, uniqExact(post_id, poll_name) AS pairs
  FROM governance_db.forum_polls FINAL GROUP BY poll_id)"""))
    assert pairs_per_id == 1
    ids_per_pair = int(_scalar(ch, """
SELECT max(ids) FROM (
  SELECT post_id, poll_name, uniqExact(poll_id) AS ids
  FROM governance_db.forum_polls FINAL GROUP BY post_id, poll_name)"""))
    assert ids_per_pair == 1
    orphan_posts = int(_scalar(ch, """
SELECT count() FROM governance_db.forum_polls FINAL
WHERE post_id NOT IN (SELECT id FROM governance_db.forum_posts FINAL)"""))
    assert orphan_posts == 0
    topic_mismatch = int(_scalar(ch, """
SELECT count()
FROM governance_db.forum_polls AS p FINAL
INNER JOIN governance_db.forum_posts AS fp FINAL ON fp.id = p.post_id
WHERE toInt64(p.topic_id) != toInt64(fp.topic_id)"""))
    assert topic_mismatch == 0


def test_poll_voters_is_poll_level_and_options_unique(ch):
    """voters is a poll-level total repeated per option row (so max == min
    per poll); (poll_id, option_id) is unique; -1 is the only sentinel."""
    voters_varies = int(_scalar(ch, """
SELECT countIf(mn != mx) FROM (
  SELECT poll_id, min(voters) AS mn, max(voters) AS mx
  FROM governance_db.forum_polls FINAL GROUP BY poll_id)"""))
    assert voters_varies == 0
    max_dup_options = int(_scalar(ch, """
SELECT max(cnt) FROM (
  SELECT poll_id, option_id, count() AS cnt
  FROM governance_db.forum_polls FINAL GROUP BY poll_id, option_id)"""))
    assert max_dup_options == 1
    sentinel_floor = int(_scalar(ch, """
SELECT min(option_votes) FROM governance_db.forum_polls FINAL"""))
    assert sentinel_floor >= -1


def test_like_identity_and_attribution_band(ch):
    """(post_id, acting_user_id) is the per-like identity. The attribution
    band is a data-quality rail only (measured 0.72 on 2026-07-31): the
    DISPLAYED figure is computed live by forum_summary.like_attribution_pct.
    If a backfill moves coverage outside the band, re-measure and revisit
    the UI wording together with this rail."""
    max_dup_likes = int(_scalar(ch, """
SELECT max(cnt) FROM (
  SELECT post_id, acting_user_id, count() AS cnt
  FROM governance_db.forum_likes FINAL GROUP BY post_id, acting_user_id)"""))
    assert max_dup_likes == 1
    eligible = int(_scalar(ch, """
SELECT count() FROM governance_db.forum_likes FINAL
WHERE hidden = 0 AND deleted = 0
  AND topic_id IN (SELECT id FROM governance_db.forum_topics FINAL)
  AND post_id IN (SELECT id FROM governance_db.forum_posts FINAL)"""))
    counters = int(_scalar(ch, """
SELECT sum(like_count) FROM governance_db.forum_posts FINAL"""))
    ratio = eligible / max(counters, 1)
    assert 0.60 <= ratio <= 0.85, f"attribution ratio {ratio:.3f} outside band"


def test_forum_polls_spec_tie_and_zero_vote_contract(ch):
    """Run the rendered forum_polls spec: leading_option must be NULL on
    every hidden, tied, or zero-vote poll — and live data currently contains
    all three shapes, so the guards are actually exercised (if one shape
    disappears from the data, loosen the presence floor consciously)."""
    from cerebro_mcp.tools.visualization import governance_explorer

    range_state = governance_explorer._range_state("", "")
    specs = {
        spec.key: spec
        for spec in governance_explorer._forum_specs(
            range_state, governance_explorer._default_filters()
        )
    }
    spec = specs["forum_polls"]
    result = ch.run_query(
        spec.sql, GOV_DB, requested_max_rows=10_000, audience="internal",
        fetch_mode="auto", parameters=spec.parameters or None,
    )
    idx = {str(name): i for i, name in enumerate(result.columns)}
    tied = hidden = zero = 0
    for row in result.rows:
        leading_option = row[idx["leading_option"]]
        leading_votes = row[idx["leading_votes"]]
        if row[idx["leading_tied"]]:
            tied += 1
            assert leading_option is None, row
        if row[idx["results_hidden"]]:
            hidden += 1
            assert leading_option is None, row
            assert leading_votes is None, row
        if leading_votes == 0:
            zero += 1
            assert leading_option is None, row
    assert tied >= 1, "no tied poll left in live data — loosen consciously"
    assert zero >= 1, "no zero-vote poll left in live data — loosen consciously"
    assert hidden >= 1, "no hidden-results poll left in live data — loosen consciously"


def test_source_freshness_forum_clock_matches_weakest_table_live(ch):
    """The rendered forum ingestion clock equals the min of the six forum
    tables' independently-queried max(ingested_at) values."""
    from cerebro_mcp.tools.visualization import governance_explorer

    spec = governance_explorer._source_freshness_spec()
    result = ch.run_query(
        spec.sql, GOV_DB, requested_max_rows=10, audience="internal",
        fetch_mode="auto",
    )
    idx = {str(name): i for i, name in enumerate(result.columns)}
    forum_clock = None
    for row in result.rows:
        if str(row[idx["source"]]) == "forum":
            forum_clock = row[idx["latest_ingested_at"]]
    assert forum_clock is not None
    per_table = [
        _scalar(ch, f"SELECT max(ingested_at) FROM governance_db.{table} FINAL")
        for table in (
            "forum_topics", "forum_posts", "forum_users", "forum_categories",
            "forum_polls", "forum_likes",
        )
    ]
    assert forum_clock == min(per_table), (forum_clock, per_table)


def test_choices_and_scores_extract_on_all_proposals(ch):
    bad = int(_scalar(ch, """
SELECT countIf(length(JSONExtract(raw_json, 'choices', 'Array(String)')) = 0)
FROM governance_db.snapshot_proposals FINAL"""))
    assert bad == 0
    # scores may legitimately be empty while scores_state is pending; when
    # final, choices/scores lengths must agree.
    mismatched_final = int(_scalar(ch, """
SELECT countIf(
  scores_state = 'final'
  AND length(JSONExtract(raw_json, 'choices', 'Array(String)'))
      != length(JSONExtract(raw_json, 'scores', 'Array(Float64)')))
FROM governance_db.snapshot_proposals FINAL"""))
    assert mismatched_final == 0


def test_gip_extraction_hits_both_sides(ch):
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    gip_sql = gov._gip_sql("title")
    proposals = int(_scalar(ch, f"""
SELECT countIf({gip_sql} IS NOT NULL)
FROM governance_db.snapshot_proposals FINAL"""))
    topics = int(_scalar(ch, f"""
SELECT countIf({gip_sql} IS NOT NULL)
FROM governance_db.forum_topics FINAL"""))
    assert proposals > 0
    assert topics > 0


def test_gip_fixture_table_matches_in_clickhouse(ch):
    """Evaluate the shared GIP fixture table in the engine that runs the SQL
    dialect — RE2 escape/semantics differences from Python/JS make this the
    only authoritative check for the rendered pattern (incl. that the
    \\x{200B} braces survive the f-string assembly and the client's
    {name:Type} parameter substitution untouched)."""
    from cerebro_mcp.tools.visualization import governance_explorer as gov
    from tests.gip_fixtures import GIP_TITLE_FIXTURES

    strings = [text for text, _ in GIP_TITLE_FIXTURES]
    rows = _rows(ch, f"""
SELECT s, {gov._gip_sql('s')} AS gip
FROM (SELECT arrayJoin({{strs:Array(String)}}) AS s)
""", parameters={"strs": strings})
    got = {str(row[0]): row[1] for row in rows}
    assert set(got) == set(strings)
    for text, expected in GIP_TITLE_FIXTURES:
        value = int(got[text]) if got[text] is not None else None
        assert value == expected, repr(text)


def test_choice_jsontype_classification_on_sample(ch):
    """A 1k-vote sample classifies to kinds within {single, ranked} only."""
    rows = _rows(ch, """
SELECT multiIf(JSONType(raw_json, 'choice') IN ('Int64', 'UInt64'), 'single',
               JSONType(raw_json, 'choice') = 'Array', 'ranked',
               'unsupported') AS choice_kind,
       count() AS n
FROM (
  SELECT raw_json FROM governance_db.snapshot_votes FINAL
  ORDER BY created_at DESC, id
  LIMIT 1000
)
GROUP BY choice_kind
ORDER BY choice_kind""")
    kinds = {str(row[0]) for row in rows}
    assert kinds <= {"single", "ranked"}, f"unexpected choice kinds: {kinds}"


def test_post_reingest_discussion_coverage(ch):
    if "discussion" not in _columns(ch, "snapshot_proposals"):
        pytest.skip("pre-reingest schema: snapshot_proposals.discussion absent")
    populated = int(_scalar(ch, """
SELECT countIf(discussion != '')
FROM governance_db.snapshot_proposals FINAL"""))
    if populated == 0:
        pytest.skip(
            "discussion column exists but is empty — data not re-ingested yet"
        )
    assert populated >= 90
    resolving = int(_scalar(ch, r"""
SELECT count() FROM governance_db.snapshot_proposals FINAL
WHERE toUInt32OrNull(extract(discussion, 'forum\\.gnosis\\.io/t/[^/]+/([0-9]+)'))
      IN (SELECT id FROM governance_db.forum_topics FINAL)"""))
    assert resolving >= 85


def test_post_reingest_raw_markdown_coverage(ch):
    if "raw" not in _columns(ch, "forum_posts"):
        pytest.skip("pre-reingest schema: forum_posts.raw absent")
    total = int(_scalar(ch, "SELECT count() FROM governance_db.forum_posts FINAL"))
    with_raw = int(_scalar(ch, """
SELECT countIf(raw != '') FROM governance_db.forum_posts FINAL"""))
    if with_raw == 0:
        pytest.skip("raw column exists but is empty — data not re-ingested yet")
    assert with_raw / max(total, 1) >= 0.99


def _live_entity_identifiers(ch) -> dict[str, str]:
    proposal_id = str(_scalar(ch, """
SELECT id FROM governance_db.snapshot_proposals FINAL
WHERE discussion != '' ORDER BY created_at DESC LIMIT 1"""))
    voter = str(_scalar(ch, """
SELECT lower(voter) FROM governance_db.snapshot_votes FINAL
ORDER BY vp DESC LIMIT 1"""))
    topic_id = str(_scalar(ch, """
SELECT id FROM governance_db.forum_topics FINAL
ORDER BY posts_count DESC LIMIT 1"""))
    user_id = str(_scalar(ch, """
SELECT user_id FROM governance_db.forum_posts FINAL
WHERE user_id > 0 ORDER BY created_at DESC LIMIT 1"""))
    return {
        "proposal": proposal_id,
        "voter": voter,
        "forum_topic": topic_id,
        "forum_user": user_id,
    }


def test_every_spec_executes_against_live_clickhouse(ch):
    """Run EVERY dataset spec (all sections, default + filtered variants, and
    all entity bundles) against the real database. This is the guard the
    hermetic StubCH suite cannot provide — it catches ClickHouse-only issues
    like alias-shadowing ILLEGAL_AGGREGATION errors."""
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    section_filter_variants = {
        "overview": [gov._default_filters()],
        "proposals": [
            gov._default_filters(),
            {**gov._default_filters(), "query": "gip", "proposal_state": "closed",
             "proposal_type": "basic", "quorum_status": "met",
             "sort_by": "most_votes"},
        ],
        "voters": [
            gov._default_filters(),
            {**gov._default_filters(), "sort_by": "vote_count"},
        ],
        "forum": [
            gov._default_filters(),
            {**gov._default_filters(), "query": "gip", "category_id": 21,
             "forum_status": "open", "sort_by": "most_posts"},
        ],
        # The citation graph was the one section neither sweep executed —
        # added with WL-039, whose regex change lands exactly there. Its SQL
        # is range-invariant, so the fingerprint dedup collapses the range
        # variants to one execution per query.
        "graph": [gov._default_filters()],
    }
    range_variants = [
        gov._range_state("", ""),
        gov._range_state("90d", ""),
        gov._range_state("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    ]

    specs: list[tuple[str, object]] = []
    for section, filter_variants in section_filter_variants.items():
        for filters in filter_variants:
            for range_state in range_variants:
                for spec in gov._section_specs(section, range_state, filters):
                    specs.append((f"{section}:{spec.key}", spec))
    for kind, identifier in _live_entity_identifiers(ch).items():
        for spec in gov._entity_specs(kind, identifier):
            specs.append((f"entity:{kind}:{spec.key}", spec))

    failures = []
    seen_sql = set()
    for label, spec in specs:
        fingerprint = (spec.sql, tuple(sorted((spec.parameters or {}).items())))
        if fingerprint in seen_sql:
            continue
        seen_sql.add(fingerprint)
        try:
            ch.run_query(
                spec.sql, GOV_DB, requested_max_rows=100, audience="internal",
                fetch_mode="auto", parameters=spec.parameters or None,
            )
        except Exception as exc:  # noqa: BLE001 — collecting every failure
            failures.append(f"{label}: {exc}")
    assert not failures, "specs failed against live ClickHouse:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Delegation plane (rpc_log_indexer) — gated on the DB being reachable,
# since it lives in a separate database that may not be granted everywhere.
# ---------------------------------------------------------------------------

DELEGATE_DB = "rpc_log_indexer"
DELEGATE_VIEW = "v_delegate_events_gnosis"
DELEGATE_KEY_COLUMNS = {
    "environment", "chain_id", "action", "delegator", "id", "delegate",
    "block_timestamp", "block_number", "log_index", "tx_hash",
}


def _delegate_db_reachable(ch) -> bool:
    try:
        ch.run_query(
            f"SELECT 1 FROM {DELEGATE_DB}.{DELEGATE_VIEW} LIMIT 1",
            GOV_DB, requested_max_rows=1, audience="internal", fetch_mode="auto",
        )
        return True
    except Exception:
        return False


def test_delegate_view_exists_with_key_columns(ch):
    if not _delegate_db_reachable(ch):
        pytest.skip(f"{DELEGATE_DB}.{DELEGATE_VIEW} not reachable (grants/DB absent)")
    result = ch.run_query(
        f"SELECT * FROM {DELEGATE_DB}.{DELEGATE_VIEW} LIMIT 0",
        GOV_DB, requested_max_rows=1, audience="internal", fetch_mode="auto",
    )
    columns = {str(name) for name in result.columns}
    missing = DELEGATE_KEY_COLUMNS - columns
    assert not missing, f"{DELEGATE_VIEW} missing columns: {sorted(missing)}"


def test_delegation_specs_execute_against_live_clickhouse(ch):
    """Every delegation spec (default + sorted variants, all ranges) against
    the real delegate registry view + the cross join into snapshot_votes."""
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    if not _delegate_db_reachable(ch):
        pytest.skip(f"{gov.DELEGATE_DB}.{gov.DELEGATE_VIEW} not reachable")

    range_variants = [
        gov._range_state("", ""),
        gov._range_state("90d", ""),
        gov._range_state("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    ]
    filter_variants = [
        gov._default_filters(),
        {**gov._default_filters(), "sort_by": "recently_active"},
    ]
    failures: list[str] = []
    seen: set = set()
    for filters in filter_variants:
        for range_state in range_variants:
            for spec in gov._delegations_specs(range_state, filters):
                fingerprint = (spec.sql, tuple(sorted((spec.parameters or {}).items())))
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                try:
                    ch.run_query(
                        spec.sql, GOV_DB, requested_max_rows=100,
                        audience="internal", fetch_mode="auto",
                        parameters=spec.parameters or None,
                    )
                except Exception as exc:  # noqa: BLE001 — collecting every failure
                    failures.append(f"delegations:{spec.key}: {exc}")
    assert not failures, "delegation specs failed:\n" + "\n".join(failures)


def _treasury_db_reachable(ch) -> bool:
    try:
        ch.run_query(
            "SELECT 1 FROM rpc_state_indexer.census_publications LIMIT 1",
            GOV_DB, requested_max_rows=1, audience="internal", fetch_mode="auto",
        )
        return True
    except Exception:
        return False


_TREASURY_ENTITIES = (
    ("treasury_wallet", "1:0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f"),
    ("treasury_wallet", "100:0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f"),
    ("treasury_token", "1:0x6810e776880c02933d47db1b9fc05908e5386b96"),
    ("treasury_token", "100:0xcb444e90d8198415266c6a2724b7900fb12fc56e"),  # EURe v1
)


def _treasury_live_specs(gov):
    specs = gov._treasury_specs(gov._range_state("", ""), gov._default_filters())
    for kind, ident in _TREASURY_ENTITIES:
        specs += gov._entity_specs(kind, ident)
    return specs


def _treasury_budget_rows(ch, sql: str, parameters: dict):
    from cerebro_mcp.clients.clickhouse import INTERACTIVE_QUERY_BUDGET

    return ch.run_query(
        sql, GOV_DB, requested_max_rows=10_000, audience="internal",
        fetch_mode="auto", parameters=parameters or None,
        query_budget=INTERACTIVE_QUERY_BUDGET,
    )


def test_treasury_specs_execute_within_budget_and_match_the_contract(ch):
    """Every treasury section and entity spec against the real plane, UNDER the
    interactive budget, with its columns equal to the UI contract
    (ui/src/mini-apps/governance/model/treasuryColumns.json). The plane ran in no
    test before the 2026-08 OOM, and the full-history rewrite is only proven
    against a real server: the history datasets must stay well inside ROW_CAP."""
    import json
    import time
    from pathlib import Path

    from cerebro_mcp.tools.visualization import governance_explorer as gov

    if not _treasury_db_reachable(ch):
        pytest.skip("rpc_state_indexer.census_publications not reachable")
    contract = json.loads(
        (Path(__file__).resolve().parents[1] / "ui/src/mini-apps/governance/model/treasuryColumns.json")
        .read_text()
    )["datasets"]
    failures: list[str] = []
    for spec in _treasury_live_specs(gov):
        started = time.monotonic()
        try:
            result = _treasury_budget_rows(ch, spec.sql, spec.parameters)
        except Exception as exc:  # noqa: BLE001 — collect every failure
            failures.append(f"{spec.key}: {exc}")
            continue
        elapsed = time.monotonic() - started
        print(f"treasury {spec.key}: {len(result.rows)} rows in {elapsed:.2f}s")
        if elapsed > 15:
            failures.append(f"{spec.key}: {elapsed:.1f}s over the 15s smoke bound")
        if [str(c) for c in result.columns] != contract[spec.key]:
            failures.append(f"{spec.key}: columns {result.columns} != contract")
        if not spec.exact_count and len(result.rows) > 0.8 * gov.ROW_CAP:
            failures.append(f"{spec.key}: {len(result.rows)} rows nears ROW_CAP")
    assert not failures, "treasury specs failed:\n" + "\n".join(failures)


def _asof_ctes(gov) -> str:
    from cerebro_mcp.tools.visualization import sql_loader

    common = {"pub": gov._tq(gov.TREASURY_PUB_TABLE), "job": gov.TREASURY_JOB,
              "chain_pred": "1", "window_days": gov.TREASURY_ASOF_WINDOW_DAYS}
    asof = sql_loader.load_sql(
        "governance", "_cte_treasury_asof", **common, served=gov._tq(gov.TREASURY_SERVED_VIEW),
        ratio=gov.TREASURY_COMPLETENESS_RATIO, max_carry_days=gov.TREASURY_MAX_CARRY_DAYS,
    )
    positions = sql_loader.load_sql(
        "governance", "_cte_treasury_asof_positions", balances=gov._tq(gov.TREASURY_BALANCES_TABLE),
        job=gov.TREASURY_JOB, window_days=gov.TREASURY_ASOF_WINDOW_DAYS,
    )
    return f"{asof},\n{positions}"


def test_treasury_two_step_read_equals_the_canonical_view_at_as_of(ch):
    """P1 — the served two-step read (v_publications_current -> token_balances,
    argMax dedup, attempt pin) returns exactly the canonical v_treasury_balances
    at each chain's as-of. BOTH sides in ONE query: the plane is written daily,
    and two separate runs would measure the write (live-table-invalidates-cross-query-diff)."""
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    if not _treasury_db_reachable(ch):
        pytest.skip("rpc_state_indexer.census_publications not reachable")
    sql = gov._compact_sql(f"""WITH {_asof_ctes(gov)},
two_step AS (
  SELECT p.ps_chain AS c, p.ps_token AS t, sum(p.ps_raw) AS s, count() AS n
  FROM apos AS p INNER JOIN picked AS k ON k.pk_chain = p.ps_chain AND k.pk_token = p.ps_token
  WHERE k.pk_date = k.pk_as_of GROUP BY c, t),
canonical AS (
  SELECT v.chain_id AS c, v.token_address AS t, sum(v.balance_raw) AS s, count() AS n
  FROM {gov._tq(gov.TREASURY_CANONICAL_VIEW)} AS v
  WHERE v.job_name = '{gov.TREASURY_JOB}' AND v.balance_raw != 0
    AND v.snapshot_date IN (SELECT pk_as_of FROM picked)
    AND (v.chain_id, v.snapshot_date) IN (SELECT pk_chain, pk_as_of FROM picked)
  GROUP BY c, t)
SELECT count() AS tokens, countIf(a.s != b.s OR a.n != b.n) AS mismatches
FROM two_step AS a FULL OUTER JOIN canonical AS b ON a.c = b.c AND a.t = b.t
ORDER BY tokens""")
    tokens, mismatches = _rows(ch, sql)[0]
    assert tokens > 100, tokens
    assert mismatches == 0, f"{mismatches} of {tokens} tokens differ from the canonical view"


def test_treasury_attempt_sums_equal_the_published_observed_sums(ch):
    """P2 — at every month-end candidate day, the argMax-deduplicated balances of
    each served attempt sum to that publication's own observed_sum_raw. Proves the
    FINAL-free dedup and the attempt pin across the whole history."""
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    if not _treasury_db_reachable(ch):
        pytest.skip("rpc_state_indexer.census_publications not reachable")
    sql = gov._compact_sql(f"""WITH {gov._treasury_month_candidates("1")},
{gov._treasury_month_served("1")},
bal AS (
  SELECT chain_id AS c, token_address AS t, snapshot_date AS d, attempt_id AS a,
         holder_address AS h, argMax(balance_raw, insert_version) AS v
  FROM {gov._tq(gov.TREASURY_BALANCES_TABLE)}
  WHERE job_name = '{gov.TREASURY_JOB}' AND snapshot_date IN (SELECT c_date FROM cand)
    AND (chain_id, token_address, snapshot_date, attempt_id)
        IN (SELECT sm_chain, sm_token, sm_date, sm_attempt FROM served_m)
  GROUP BY c, t, d, a, h),
sums AS (SELECT c, t, d, a, sum(v) AS s FROM bal GROUP BY c, t, d, a),
pubs AS (
  SELECT chain_id AS c, target_address AS t, snapshot_date AS d, attempt_id AS a,
         any(observed_sum_raw) AS o
  FROM {gov._tq(gov.TREASURY_PUB_TABLE)}
  WHERE job_name = '{gov.TREASURY_JOB}' AND target_kind = 'token'
    AND snapshot_date IN (SELECT c_date FROM cand)
  GROUP BY c, t, d, a)
SELECT count() AS attempts, countIf(p.o IS NOT NULL AND s.s != p.o) AS mismatches
FROM sums AS s INNER JOIN pubs AS p ON s.c = p.c AND s.t = p.t AND s.d = p.d AND s.a = p.a
ORDER BY attempts""")
    attempts, mismatches = _rows(ch, sql)[0]
    assert attempts > 10_000, attempts
    assert mismatches == 0, f"{mismatches} of {attempts} attempts disagree with observed_sum_raw"


def test_treasury_nav_matches_an_independent_canonical_valuation(ch):
    """P3 — each chain's summary nav_usd equals an independent valuation: the
    canonical view at the same as-of, registry-priced tokens, hub price that day.
    Carried tokens (valued at their own earlier day) are the only allowed gap."""
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    if not _treasury_db_reachable(ch):
        pytest.skip("rpc_state_indexer.census_publications not reachable")
    summary = next(s for s in gov._treasury_specs(gov._range_state("", ""), gov._default_filters())
                   if s.key == "treasury_summary")
    result = _treasury_budget_rows(ch, summary.sql, summary.parameters)
    cols = {name: i for i, name in enumerate(result.columns)}
    for row in result.rows:
        chain, as_of = row[cols["chain_id"]], row[cols["as_of"]]
        if as_of is None:
            continue
        sql = gov._compact_sql(f"""WITH {sql_loader_registry()}
SELECT sum(toFloat64(v.balance_raw) / pow(10, r.reg_dec) * h.price) AS nav
FROM {gov._tq(gov.TREASURY_CANONICAL_VIEW)} AS v
INNER JOIN reg AS r ON r.reg_chain = v.chain_id AND r.reg_token = v.token_address
INNER JOIN (SELECT upper(symbol) AS hs, date AS hd, price FROM {gov.TREASURY_PRICE_HUB}) AS h
        ON h.hs = r.reg_psym AND h.hd = v.snapshot_date
WHERE v.job_name = '{gov.TREASURY_JOB}' AND v.chain_id = {int(chain)}
  AND v.snapshot_date = toDate({{as_of:String}}) AND v.balance_raw != 0
  AND r.reg_role = 'priced' AND v.snapshot_date >= r.reg_from AND v.snapshot_date < r.reg_to
ORDER BY nav""")
        params = gov._treasury_binds(sql, {"as_of": str(as_of)})
        independent = float(_rows(ch, sql, params)[0][0] or 0)
        nav = float(row[cols["nav_usd"]] or 0)
        tolerance = 1e-6 if int(row[cols["carried_tokens"]] or 0) == 0 else 0.02
        assert abs(nav - independent) <= tolerance * max(nav, 1.0), (chain, nav, independent)


def sql_loader_registry() -> str:
    from cerebro_mcp.tools.visualization import sql_loader

    return sql_loader.load_sql("governance", "_cte_treasury_registry")


def test_treasury_registry_agrees_with_whitelist_hub_and_metadata(ch):
    """The registry is hand-reviewed data; the live plane checks it: Gnosis entries
    agree with dbt.tokens_whitelist (symbol, decimals, Monerium windows), every
    price symbol has a recent hub price, and registry decimals equal the indexer's
    resolved metadata."""
    from cerebro_mcp.tools.visualization import treasury_registry as registry

    if not _treasury_db_reachable(ch):
        pytest.skip("rpc_state_indexer.census_publications not reachable")
    whitelist = {
        str(addr).lower(): (str(sym), int(dec), str(start), str(end) if end else None)
        for addr, sym, dec, start, end in _rows(
            ch, "SELECT address, symbol, decimals, toString(date_start), toString(date_end) "
                "FROM dbt.tokens_whitelist ORDER BY address")
    }
    problems: list[str] = []
    for entry in registry.TOKENS:
        if entry.chain_id != 100 or entry.address not in whitelist:
            continue
        sym, dec, _start, end = whitelist[entry.address]
        if dec != entry.decimals:
            problems.append(f"{entry.address}: decimals {entry.decimals} != whitelist {dec}")
        if entry.role == "priced" and entry.price_basis == "direct" and sym.upper() != entry.price_symbol:
            problems.append(f"{entry.address}: hub symbol {entry.price_symbol} != whitelist {sym}")
        if end and entry.role == "priced" and entry.valid_to != end:
            problems.append(f"{entry.address}: valid_to {entry.valid_to} != whitelist end {end}")
    latest = dict(_rows(
        ch, "SELECT upper(symbol), toString(max(date)) FROM dbt.int_execution_token_prices_daily "
            "GROUP BY upper(symbol) ORDER BY 1"))
    hub_max = max(latest.values())
    for sym in registry.bind_params()["hub_syms"]:
        if sym not in latest:
            problems.append(f"hub has no series for {sym}")
        elif latest[sym] < hub_max:
            problems.append(f"hub series {sym} ends {latest[sym]} (hub latest {hub_max})")
    meta = {
        (int(c), str(t)): d
        for c, t, d in _rows(
            ch, "SELECT chain_id, token_address, decimals FROM rpc_state_indexer.v_token_metadata_current "
                "WHERE resolution_status = 'resolved' ORDER BY chain_id, token_address")
    }
    for entry in registry.TOKENS:
        observed = meta.get((entry.chain_id, entry.address))
        if observed is not None and int(observed) != entry.decimals:
            problems.append(f"{entry.chain_id}:{entry.address}: decimals {entry.decimals} != metadata {observed}")
    assert not problems, "\n".join(problems)


def test_treasury_spam_fixtures_match_in_clickhouse(ch):
    """The RE2 classifier is authoritative; it must agree with the shared fixture
    table the Python twin is tested against (tests/treasury_spam_fixtures.py)."""
    from cerebro_mcp.tools.visualization import governance_explorer as gov
    from cerebro_mcp.tools.visualization import sql_loader
    from cerebro_mcp.tools.visualization import treasury_registry as registry
    from tests.treasury_spam_fixtures import TREASURY_SPAM_FIXTURES as fixtures

    expr = sql_loader.load_sql(
        "governance", "_expr_treasury_spam_reason", max_symbol_len=registry.MAX_SYMBOL_LEN,
        max_name_len=registry.MAX_NAME_LEN, mass_min_wallets=registry.MASS_AIRDROP_MIN_WALLETS,
        mass_share=registry.MASS_AIRDROP_SHARE,
    )
    sql = gov._compact_sql(f"""SELECT fx_i, {expr} AS reason FROM (
  SELECT tupleElement(f, 1) AS fx_i, tupleElement(f, 2) AS x_role, tupleElement(f, 3) AS x_symbol,
         tupleElement(f, 4) AS x_name, tupleElement(f, 5) AS x_wallets, tupleElement(f, 6) AS x_active
  FROM (SELECT arrayJoin(arrayZip({{fx_i:Array(UInt32)}}, {{fx_role:Array(String)}},
        {{fx_symbol:Array(String)}}, {{fx_name:Array(String)}}, {{fx_wallets:Array(UInt32)}},
        {{fx_active:Array(UInt32)}})) AS f))
ORDER BY fx_i""")
    roles = []
    for chain, address, *_ in fixtures:
        entry = registry.entry_at(chain, address, registry.PRESENT)
        roles.append(entry.role if entry else "")
    params = gov._treasury_binds(sql, {
        "fx_i": list(range(len(fixtures))), "fx_role": roles,
        "fx_symbol": [f[2] for f in fixtures], "fx_name": [f[3] for f in fixtures],
        "fx_wallets": [f[4] for f in fixtures], "fx_active": [f[5] for f in fixtures],
    })
    got = {int(i): reason for i, reason in _rows(ch, sql, params)}
    mismatches = [(fixtures[i], got.get(i)) for i in range(len(fixtures)) if got.get(i) != fixtures[i][6]]
    assert not mismatches, mismatches


def test_treasury_named_fakes_are_hidden_and_the_class_partition_is_total(ch):
    """The observed spoofs stay hidden, the real assets stay priced, and every held
    token lands in exactly one class (nothing silently vanishes)."""
    from cerebro_mcp.tools.visualization import governance_explorer as gov

    if not _treasury_db_reachable(ch):
        pytest.skip("rpc_state_indexer.census_publications not reachable")
    specs = {s.key: s for s in gov._treasury_specs(gov._range_state("", ""), gov._default_filters())}
    holdings = _treasury_budget_rows(ch, specs["treasury_holdings"].sql, specs["treasury_holdings"].parameters)
    cols = {name: i for i, name in enumerate(holdings.columns)}
    klass = {(int(r[cols["chain_id"]]), r[cols["token_address"]]): r[cols["token_class"]] for r in holdings.rows}
    fakes = [(1, "0x357eb8dc76920a7a00d8e3059cdb0249aceb2df7"), (1, "0x289d5488ab09f43471914e572ec9e3651c735af2"),
             (1, "0x7452e3fc2fe611c6b7761c6c393bece059881ac7"), (1, "0x14f01f4fd1028997fb87573c2602fc121d07a07f")]
    for key in fakes:
        if key in klass:
            assert klass[key] == "spam", key
    for key in ((1, "0x6810e776880c02933d47db1b9fc05908e5386b96"), (100, "0x9c58bacc331c9aa871afd802db6379a98e80cedb")):
        assert klass.get(key) == "priced", key
    summary = _treasury_budget_rows(ch, specs["treasury_summary"].sql, specs["treasury_summary"].parameters)
    scols = {name: i for i, name in enumerate(summary.columns)}
    for row in summary.rows:
        chain = int(row[scols["chain_id"]])
        held = [c for (ch_, _t), c in klass.items() if ch_ == chain]
        total = sum(int(row[scols[k]] or 0) for k in (
            "priced_tokens", "listed_tokens", "unverified_tokens", "hidden_spam_tokens",
            "hidden_retired_tokens"))
        assert total == len(held), (chain, total, len(held))
