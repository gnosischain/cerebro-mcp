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
    gip_sql = r"toInt32OrNull(extract(title, '(?i)\\bGIP[\\s-]?0*([0-9]+)'))"
    proposals = int(_scalar(ch, f"""
SELECT countIf({gip_sql} IS NOT NULL)
FROM governance_db.snapshot_proposals FINAL"""))
    topics = int(_scalar(ch, f"""
SELECT countIf({gip_sql} IS NOT NULL)
FROM governance_db.forum_topics FINAL"""))
    assert proposals > 0
    assert topics > 0


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
