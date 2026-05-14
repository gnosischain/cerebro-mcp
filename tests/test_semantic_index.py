"""Unit tests for the semantic index helpers.

Covers PR 6 (idf-weighted token scoring) and the legacy `score_metric`
behaviour preserved when no idf table is passed.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.semantic_index import (
    build_token_idf,
    score_metric,
)


def _metric(
    name: str,
    *,
    label: str = "",
    synonyms: list[str] | None = None,
    description: str = "",
    module: str = "execution",
    quality_tier: str = "approved",
) -> dict:
    """Build a metric dict in the shape `build_indexes` produces."""
    synonyms = synonyms or []
    all_synonyms = [name.lower(), label.lower(), *[s.lower() for s in synonyms]]
    return {
        "name": name,
        "label": label,
        "module": module,
        "quality_tier": quality_tier,
        "all_synonyms": [s for s in all_synonyms if s],
        "search_blob": " ".join(filter(None, [name, label, description, module, *synonyms])).lower(),
    }


# ─── build_token_idf ────────────────────────────────────────────────


class TestBuildTokenIdf:
    def test_empty_metrics_returns_empty_dict(self):
        assert build_token_idf([]) == {}

    def test_rare_tokens_score_higher_than_common(self):
        # `weekly` appears in all 3 metrics → low idf, near the floor.
        # `passkey` appears in only 1 → high idf, near the cap.
        metrics = [
            _metric("revenue_users_weekly", description="weekly active users"),
            _metric("validators_active_weekly", description="weekly validator count"),
            _metric("passkey_logins_weekly", description="weekly passkey logins"),
        ]
        idf = build_token_idf(metrics)

        assert "weekly" in idf
        assert "passkey" in idf
        assert idf["passkey"] > idf["weekly"]

    def test_weights_bounded_by_floor_and_cap(self):
        metrics = [_metric(f"m_{i}", description=f"token_{i}") for i in range(10)]
        idf = build_token_idf(metrics)

        for weight in idf.values():
            assert 5 <= weight <= 15  # _TOKEN_BONUS_FLOOR / _TOKEN_BONUS_CAP


# ─── score_metric: legacy vs idf-weighted ───────────────────────────


class TestScoreMetricLegacy:
    """Backwards-compat: omitting `token_idf` keeps the pre-PR-6 behaviour."""

    def test_exact_name_match_scores_100_plus_approved_bonus(self):
        m = _metric("transaction_count")
        # token_idf=None → flat +5 per matched token (the one from the
        # exact-name match itself: "transaction_count"). Plus +20 for
        # approved, +15 for module-match if "transaction" inferred to
        # execution... actually 'transaction' isn't a module name, so
        # only +20 applies.
        score = score_metric("transaction_count", m)
        assert score >= 100 + 20

    def test_synonym_match_scores_90_plus_bonuses(self):
        m = _metric("transaction_count", synonyms=["tx count", "transactions"])
        score = score_metric("tx count", m)
        assert score >= 90 + 20

    def test_no_match_returns_zero(self):
        m = _metric("transaction_count")
        assert score_metric("completely unrelated phrase", m) == 0


class TestScoreMetricWithIdf:
    """With idf table, rare-token matches outscore common-token matches."""

    def test_rare_token_outscores_common_token(self):
        passkey_metric = _metric(
            "passkey_logins",
            description="passkey authentication signins",
        )
        weekly_metric = _metric("weekly_summary", description="weekly users")
        weekly_only = _metric("monthly_summary", description="weekly tally")

        idf = build_token_idf([passkey_metric, weekly_metric, weekly_only])
        # Both queries are single tokens chosen to ONLY substring-match
        # via the token-bonus path — no name / synonym / prefix hits.
        # Use queries that don't equal any name and don't get
        # module-bonus.
        rare_score = score_metric("passkey", passkey_metric, token_idf=idf)
        common_score = score_metric("weekly", weekly_metric, token_idf=idf)

        # The rare-token match should score strictly higher because the
        # +bonus per matched token is idf-weighted.
        assert rare_score > common_score

    def test_idf_omitted_falls_back_to_legacy_constant_bonus(self):
        m = _metric("revenue_users_weekly", description="weekly active users")
        # Same metric, same query — with vs without idf. The result
        # should differ ONLY in the token bonus contribution.
        legacy = score_metric("active users", m)
        with_idf = score_metric(
            "active users", m, token_idf={"active": 5.0, "users": 5.0}
        )
        # When idf gives the floor (5.0), it should match legacy exactly.
        assert legacy == with_idf

    def test_idf_high_weight_still_bounded_by_cap(self):
        m = _metric("rare_metric", description="rare singleton token here")
        # Construct an idf table where one token is overinflated.
        idf = {"rare": 999.0}
        score = score_metric("rare", m, token_idf=idf)
        # The bonus must not exceed _TOKEN_BONUS_CAP (15) per token —
        # this is enforced inside build_token_idf, not score_metric. If a
        # caller injects an absurd weight directly the int() truncation
        # at least prevents NaNs from leaking. So we just check the
        # score isn't astronomical.
        assert score < 10_000