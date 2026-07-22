"""Focused concurrency tests for deterministic Graph Explorer traversal."""

from __future__ import annotations

from threading import Barrier, Lock
from time import sleep

from cerebro_mcp.semantic.graph_profiles import GraphProfile
from cerebro_mcp.tools.semantic.graph_explorer.traverse import bfs_expand


def _profile(
    profile_id: str,
    *,
    source_kind: str = "address",
    target_kind: str = "address",
) -> GraphProfile:
    return GraphProfile(
        profile=profile_id,
        model_name=f"model_{profile_id}",
        relation_name=f"relation_{profile_id}",
        source_column="source",
        target_column="target",
        source_kind=source_kind,
        target_kind=target_kind,
    )


def _node_and_edge(profile_id: str, seed_id: str):
    node_id = f"node-{profile_id}"
    return (
        [
            {
                "id": node_id,
                "kind": "address",
                "label": node_id,
                "profiles": [profile_id],
            }
        ],
        [
            {
                "id": f"{profile_id}:{seed_id}->{node_id}",
                "source": seed_id,
                "target": node_id,
                "profile": profile_id,
                "weight": 1.0,
                "edge_count": 1,
                "directed": True,
            }
        ],
    )


def test_profile_fetches_are_bounded_parallel_but_merge_in_profile_order():
    profiles = [_profile(f"p{index}") for index in range(6)]
    first_wave = Barrier(4)
    lock = Lock()
    starts = 0
    active = 0
    peak_active = 0

    def fetch(_ch, profile, *, seed_ids, direction, window_days, limit):
        nonlocal starts, active, peak_active
        del direction, window_days, limit
        with lock:
            starts += 1
            start_number = starts
            active += 1
            peak_active = max(peak_active, active)
        try:
            # This barrier can complete only if at least four independent
            # profile calls are in flight together.
            if start_number <= 4:
                first_wave.wait(timeout=2)
            # Deliberately vary completion order. Admission below must still
            # follow the caller's profile order.
            sleep((5 - int(profile.profile[1:])) * 0.002)
            nodes, edges = _node_and_edge(profile.profile, seed_ids[0])
            return nodes, edges, [f"warning-{profile.profile}"]
        finally:
            with lock:
                active -= 1

    result = bfs_expand(
        None,
        frontier=[("seed", "address")],
        chosen_profiles=profiles,
        kind_partition=True,
        hops=1,
        window_days=90,
        per_query_limit=25,
        node_cap=100,
        per_hop_budget=100,
        fetch=fetch,
    )

    assert peak_active == 4
    assert list(result.nodes) == [f"node-p{index}" for index in range(6)]
    assert list(result.edges) == [
        f"p{index}:seed->node-p{index}" for index in range(6)
    ]
    assert result.warnings == [f"warning-p{index}" for index in range(6)]


def test_parallel_profile_exception_is_isolated_and_ordered():
    profiles = [_profile(f"p{index}") for index in range(3)]
    all_started = Barrier(3)

    def fetch(_ch, profile, *, seed_ids, direction, window_days, limit):
        del direction, window_days, limit
        all_started.wait(timeout=2)
        if profile.profile == "p1":
            raise RuntimeError("profile unavailable")
        nodes, edges = _node_and_edge(profile.profile, seed_ids[0])
        return nodes, edges, [f"warning-{profile.profile}"]

    result = bfs_expand(
        None,
        frontier=[("seed", "address")],
        chosen_profiles=profiles,
        kind_partition=True,
        hops=1,
        window_days=90,
        per_query_limit=25,
        node_cap=100,
        per_hop_budget=100,
        fetch=fetch,
    )

    assert list(result.nodes) == ["node-p0", "node-p2"]
    assert list(result.edges) == ["p0:seed->node-p0", "p2:seed->node-p2"]
    assert result.profiles_used == {"p0", "p2"}
    assert result.warnings == [
        "warning-p0",
        "p1: profile unavailable",
        "warning-p2",
    ]


def test_budget_admission_skips_next_kind_group_after_parallel_fetch():
    profiles = [
        _profile("a0", source_kind="kind-a", target_kind="kind-a"),
        _profile("a1", source_kind="kind-a", target_kind="kind-a"),
        _profile("b0", source_kind="kind-b", target_kind="kind-b"),
    ]
    kind_a_started = Barrier(2)
    calls: list[str] = []
    calls_lock = Lock()

    def fetch(_ch, profile, *, seed_ids, direction, window_days, limit):
        del direction, window_days, limit
        with calls_lock:
            calls.append(profile.profile)
        if profile.profile.startswith("a"):
            kind_a_started.wait(timeout=2)
        nodes, edges = _node_and_edge(profile.profile, seed_ids[0])
        return nodes, edges, []

    result = bfs_expand(
        None,
        frontier=[("seed-a", "kind-a"), ("seed-b", "kind-b")],
        chosen_profiles=profiles,
        kind_partition=True,
        hops=1,
        window_days=90,
        per_query_limit=25,
        node_cap=100,
        per_hop_budget=1,
        fetch=fetch,
    )

    # Both independent queries in the admitted group ran, but serial budget
    # admission retained only the first profile's result. The next kind group
    # never ran and therefore remains expandable.
    assert set(calls) == {"a0", "a1"}
    assert list(result.nodes) == ["node-a0"]
    assert list(result.edges) == ["a0:seed-a->node-a0"]
    assert result.profiles_used == {"a0"}
    assert result.expanded_frontier == {"seed-a"}
    assert result.truncated is True
    assert result.truncated_at_hop == 1
