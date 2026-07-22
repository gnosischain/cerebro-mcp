"""Focused tests for Graph Explorer address-role cache semantics."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from cerebro_mcp.tools.semantic.graph_explorer import fetch


def _role_result(*, is_safe: int = 1):
    values = [0] * len(fetch.ADDRESS_ROLE_COLUMNS)
    values[0] = is_safe
    return SimpleNamespace(
        columns=list(fetch.ADDRESS_ROLE_COLUMNS),
        rows=[values],
    )


@pytest.fixture(autouse=True)
def _clear_role_cache():
    fetch.reset_address_role_cache_for_tests()
    yield
    fetch.reset_address_role_cache_for_tests()


def test_verified_roles_are_cached_and_returned_as_defensive_copies(monkeypatch):
    calls = 0

    def query(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _role_result()

    monkeypatch.setattr(fetch.mini_apps, "run_structured_query", query)
    source = object()

    first, first_status = fetch.resolve_address_roles_with_status(source, "0xAbC")
    first["is_safe"] = 0
    second, second_status = fetch.resolve_address_roles_with_status(source, "0xabc")

    assert calls == 1
    assert second["is_safe"] == 1
    assert first_status.succeeded is True
    assert second_status == fetch.EvidenceQueryStatus(
        succeeded=True,
        source_rows_returned=1,
        complete=True,
    )


def test_verified_absence_is_cached_separately_and_scoped_to_source(monkeypatch):
    calls: dict[object, int] = {}
    source_without_role = object()
    source_with_role = object()

    def query(ch, *_args, **_kwargs):
        calls[ch] = calls.get(ch, 0) + 1
        if ch is source_without_role:
            return SimpleNamespace(columns=list(fetch.ADDRESS_ROLE_COLUMNS), rows=[])
        return _role_result()

    monkeypatch.setattr(fetch.mini_apps, "run_structured_query", query)

    missing, missing_status = fetch.resolve_address_roles_with_status(
        source_without_role, "0xabc"
    )
    missing_again, _ = fetch.resolve_address_roles_with_status(
        source_without_role, "0xABC"
    )
    present, present_status = fetch.resolve_address_roles_with_status(
        source_with_role, "0xabc"
    )

    assert missing == missing_again == {}
    assert missing_status == fetch.EvidenceQueryStatus(
        succeeded=True,
        source_rows_returned=0,
        complete=True,
    )
    assert present["is_safe"] == 1
    assert present_status.source_rows_returned == 1
    assert calls == {source_without_role: 1, source_with_role: 1}


def test_failed_role_lookup_is_never_cached_as_verified_absence(monkeypatch):
    calls = 0

    def query(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("role source unavailable")
        return _role_result()

    monkeypatch.setattr(fetch.mini_apps, "run_structured_query", query)
    source = object()

    failed_roles, failed_status = fetch.resolve_address_roles_with_status(
        source, "0xabc"
    )
    recovered_roles, recovered_status = fetch.resolve_address_roles_with_status(
        source, "0xabc"
    )
    cached_roles, _ = fetch.resolve_address_roles_with_status(source, "0xabc")

    assert failed_roles == {}
    assert failed_status.succeeded is False
    assert "role source unavailable" in str(failed_status.error)
    assert recovered_status.succeeded is True
    assert recovered_roles["is_safe"] == cached_roles["is_safe"] == 1
    assert calls == 2


def test_role_and_absence_entries_use_independent_ttls(monkeypatch):
    now = [100.0]
    calls: dict[str, int] = {}

    monkeypatch.setattr(fetch.time, "monotonic", lambda: now[0])

    def query(_ch, *_args, **kwargs):
        address = kwargs["parameters"]["addr"]
        calls[address] = calls.get(address, 0) + 1
        if address == "0xmissing":
            return SimpleNamespace(columns=list(fetch.ADDRESS_ROLE_COLUMNS), rows=[])
        return _role_result()

    monkeypatch.setattr(fetch.mini_apps, "run_structured_query", query)
    source = object()

    fetch.resolve_address_roles_with_status(source, "0xrole")
    fetch.resolve_address_roles_with_status(source, "0xmissing")
    now[0] += fetch._ADDRESS_ROLE_ABSENCE_CACHE_TTL_SECONDS + 1
    fetch.resolve_address_roles_with_status(source, "0xrole")
    fetch.resolve_address_roles_with_status(source, "0xmissing")

    assert calls == {"0xrole": 1, "0xmissing": 2}

    now[0] += fetch._ADDRESS_ROLE_CACHE_TTL_SECONDS
    fetch.resolve_address_roles_with_status(source, "0xrole")
    assert calls["0xrole"] == 2


def test_role_cache_is_bounded_and_safe_for_concurrent_hits(monkeypatch):
    calls = 0

    monkeypatch.setattr(fetch, "_ADDRESS_ROLE_CACHE_MAX_ENTRIES", 2)

    def query(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _role_result()

    monkeypatch.setattr(fetch.mini_apps, "run_structured_query", query)
    source = object()

    for address in ("0xa", "0xb", "0xc"):
        fetch.resolve_address_roles_with_status(source, address)
    assert len(fetch._address_role_cache) == 2

    # 0xa was the least-recently used entry and must be fetched again.
    fetch.resolve_address_roles_with_status(source, "0xa")
    assert calls == 4

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _index: fetch.resolve_address_roles_with_status(
                    source, "0xa"
                )[0]["is_safe"],
                range(64),
            )
        )

    assert results == [1] * 64
    assert calls == 4

