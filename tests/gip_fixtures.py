"""Shared GIP title-identity fixture table — WL-039.

dbt macros/governance/parse_gip_number.sql is the definition of record; the
three dialect renderings (SQL/RE2, Python, JS) cannot share one string, so
THIS table is what pins them together. It is evaluated in each engine:

- Python: test_governance_explorer.test_gip_extraction_exact_patterns_only
- ClickHouse RE2 (authoritative for the SQL dialect):
  test_governance_live_smoke.test_gip_fixture_table_matches_in_clickhouse
- JS: ui/src/mini-apps/governance/__tests__/gip.test.ts mirrors these rows
  verbatim (content-identical by hand; each file points at the other).

Expected values were derived by EVALUATION against the canonical pattern,
never by intuition — "(GIP-7)" is the cautionary case: the paren-prefix
branch cannot both consume the token and still match GIP after it, so a
title that is nothing but a parenthesised mention is NOT that GIP.
Expectations include the gip_number > 0 phantom guard (GIP-0 -> None).
"""

GIP_TITLE_FIXTURES: list[tuple[str, int | None]] = [
    # Plain identities.
    ("GIP-151: Should GnosisDAO fund X", 151),
    ("GIP 152 - Treasury topup", 152),
    ("gip-128", 128),
    ("GIP-0042 legacy numbering", 42),
    ("GIP - 77", 77),
    # No digit cap, no trailing boundary.
    ("GIP-1234567", 1234567),
    ("GIP-151abc", 151),
    # Canonical prefixes before the identity.
    ("[Draft] GIP-90: x", 90),
    ("(Signaling) GIP-64 vote", 64),
    ("# GIP-12", 12),
    ("​GIP-45", 45),
    ("Redo of: GIP-33", 33),
    ("Re-do of: GIP-33", 33),
    ("[RE-RUN] (redo) GIP-8", 8),
    # Mid-title mentions are NOT identities (anchored pattern).
    ("discussing gip-128 here", None),
    ("Re: GIP-33 follow-up", None),
    ("(GIP-7)", None),
    # Other DAOs' numbering and non-matches.
    ("AGIP-5 is another DAO's numbering", None),
    ("preGIP-9", None),
    ("GIP:151 colon is not a separator", None),
    ("no token here", None),
    ("", None),
    # Phantom guard: GIP-0 is never an identity.
    ("GIP-0", None),
]
