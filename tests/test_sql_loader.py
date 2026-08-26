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


# ---------------------------------------------------------------------------
# Fragment comment stripping
# ---------------------------------------------------------------------------


def test_comment_headers_are_stripped_from_every_template():
    """A .sql file carries its rationale for whoever OPENS it; none of that prose is
    sent to ClickHouse.

    An earlier version stripped fragments only, keeping whole-query headers on the
    theory that they show up usefully in `system.query_log`. Measured, that was a bad
    trade: `settings.MAX_QUERY_LENGTH` is 10,000 and inlining the headers took the
    longest cow statement from 9,523 to 9,852 characters. One more chain in
    COW_CHAINS would have failed `trader_activity` at all-networks scope with a
    validation error that pointed at nothing. Stripping universally recovered the
    budget (longest is now 9,114) and made rendered output byte-identical to the
    Python literals it replaced, so the refactor is verifiable by comparing bytes.
    """
    frag = sql_loader.load_sql("cow", "_anchor_trades", scope="1")
    assert "--" not in frag
    assert frag.startswith("SELECT max(block_timestamp)")

    whole = sql_loader.load_sql(
        "cow", "activity", shared_ctes="C", activity_union="A"
    )
    assert "--" not in whole
    assert whole == "WITH C\nSELECT * FROM (\nA\n) ORDER BY bucket,chain_id"

    # …while the files themselves really do carry the rationale.
    for app, name, needle in [
        ("cow", "_anchor_trades", "base"),
        ("cow", "activity", "11 GiB"),
        ("governance", "treasury_token_history", "scan"),
    ]:
        raw = (sql_loader.QUERIES_DIR / app / f"{name}.sql").read_text()
        assert raw.lstrip().startswith("--"), f"{app}/{name} has no header"
        assert needle.lower() in raw.lower(), f"{app}/{name} lost its rationale"


def test_no_rendered_template_exceeds_the_query_length_cap():
    """The cap is enforced in clients/clickhouse.py via validate_query, and a
    template that renders over it fails at execution time on one code path only.
    Fragments render short by nature; this is really a guard on the assembled
    whole-query files and on anything that grows with the chain list."""
    from cerebro_mcp.config import settings

    over = []
    for app in sql_loader.apps():
        for name in sql_loader.available(app):
            tokens = sorted(sql_loader._tokens(app, name))
            n = len(sql_loader.load_sql(app, name, **{t: "" for t in tokens}))
            if n > settings.MAX_QUERY_LENGTH:
                over.append(f"{app}/{name}: {n} > {settings.MAX_QUERY_LENGTH}")
    assert over == [], "\n".join(over)


def test_is_fragment_matches_the_naming_convention():
    assert sql_loader.is_fragment("_cte_checkpoints")
    assert sql_loader.is_fragment("_anchor_trades")
    assert not sql_loader.is_fragment("activity")
    assert not sql_loader.is_fragment("trades")


def test_stripping_only_removes_comment_ONLY_lines():
    """A trailing comment after code is left alone — removing it would require
    knowing whether the `--` sits inside a string literal."""
    assert sql_loader._strip_comment_lines("-- gone\nkept\n  -- gone too\n") == (
        "kept\n"
    )
    assert sql_loader._strip_comment_lines("x = 1 -- kept\n") == "x = 1 -- kept\n"
    # Blank lines are significant in several fragments (a leading newline before a
    # CTE name) and must survive.
    assert sql_loader._strip_comment_lines("\ncp AS (\n") == "\ncp AS (\n"


def test_every_fragment_that_renders_inline_carries_a_rationale():
    """Rule 0's actual purpose. A fragment whose file is pure SQL with no
    explanation is the state this rule exists to move away from, so new ones must
    document themselves. Pre-existing fragments are listed rather than rewritten;
    shrink-only, so the list can only get shorter."""
    UNDOCUMENTED = {
        "cow/_cte_checkpoints", "cow/_cte_owner_months", "cow/_cte_token_metadata",
        "cow/_cte_token_metadata_multichain", "cow/_pred_native_price_window",
    }
    missing, stale = [], []
    for app in sql_loader.apps():
        for name in sql_loader.available(app):
            if not sql_loader.is_fragment(name):
                continue
            key = f"{app}/{name}"
            raw = (sql_loader.QUERIES_DIR / app / f"{name}.sql").read_text()
            documented = any(
                l.lstrip().startswith("--") for l in raw.split("\n")
            )
            if documented and key in UNDOCUMENTED:
                stale.append(key)
            elif not documented and key not in UNDOCUMENTED:
                missing.append(key)
    assert missing == [], f"fragments with no rationale header: {missing}"
    assert stale == [], (
        f"{stale} now carry a header — delete them from UNDOCUMENTED so the "
        f"backlog can only shrink"
    )


def _loaded_template_names() -> set[str]:
    """``app/name`` pairs that actually appear in a ``load_sql(app, name, ...)`` call.

    Parsed from the AST, NOT grepped. The first version of this test matched any
    quoted occurrence of the stem and had a FALSE NEGATIVE that hid two real
    orphans: ``trade_activity`` and ``trader_activity`` are also QuerySpec keys and
    SECTION_GROUPS entries, so the name was "found" in the source while no
    ``load_sql`` call ever named it. A guard that reports success for the wrong
    reason is the failure mode this repo keeps paying for.
    """
    import ast
    import pathlib

    src = pathlib.Path(sql_loader.__file__).resolve().parents[3]
    found: set[str] = set()
    for path in src.rglob("*.py"):
        if "queries" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if fname != "load_sql" or len(node.args) < 2:
                continue
            app, name = node.args[0], node.args[1]
            if isinstance(app, ast.Constant) and isinstance(name, ast.Constant):
                found.add(f"{app.value}/{name.value}")
    return found


#: Templates shipped but loaded by nothing. DELIBERATELY EMPTY — every ``.sql`` in
#: the tree is loaded by name.
#:
#: It briefly held ``cow/trade_activity`` and ``cow/trader_activity``, two of the
#: THREE envelopes commit 77927cf ("sql isolated + miniapps update") wrote for the
#: same per-chain UNION shape while wiring NONE of them. ``activity.sql`` was
#: adopted instead and proven to render byte-identically to both, with the live
#: ``shared_ctes`` / ``firsts_cte`` / arm values — the traders case by passing
#: ``shared_ctes=f"{shared_ctes},{firsts_cte}"`` rather than taking the comma in the
#: template. The two were then deleted: git preserves them if a per-section split is
#: ever wanted, and a dead template in the tree is worse than one in history because
#: it invites edits that change nothing. That is not hypothetical here — editing
#: ``trade_activity.sql`` to fix the trades envelope would have done nothing at all.
#:
#: Adding an entry means shipping a file nothing reads. Justify it here, or wire it.
KNOWN_ORPHANS: set[str] = set()


def test_no_shipped_template_is_orphaned():
    """Every ``.sql`` file is actually loaded by name somewhere.

    ``sql_loader.available()``'s docstring claimed since it was written that it is
    "used by the registry test that asserts no .sql file is orphaned". That test did
    not exist. Meanwhile ``queries/cow/activity.sql`` sat unreferenced for three
    commits while the Python it was meant to replace kept running.

    The complementary direction is already covered — ``load_sql`` raises for a
    missing file — so the two together stop the file set and the call sites drifting
    apart in either direction. See ``orphaned-sql-template-never-wired``.
    """
    loaded = _loaded_template_names()
    orphans = [
        f"{app}/{name}"
        for app in sql_loader.apps()
        for name in sql_loader.available(app)
        if f"{app}/{name}" not in loaded and f"{app}/{name}" not in KNOWN_ORPHANS
    ]
    assert orphans == [], (
        f"unreferenced .sql templates: {orphans}. Delete them, or wire them up — "
        f"a template nothing loads is a trap for the next reader."
    )


def test_the_known_orphan_list_is_shrink_only():
    loaded = _loaded_template_names()
    stale = sorted(k for k in KNOWN_ORPHANS if k in loaded)
    assert stale == [], (
        f"{stale} are now loaded — delete them from KNOWN_ORPHANS so the rule "
        f"covers them"
    )


def test_the_orphan_detector_reads_call_sites_not_bare_strings():
    """Negative test for the false negative that hid two real orphans: a name
    appearing only as a QuerySpec key must NOT count as loaded.

    Still meaningful after those two files were deleted, because the MECHANISM is
    unchanged — ``trade_activity`` and ``trader_activity`` remain live dataset keys
    (QuerySpec keys, SECTION_GROUPS entries, and UI dataset ids), so a bare-string
    detector would still report them as "found". That is what made the grep version
    of this guard pass while both templates were dead.
    """
    loaded = _loaded_template_names()
    assert "cow/activity" in loaded
    assert "cow/_anchor_trades" in loaded
    # Live dataset keys, never load_sql names.
    assert "cow/trade_activity" not in loaded
    assert "cow/trader_activity" not in loaded
    # And the mechanism is still present, or this test proves nothing.
    import pathlib

    cow = (
        pathlib.Path(sql_loader.__file__).resolve().parent / "cow_explorer.py"
    ).read_text(encoding="utf-8")
    assert '"trade_activity"' in cow and '"trader_activity"' in cow
