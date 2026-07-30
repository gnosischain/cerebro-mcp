import pytest
from cerebro_mcp.safety import validate_query, validate_identifier, ensure_limit, extract_table_names


class TestValidateQuery:
    def test_valid_select(self):
        valid, err = validate_query("SELECT 1")
        assert valid
        assert err == ""

    def test_valid_select_with_from(self):
        valid, err = validate_query("SELECT * FROM dbt.my_table WHERE id = 1")
        assert valid

    def test_valid_cte(self):
        valid, err = validate_query(
            "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte"
        )
        assert valid

    def test_valid_explain(self):
        valid, err = validate_query("EXPLAIN SELECT * FROM my_table")
        assert valid

    def test_valid_describe(self):
        valid, err = validate_query("DESCRIBE TABLE my_table")
        assert valid

    def test_valid_show(self):
        valid, err = validate_query("SHOW TABLES")
        assert valid

    def test_valid_exists(self):
        valid, err = validate_query("EXISTS TABLE my_table")
        assert valid

    def test_reject_insert(self):
        valid, err = validate_query("INSERT INTO my_table VALUES (1, 2)")
        assert not valid
        assert "INSERT" in err

    def test_reject_drop(self):
        valid, err = validate_query("DROP TABLE my_table")
        assert not valid
        assert "DROP" in err

    def test_reject_alter(self):
        valid, err = validate_query("ALTER TABLE my_table ADD COLUMN x Int32")
        assert not valid

    def test_reject_delete(self):
        valid, err = validate_query("DELETE FROM my_table WHERE id = 1")
        assert not valid

    def test_reject_create(self):
        valid, err = validate_query("CREATE TABLE my_table (id Int32)")
        assert not valid

    def test_reject_truncate(self):
        valid, err = validate_query("TRUNCATE TABLE my_table")
        assert not valid

    def test_reject_system(self):
        valid, err = validate_query("SYSTEM FLUSH LOGS")
        assert not valid

    def test_reject_multi_statement(self):
        valid, err = validate_query("SELECT 1; DROP TABLE my_table")
        assert not valid
        assert "Multiple" in err

    def test_allow_trailing_semicolon(self):
        valid, err = validate_query("SELECT 1;")
        assert valid

    def test_reject_empty(self):
        valid, err = validate_query("")
        assert not valid

    def test_reject_too_long(self):
        valid, err = validate_query("SELECT " + "x" * 20000, max_length=10000)
        assert not valid
        assert "length" in err

    def test_keyword_in_string_literal_allowed(self):
        valid, err = validate_query("SELECT * FROM t WHERE name = 'DELETE THIS'")
        assert valid

    def test_reject_select_into_outfile(self):
        valid, err = validate_query("SELECT * FROM t INTO OUTFILE '/tmp/data.csv'")
        assert not valid

    def test_reject_grant(self):
        valid, err = validate_query("GRANT SELECT ON db.* TO user")
        assert not valid

    def test_reject_format_clause(self):
        valid, err = validate_query("SELECT * FROM t FORMAT JSON")
        assert not valid
        assert "pattern" in err

    def test_reject_settings_clause(self):
        valid, err = validate_query("SELECT * FROM t SETTINGS max_threads = 8")
        assert not valid
        assert "pattern" in err

    def test_reject_external_table_functions(self):
        for sql in (
            "SELECT * FROM s3('https://example.com/file.csv')",
            "SELECT * FROM remote('cluster', db, t)",
            "SELECT * FROM url('https://example.com', 'CSV')",
        ):
            valid, err = validate_query(sql)
            assert not valid
            assert "pattern" in err

    def test_valid_subquery(self):
        valid, err = validate_query(
            "SELECT * FROM (SELECT 1 AS x) WHERE x = 1"
        )
        assert valid

    def test_reject_update(self):
        valid, err = validate_query("UPDATE my_table SET x = 1 WHERE id = 1")
        assert not valid


class TestValidateIdentifier:
    def test_valid_name(self):
        valid, err = validate_identifier("my_table")
        assert valid

    def test_valid_alphanumeric(self):
        valid, err = validate_identifier("table123")
        assert valid

    def test_reject_spaces(self):
        valid, err = validate_identifier("my table")
        assert not valid

    def test_reject_semicolon(self):
        valid, err = validate_identifier("table;DROP")
        assert not valid

    def test_reject_empty(self):
        valid, err = validate_identifier("")
        assert not valid

    def test_reject_dots(self):
        valid, err = validate_identifier("db.table")
        assert not valid


class TestEnsureLimit:
    def test_adds_limit_when_missing(self):
        result = ensure_limit("SELECT * FROM t", 100)
        assert "LIMIT 100" in result

    def test_preserves_existing_limit(self):
        result = ensure_limit("SELECT * FROM t LIMIT 50", 100)
        assert result.startswith("SELECT * FROM (SELECT * FROM t LIMIT 50)")
        assert result.endswith("LIMIT 100")

    def test_handles_trailing_semicolon(self):
        result = ensure_limit("SELECT * FROM t;", 100)
        assert "LIMIT 100" in result
        assert not result.rstrip().endswith(";")

    def test_case_insensitive_limit_detection(self):
        result = ensure_limit("SELECT * FROM t limit 50", 100)
        assert result.startswith("SELECT * FROM (SELECT * FROM t limit 50)")
        assert result.endswith("LIMIT 100")


class TestExtractTableNames:
    def test_simple_from(self):
        assert extract_table_names("SELECT * FROM users") == ["users"]

    def test_qualified_table(self):
        assert extract_table_names("SELECT * FROM dbt.api_tx_daily") == ["dbt.api_tx_daily"]

    def test_join(self):
        result = extract_table_names(
            "SELECT a.x FROM table_a a JOIN table_b b ON a.id = b.id"
        )
        assert result == ["table_a", "table_b"]

    def test_cerebro_alias_excluded(self):
        result = extract_table_names(
            "SELECT * FROM (SELECT * FROM real_table) AS _cerebro_limit LIMIT 100"
        )
        assert result == ["real_table"]

    def test_cte(self):
        assert "source" in extract_table_names(
            "WITH cte AS (SELECT * FROM source) SELECT * FROM cte"
        )

    def test_no_from(self):
        assert extract_table_names("SELECT 1") == []

    def test_string_literal_ignored(self):
        result = extract_table_names("SELECT * FROM users WHERE x = 'FROM fake'")
        assert result == ["users"]

    def test_dedup(self):
        assert extract_table_names("SELECT * FROM t JOIN t ON t.a = t.b") == ["t"]

    def test_multi_join(self):
        sql = "SELECT * FROM a LEFT JOIN b ON 1=1 RIGHT JOIN c ON 1=1"
        assert extract_table_names(sql) == ["a", "b", "c"]


class TestInternalOnlyTables:
    """Verify that execute_query rejects raw SQL referring to internal-only
    bridge tables. These hold raw addresses + pseudonyms together; querying
    them would defeat the pseudonymization boundary.
    """

    def test_reject_ga_bridge_unqualified(self):
        valid, err = validate_query(
            "SELECT * FROM int_execution_gnosis_app_user_identity_bridge LIMIT 10"
        )
        assert not valid
        assert "internal-only" in err.lower()

    def test_reject_ga_bridge_qualified(self):
        valid, err = validate_query(
            "SELECT address, user_pseudonym "
            "FROM dbt.int_execution_gnosis_app_user_identity_bridge LIMIT 10"
        )
        assert not valid
        assert "internal-only" in err.lower()

    def test_reject_gp_bridge(self):
        valid, err = validate_query(
            "SELECT * FROM int_execution_gpay_user_identity_bridge"
        )
        assert not valid
        assert "internal-only" in err.lower()

    def test_reject_bridge_in_join(self):
        valid, err = validate_query(
            "SELECT t.user_pseudonym, b.address "
            "FROM dbt.fct_execution_gnosis_app_attribution_30d t "
            "JOIN dbt.int_execution_gnosis_app_user_identity_bridge b "
            "  ON b.user_pseudonym = t.user_pseudonym"
        )
        assert not valid
        assert "internal-only" in err.lower()

    def test_allow_pseudonym_only_marts(self):
        # The MTA marts hold only user_pseudonym (no raw addresses) and
        # should be queryable normally.
        valid, err = validate_query(
            "SELECT * FROM dbt.int_execution_gnosis_app_conversions LIMIT 10"
        )
        assert valid
        valid, err = validate_query(
            "SELECT * FROM dbt.fct_execution_gpay_attribution_30d LIMIT 10"
        )
        assert valid

    def test_allow_pseudonym_table_in_join(self):
        valid, err = validate_query(
            "SELECT a.event_kind, b.conversion_kind "
            "FROM dbt.int_execution_gnosis_app_user_events_unified a "
            "JOIN dbt.int_execution_gnosis_app_conversions b "
            "  ON a.user_pseudonym = b.user_pseudonym "
            "LIMIT 10"
        )
        assert valid


def test_the_system_refusal_names_the_tools_that_do_the_job():
    """"Forbidden keyword detected: SYSTEM" is the platform's most common error —
    14 occurrences across the last 6 deployed sessions.

    Measured in one live Desktop session: the caller queried `system.tables`, got
    the bare refusal, retried `system.tables` with a different WHERE, tried
    `information_schema` (also blocked), tried an unqualified table name
    (UNKNOWN_TABLE), and only then reached `list_tables`. Four wasted round-trips
    rediscovering a tool the server already exposes.

    A refusal that does not say what to do instead invites exactly that retry
    loop, so the message must carry the alternative.
    """
    ok, message = validate_query("SELECT name FROM system.tables", 10000)
    assert ok is False
    assert "SYSTEM" in message
    for tool in ("list_tables", "describe_table", "search_models"):
        assert tool in message, f"the refusal should point at {tool}"


def test_only_reachable_keywords_carry_a_hint():
    """An entry for a keyword that is not actually forbidden can never fire, and
    reads as guidance that exists when it does not. `SHOW` was exactly that — it
    is an ALLOWED prefix, so a hint keyed on it was dead the moment it was
    written."""
    from cerebro_mcp.safety import (
        ALLOWED_PREFIXES,
        FORBIDDEN_KEYWORDS,
        _KEYWORD_ALTERNATIVES,
    )

    unreachable = [k for k in _KEYWORD_ALTERNATIVES if k not in FORBIDDEN_KEYWORDS]
    assert unreachable == [], f"{unreachable} can never be emitted"
    for keyword in _KEYWORD_ALTERNATIVES:
        assert keyword not in ALLOWED_PREFIXES


def test_a_write_keyword_gets_no_alternative():
    """Only SYSTEM redirects. INSERT/DROP/GRANT are genuine writes with no
    read-only equivalent, and inventing one would be misleading."""
    for sql in ("WITH x AS (SELECT 1) SELECT * FROM x WHERE 1 IN (SELECT 1) /* DROP */",):
        pass
    ok, message = validate_query("SELECT 1 UNION ALL SELECT 1 FROM t GRANT", 10000)
    assert ok is False
    assert "list_tables" not in message


def test_a_clean_query_is_still_allowed():
    ok, message = validate_query(
        "SELECT * FROM dbt.api_execution_transactions_total", 10000
    )
    assert ok is True and message == ""
