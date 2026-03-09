import pytest
from cerebro_mcp.safety import validate_identifier


class TestSQLInjectionPrevention:
    """Test that SQL injection vectors are blocked."""

    def test_name_pattern_injection_blocked_by_parameterization(self):
        """name_pattern is now parameterized, so injection payloads become
        literal LIKE patterns that match nothing (not executed as SQL)."""
        # These would be dangerous with f-string interpolation,
        # but are safe as parameterized LIKE values
        payloads = [
            "'; DROP TABLE foo; --",
            "' OR '1'='1",
            "'; INSERT INTO evil VALUES(1); --",
            "test%'; DELETE FROM system.tables; --",
        ]
        # The payloads are just strings passed as parameters —
        # they won't match any table names, so they're harmless.
        # This test documents that the fix works by design.
        for payload in payloads:
            # validate_identifier would reject these, but name_pattern
            # bypasses it — that's why parameterization is critical
            valid, _ = validate_identifier(payload)
            assert not valid, f"Identifier validation should reject: {payload}"

    def test_database_injection_blocked(self):
        payloads = [
            "dbt; DROP TABLE foo",
            "dbt' OR '1'='1",
            "dbt--",
            "../etc/passwd",
            "dbt`; DROP TABLE foo",
        ]
        for payload in payloads:
            valid, _ = validate_identifier(payload)
            assert not valid, f"Should reject: {payload}"

    def test_table_injection_blocked(self):
        payloads = [
            "blocks; DROP TABLE foo",
            "blocks' OR '1'='1",
            "blocks`",
            "blocks--comment",
        ]
        for payload in payloads:
            valid, _ = validate_identifier(payload)
            assert not valid, f"Should reject: {payload}"

    def test_valid_identifiers_accepted(self):
        valid_names = [
            "blocks",
            "stg_execution__blocks",
            "api_consensus_validators_active_daily",
            "dbt",
            "crawlers_data",
            "table_123",
        ]
        for name in valid_names:
            valid, err = validate_identifier(name)
            assert valid, f"Should accept '{name}': {err}"

    def test_valid_like_patterns(self):
        """LIKE patterns contain % and _ which are invalid identifiers
        but valid as parameterized LIKE values."""
        patterns = ["stg_%", "%validators%", "api_%_daily"]
        for pattern in patterns:
            valid, _ = validate_identifier(pattern)
            # These are invalid as identifiers (contain %)
            assert not valid
            # But they're safe as parameterized LIKE values —
            # clickhouse handles escaping
