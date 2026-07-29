"""The .sql template loader.

The loader's whole job is to keep two brace-shaped namespaces apart:
``@fragment`` (Python-side composition, substituted here) and ``{name:Type}``
(ClickHouse bound parameters, passed through). Every test below is about that
separation or about failing loudly when a template and its caller drift.
"""

from __future__ import annotations

import re

import pytest

from cerebro_mcp.tools.visualization import sql_loader
from cerebro_mcp.tools.visualization.sql_loader import SqlTemplateError, load_sql


@pytest.fixture(autouse=True)
def _fresh_cache():
    sql_loader.reset_cache_for_tests()
    yield
    sql_loader.reset_cache_for_tests()


@pytest.fixture()
def template(tmp_path, monkeypatch):
    """Write a template into a throwaway queries/ root."""
    def write(app: str, name: str, body: str) -> None:
        directory = tmp_path / app
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.sql").write_text(body, encoding="utf-8")
        monkeypatch.setattr(sql_loader, "QUERIES_DIR", tmp_path)
        sql_loader.reset_cache_for_tests()
    return write


def test_clickhouse_bound_parameters_pass_through_untouched(template):
    """The entire reason for the migration. In an f-string every one of these
    had to be doubled to ``{{env:String}}``; here they are written natively and
    the loader must not touch them."""
    template("app", "q", "SELECT {env:String}, {chain_id:UInt64}, {n:UInt32}\n")
    out = load_sql("app", "q")
    assert out == "SELECT {env:String}, {chain_id:UInt64}, {n:UInt32}"
    assert "{{" not in out


def test_fragments_are_substituted(template):
    template("app", "q", "SELECT * FROM @src WHERE @pred\n")
    assert load_sql("app", "q", src="db.t", pred="a = 1") == "SELECT * FROM db.t WHERE a = 1"


def test_a_longer_fragment_name_is_not_split_by_a_shorter_one(template):
    """`@chain` must not substitute inside `@chain_sql` and leave a stray
    `_sql` behind — a corruption that still parses as SQL in some positions."""
    template("app", "q", "SELECT @chain_sql AND @chain\n")
    assert load_sql("app", "q", chain_sql="chain_id = 1", chain="c") == "SELECT chain_id = 1 AND c"


def test_a_missing_fragment_raises_rather_than_shipping_an_at_sign(template):
    """An unsubstituted `@chain_sql` reaches ClickHouse as a syntax error at
    whatever moment that code path first runs. Fail at render time instead."""
    template("app", "q", "SELECT * FROM @src WHERE @pred\n")
    with pytest.raises(SqlTemplateError, match=r"needs fragments.*pred"):
        load_sql("app", "q", src="db.t")


def test_an_unused_fragment_raises_rather_than_silently_dropping_a_filter(template):
    """The dangerous direction: a caller passes `ltd_sql=` , the template no
    longer references it, and the exclusion silently stops applying."""
    template("app", "q", "SELECT * FROM @src\n")
    with pytest.raises(SqlTemplateError, match=r"does not use.*ltd_sql"):
        load_sql("app", "q", src="db.t", ltd_sql="x != 1")


def test_none_is_rejected_rather_than_rendered_as_the_word_None(template):
    """`None` stringifies to `None`, which is a perfectly valid-looking column
    name — the query would run and return the wrong thing."""
    template("app", "q", "SELECT @col\n")
    with pytest.raises(SqlTemplateError, match="is None"):
        load_sql("app", "q", col=None)


def test_a_missing_template_names_the_file_it_looked_for(template):
    template("app", "q", "SELECT 1\n")
    with pytest.raises(SqlTemplateError, match=r"app/nope\.sql"):
        load_sql("app", "nope")


def test_exactly_one_trailing_newline_is_stripped(template):
    """Editors add one; the f-strings did not have it. Anything beyond the
    first is real content and must survive."""
    template("app", "one", "SELECT 1\n")
    assert load_sql("app", "one") == "SELECT 1"
    template("app", "two", "SELECT 1\n\n")
    assert load_sql("app", "two") == "SELECT 1\n"
    template("app", "none", "SELECT 1")
    assert load_sql("app", "none") == "SELECT 1"


def test_a_leading_blank_line_is_preserved(template):
    """Most f-strings opened with a newline (`f\"\"\"\\nSELECT ...`). Byte
    fidelity with the pre-migration SQL depends on keeping it."""
    template("app", "q", "\nSELECT 1\n")
    assert load_sql("app", "q") == "\nSELECT 1"


def test_integers_and_other_scalars_stringify(template):
    template("app", "q", "SELECT * FROM t LIMIT @cap\n")
    assert load_sql("app", "q", cap=200) == "SELECT * FROM t LIMIT 200"


# ---------------------------------------------------------------------------
# The shipped templates
# ---------------------------------------------------------------------------


def test_shipped_templates_never_double_their_bound_parameters():
    """`{{env:String}}` in a .sql file is an f-string habit carried across by
    mistake — ClickHouse would receive a literal brace and fail to parse."""
    for app in sql_loader.apps():
        for name in sql_loader.available(app):
            text = (sql_loader.QUERIES_DIR / app / f"{name}.sql").read_text()
            assert "{{" not in text, f"{app}/{name}.sql has doubled braces"
            assert "}}" not in text, f"{app}/{name}.sql has doubled braces"


def test_shipped_templates_use_only_lowercase_fragment_names():
    """The loader's token regex only matches lowercase; an uppercase `@Foo`
    would sit in the file looking like a fragment and never be substituted."""
    stray = re.compile(r"@(?![a-z_])")
    for app in sql_loader.apps():
        for name in sql_loader.available(app):
            text = (sql_loader.QUERIES_DIR / app / f"{name}.sql").read_text()
            assert not stray.search(text), f"{app}/{name}.sql has a non-fragment @"
