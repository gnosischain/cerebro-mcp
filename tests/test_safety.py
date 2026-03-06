import pytest
from cerebro_mcp.safety import validate_query, validate_identifier, ensure_limit


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
        assert result.count("LIMIT") == 1

    def test_handles_trailing_semicolon(self):
        result = ensure_limit("SELECT * FROM t;", 100)
        assert "LIMIT 100" in result
        assert not result.rstrip().endswith(";")

    def test_case_insensitive_limit_detection(self):
        result = ensure_limit("SELECT * FROM t limit 50", 100)
        assert result.count("LIMIT") + result.count("limit") == 1
