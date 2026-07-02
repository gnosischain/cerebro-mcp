"""Tests for enriched PlanningError messages in the semantic planner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cerebro_mcp.semantic.planner import (
    PlanningError,
    _branch_available_axes,
    _find_time_spine_grain,
    _resolve_dimension_binding,
    _try_time_spine_upcast,
    plan_metric_query,
)


def _make_snapshot(
    *,
    models: dict | None = None,
    metrics: dict | None = None,
    dimension_index: dict | None = None,
    graph=None,
    registry_hash: str = "test",
):
    """Build a minimal snapshot namespace for planner tests."""
    return SimpleNamespace(
        models=models or {},
        metrics=metrics or {},
        dimension_index=dimension_index or {},
        graph=graph,
        registry_hash=registry_hash,
    )


class TestDimensionErrorMessages:
    def test_unreachable_dimension_lists_available_local_dims(self):
        snapshot = _make_snapshot(
            models={
                "root_model": {
                    "dimensions": [
                        {"name": "day"},
                        {"name": "month"},
                        {"name": "token"},
                    ],
                }
            },
            dimension_index={},  # "sector" not in index at all
        )
        with pytest.raises(PlanningError, match="Available dimensions on 'root_model': day, month, token"):
            _resolve_dimension_binding(snapshot, "root_model", "sector")

    def test_unreachable_dimension_no_local_dims(self):
        snapshot = _make_snapshot(
            models={"root_model": {"dimensions": []}},
            dimension_index={},
        )
        with pytest.raises(PlanningError, match="'root_model' has no local dimensions"):
            _resolve_dimension_binding(snapshot, "root_model", "sector")

    def test_unapproved_providers_mentioned(self):
        snapshot = _make_snapshot(
            models={"root_model": {"dimensions": [{"name": "day"}]}},
            dimension_index={
                "sector": [
                    {
                        "provider_model": "model_b",
                        "semantic_status": "candidate",
                        "dimension": {"name": "sector"},
                    },
                ]
            },
        )
        with pytest.raises(PlanningError, match="Providers exist but are not approved: model_b"):
            _resolve_dimension_binding(snapshot, "root_model", "sector")

    def test_rejected_path_reasons_included(self):
        """When providers exist and are approved but the path is blocked,
        the rejection reason should appear in the error."""
        # We need to set up a scenario where find_safest_path raises PlanningError
        # Use a snapshot where the provider is approved but graph pathfinding fails
        snapshot = _make_snapshot(
            models={"root_model": {"dimensions": [{"name": "day"}]}},
            dimension_index={
                "sector": [
                    {
                        "provider_model": "model_c",
                        "semantic_status": "approved",
                        "dimension": {"name": "sector"},
                    },
                ]
            },
            graph={},  # Empty graph — pathfinding will fail
        )
        with pytest.raises(PlanningError, match="Rejected paths:"):
            _resolve_dimension_binding(snapshot, "root_model", "sector")

    def test_error_starts_with_dimension_name(self):
        snapshot = _make_snapshot(
            models={"root_model": {"dimensions": []}},
            dimension_index={},
        )
        with pytest.raises(PlanningError, match=r"^Dimension 'missing_dim' is not reachable"):
            _resolve_dimension_binding(snapshot, "root_model", "missing_dim")

    def test_local_dimension_resolves_without_error(self):
        snapshot = _make_snapshot(
            models={
                "root_model": {
                    "dimensions": [{"name": "day"}],
                }
            },
        )
        binding, rejected = _resolve_dimension_binding(snapshot, "root_model", "day")
        assert binding["name"] == "day"
        assert binding["local"] is True
        assert rejected is None


# ─── PR 5: time-spine upcast helpers ─────────────────────────────────


def _spine_snapshot():
    """Snapshot containing all three time spines + a daily-grain root
    model with a `date` column. Mirrors the production shape so the
    upcast helpers exercise the same lookup path they will in production.
    """
    return _make_snapshot(
        models={
            "dim_time_spine_daily": {
                "name": "dim_time_spine_daily",
                "dimensions": [
                    {"name": "day", "type": "time",
                     "type_params": {"time_granularity": "day"}}
                ],
            },
            "dim_time_spine_weekly": {
                "name": "dim_time_spine_weekly",
                "dimensions": [
                    {"name": "week", "type": "time",
                     "type_params": {"time_granularity": "week"}}
                ],
            },
            "dim_time_spine_monthly": {
                "name": "dim_time_spine_monthly",
                "dimensions": [
                    {"name": "month", "type": "time",
                     "type_params": {"time_granularity": "month"}}
                ],
            },
            "api_consensus_validators_active_daily": {
                "name": "api_consensus_validators_active_daily",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "date",
                     "type_params": {"time_granularity": "day"}}
                ],
            },
        },
        dimension_index={},
    )


class TestTimeSpineGrainLookup:
    def test_finds_weekly_grain_from_spine_model(self):
        snap = _spine_snapshot()
        assert _find_time_spine_grain(snap, "week") == "week"

    def test_finds_monthly_grain_from_spine_model(self):
        snap = _spine_snapshot()
        assert _find_time_spine_grain(snap, "month") == "month"

    def test_returns_none_for_non_spine_dimension(self):
        snap = _spine_snapshot()
        assert _find_time_spine_grain(snap, "sector") is None


class TestTimeSpineUpcast:
    def test_week_upcast_from_daily_source(self):
        snap = _spine_snapshot()
        binding = _try_time_spine_upcast(
            snap, "api_consensus_validators_active_daily", "week"
        )

        assert binding is not None
        assert binding["local"] is True
        assert binding["_synthesised"] == "time_spine_upcast"
        assert binding["dimension"]["_upcast_template"] == "toMonday({col})"
        # Source column is read from the root model's daily dimension's `expr`.
        assert binding["dimension"]["_upcast_from_col"] == "date"
        assert binding["dimension"]["_upcast_source_grain"] == "day"

    def test_month_upcast_from_daily_source(self):
        snap = _spine_snapshot()
        binding = _try_time_spine_upcast(
            snap, "api_consensus_validators_active_daily", "month"
        )

        assert binding is not None
        assert binding["dimension"]["_upcast_template"] == "toStartOfMonth({col})"
        assert binding["dimension"]["_upcast_from_col"] == "date"

    def test_no_upcast_when_target_not_a_spine_grain(self):
        snap = _spine_snapshot()
        # `sector` isn't a time-spine grain — upcast doesn't apply.
        assert _try_time_spine_upcast(
            snap, "api_consensus_validators_active_daily", "sector"
        ) is None

    def test_no_downcast(self):
        # Asking for `day` from a weekly-only source should NOT synthesise
        # — you can't recover daily granularity from weekly aggregates.
        snap = _make_snapshot(
            models={
                "dim_time_spine_daily": {
                    "dimensions": [{"name": "day", "type": "time",
                                    "type_params": {"time_granularity": "day"}}]
                },
                "weekly_only_root": {
                    "name": "weekly_only_root",
                    "dimensions": [
                        {"name": "week", "type": "time", "expr": "week",
                         "type_params": {"time_granularity": "week"}}
                    ],
                },
            },
        )
        assert _try_time_spine_upcast(snap, "weekly_only_root", "day") is None

    def test_no_upcast_when_root_has_no_time_column(self):
        snap = _make_snapshot(
            models={
                "dim_time_spine_weekly": {
                    "dimensions": [{"name": "week", "type": "time",
                                    "type_params": {"time_granularity": "week"}}]
                },
                "no_time_root": {
                    "name": "no_time_root",
                    "dimensions": [{"name": "sector", "type": "categorical"}],
                },
            },
        )
        assert _try_time_spine_upcast(snap, "no_time_root", "week") is None


class TestResolveDimensionBindingUpcastFallback:
    def test_resolve_falls_through_to_upcast_when_no_normal_path(self):
        # `week` is not on the root and not in dimension_index → without
        # upcast support this would raise PlanningError. With it, the
        # planner synthesises a derived binding.
        snap = _spine_snapshot()
        binding, _ = _resolve_dimension_binding(
            snap, "api_consensus_validators_active_daily", "week"
        )
        assert binding["local"] is True
        assert binding["_synthesised"] == "time_spine_upcast"
        assert binding["dimension"]["_upcast_template"] == "toMonday({col})"


# ─── Cross-root multi-metric: structured multi-root axis error ───────


def _multi_root_snapshot():
    """Two approved roots — transactions (day, sector) and validators
    (day only) — mirroring the fixture shape in test_semantic_tools.py.
    `sector` is only bindable on the transactions root, and the graph
    is empty so no join path can rescue the validators branch."""
    return SimpleNamespace(
        registry_hash="test",
        graph={},
        synonym_index={},
        models={
            "api_execution_transactions_by_sector_daily": {
                "name": "api_execution_transactions_by_sector_daily",
                "relation_name": "dbt.api_execution_transactions_by_sector_daily",
                "semantic_status": "approved",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "day"},
                    {"name": "sector", "type": "categorical", "expr": "sector"},
                ],
                "measures": [
                    {"name": "transaction_count_value", "agg": "sum", "expr": "txs"},
                ],
            },
            "api_consensus_validators_active_daily": {
                "name": "api_consensus_validators_active_daily",
                "relation_name": "dbt.api_consensus_validators_active_daily",
                "semantic_status": "approved",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "day"},
                ],
                "measures": [
                    {"name": "validators_active_value", "agg": "sum", "expr": "cnt"},
                ],
            },
        },
        metrics={
            "transaction_count": {
                "name": "transaction_count",
                "root_model": "api_execution_transactions_by_sector_daily",
                "measure": "transaction_count_value",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "default_filters": [],
            },
            "validators_active": {
                "name": "validators_active",
                "root_model": "api_consensus_validators_active_daily",
                "measure": "validators_active_value",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "default_filters": [],
            },
        },
        dimension_index={
            "sector": [
                {
                    "provider_model": "api_execution_transactions_by_sector_daily",
                    "dimension": {"name": "sector", "type": "categorical", "expr": "sector"},
                    "semantic_status": "approved",
                },
            ],
        },
    )


class TestMultiRootAxisError:
    def test_dimension_on_only_one_root_raises_structured_error(self):
        # `sector` binds locally on the transactions root but is
        # unreachable from the validators root — the planner must raise
        # ONE structured error listing EVERY root and its usable axes,
        # before any SQL executes.
        snap = _multi_root_snapshot()
        with pytest.raises(PlanningError) as exc:
            plan_metric_query(
                snap,
                requested_metrics=["transaction_count", "validators_active"],
                requested_dimensions=["sector"],
            )
        message = str(exc.value)
        assert "Metrics span multiple root models with no shared axis for 'sector'" in message
        # Both roots and their axes must be listed.
        assert (
            "- transaction_count (root: api_execution_transactions_by_sector_daily): "
            "available axes = day, sector"
        ) in message
        assert (
            "- validators_active (root: api_consensus_validators_active_daily): "
            "available axes = day"
        ) in message
        # Actionable options, including the computed shared axis set.
        assert "(1) query each metric separately" in message
        assert "(2) use a dimension available on every root (shared: day)" in message
        assert "(3) drop dimensions for a scalar comparison" in message

    def test_no_shared_axes_reports_none(self):
        snap = _multi_root_snapshot()
        # Remove `day` from the validators root — the two roots now
        # share nothing.
        snap.models["api_consensus_validators_active_daily"]["dimensions"] = []
        with pytest.raises(PlanningError, match=r"shared: none"):
            plan_metric_query(
                snap,
                requested_metrics=["transaction_count", "validators_active"],
                requested_dimensions=["sector"],
            )

    def test_shared_dimension_multi_root_still_plans(self):
        snap = _multi_root_snapshot()
        plan = plan_metric_query(
            snap,
            requested_metrics=["transaction_count", "validators_active"],
            requested_dimensions=["day"],
        )
        assert plan["planner_mode"] == "multi_branch_aggregate_join"
        assert len(plan["branches"]) == 2
        assert all(set(b["dimension_bindings"]) == {"day"} for b in plan["branches"])

    def test_zero_dimension_multi_root_plans_without_error(self):
        snap = _multi_root_snapshot()
        plan = plan_metric_query(
            snap,
            requested_metrics=["transaction_count", "validators_active"],
        )
        assert plan["planner_mode"] == "multi_branch_aggregate_join"
        assert all(b["dimension_bindings"] == {} for b in plan["branches"])

    def test_single_root_keeps_detailed_dimension_error(self):
        # Single-branch plans must keep the existing per-dimension error
        # (with reachability hints), not the multi-root shape.
        snap = _multi_root_snapshot()
        with pytest.raises(PlanningError, match=r"^Dimension 'sector' is not reachable"):
            plan_metric_query(
                snap,
                requested_metrics=["validators_active"],
                requested_dimensions=["sector"],
            )


class TestBranchAvailableAxes:
    def test_lists_local_dimensions(self):
        snap = _multi_root_snapshot()
        axes = _branch_available_axes(snap, "api_execution_transactions_by_sector_daily")
        assert axes == ["day", "sector"]

    def test_includes_time_spine_upcast_grains(self):
        # The root only carries a daily `date` column, but weekly and
        # monthly spines exist and are upcast-reachable — they count as
        # available axes in the error message.
        snap = _spine_snapshot()
        axes = _branch_available_axes(snap, "api_consensus_validators_active_daily")
        assert "day" in axes
        assert "week" in axes
        assert "month" in axes


# ─── allow_candidate: planner-side opt-in for candidate metrics ───────


def _candidate_metric_snapshot(root_status: str = "approved"):
    """One candidate-tier metric on a (by default approved) root. Mirrors
    the tool-layer `_metric_is_candidate` contract the planner must obey."""
    return SimpleNamespace(
        registry_hash="allow-candidate-test",
        graph={},
        synonym_index={"wallet candidate": "candidate_wallet_metric"},
        models={
            "approved_root": {
                "name": "approved_root",
                "relation_name": "dbt.approved_root",
                "semantic_status": root_status,
                "dimensions": [
                    {"name": "sector", "type": "categorical", "expr": "sector"},
                ],
                "measures": [
                    {"name": "wallet_value", "agg": "sum", "expr": "wallets"},
                ],
            },
        },
        metrics={
            "candidate_wallet_metric": {
                "name": "candidate_wallet_metric",
                "root_model": "approved_root",
                "measure": "wallet_value",
                "quality_tier": "candidate",
                "semantic_status": "candidate",
                "allowed_dimensions": ["sector"],
                "default_filters": [],
            },
        },
        dimension_index={},
    )


class TestAllowCandidatePlanning:
    def test_candidate_metric_rejected_by_default(self):
        snap = _candidate_metric_snapshot()
        with pytest.raises(PlanningError, match="not approved for semantic execution"):
            plan_metric_query(
                snap,
                requested_metrics=["candidate_wallet_metric"],
                requested_dimensions=["sector"],
            )

    def test_candidate_metric_plans_with_allow_candidate(self):
        # This was the end-to-end break: the tool gate accepted the opt-in
        # but the planner's _resolve_metric_name re-rejected the metric.
        snap = _candidate_metric_snapshot()
        plan = plan_metric_query(
            snap,
            requested_metrics=["candidate_wallet_metric"],
            requested_dimensions=["sector"],
            allow_candidate=True,
        )
        assert plan["resolved_metrics"] == ["candidate_wallet_metric"]
        assert plan["planner_mode"] == "single_model"
        assert plan["root_models"] == ["approved_root"]

    def test_allow_candidate_resolves_via_synonym(self):
        snap = _candidate_metric_snapshot()
        plan = plan_metric_query(
            snap,
            requested_metrics=["wallet candidate"],
            requested_dimensions=["sector"],
            allow_candidate=True,
        )
        assert plan["resolved_metrics"] == ["candidate_wallet_metric"]

    def test_allow_candidate_does_not_bypass_unapproved_root(self):
        # Quality escape hatch, not an authorization one: an unapproved
        # root still blocks planning even with the flag.
        snap = _candidate_metric_snapshot(root_status="candidate")
        with pytest.raises(PlanningError, match="not approved for semantic execution"):
            plan_metric_query(
                snap,
                requested_metrics=["candidate_wallet_metric"],
                requested_dimensions=["sector"],
                allow_candidate=True,
            )

    def test_allow_candidate_requires_declared_dimensions(self):
        # Scalar-KPI candidates (no allowed_dimensions) stay unplannable.
        snap = _candidate_metric_snapshot()
        snap.metrics["candidate_wallet_metric"]["allowed_dimensions"] = []
        with pytest.raises(PlanningError, match="not approved for semantic execution"):
            plan_metric_query(
                snap,
                requested_metrics=["candidate_wallet_metric"],
                allow_candidate=True,
            )


# ─── Binding cache: memoized _resolve_dimension_binding ──────────────


class TestBindingCache:
    """The dimension-binding cache memoizes successful resolutions per
    (registry_hash, root_model, dimension_name) and serves deep copies."""

    def _remote_snapshot(self, registry_hash: str):
        """Root without `sector`; a single approved provider reachable only
        via graph path search (which the tests stub out and count)."""
        return _make_snapshot(
            models={
                "root_model": {"dimensions": [{"name": "day"}]},
                "dim_provider": {"dimensions": [{"name": "sector"}]},
            },
            dimension_index={
                "sector": [
                    {
                        "provider_model": "dim_provider",
                        "semantic_status": "approved",
                        "dimension": {"name": "sector", "type": "categorical", "expr": "sector"},
                    },
                ]
            },
            graph={"adjacency": {}},
            registry_hash=registry_hash,
        )

    def _install_counting_path_search(self, monkeypatch):
        import cerebro_mcp.semantic.planner as planner_mod

        planner_mod._BINDING_CACHE.clear()
        calls = {"n": 0}

        def fake_find_safest_path(registry_hash, graph, source, target, *, max_hops):
            calls["n"] += 1
            return SimpleNamespace(
                models=("root_model", "dim_provider"), edges=(), cost=1.0
            )

        monkeypatch.setattr(planner_mod, "find_safest_path", fake_find_safest_path)
        return calls

    def test_second_identical_call_skips_path_search(self, monkeypatch):
        calls = self._install_counting_path_search(monkeypatch)
        snap = self._remote_snapshot("binding-cache-a")

        first, first_rejected = _resolve_dimension_binding(snap, "root_model", "sector")
        second, second_rejected = _resolve_dimension_binding(snap, "root_model", "sector")

        assert calls["n"] == 1  # path search ran once; second call was cached
        assert first == second
        assert first_rejected is None and second_rejected is None
        assert second["provider_model"] == "dim_provider"
        assert second["path"] == ["root_model", "dim_provider"]

    def test_cached_results_are_independent_copies(self, monkeypatch):
        calls = self._install_counting_path_search(monkeypatch)
        snap = self._remote_snapshot("binding-cache-b")

        first, _ = _resolve_dimension_binding(snap, "root_model", "sector")
        # Mutate the caller's copy — must NOT leak into the cache.
        first["dimension"]["expr"] = "mutated"
        first["path"].append("bogus_hop")

        second, _ = _resolve_dimension_binding(snap, "root_model", "sector")
        assert calls["n"] == 1
        assert second["dimension"]["expr"] == "sector"
        assert second["path"] == ["root_model", "dim_provider"]
        assert second is not first

    def test_cache_invalidates_on_registry_hash_change(self, monkeypatch):
        calls = self._install_counting_path_search(monkeypatch)

        snap_v1 = self._remote_snapshot("binding-cache-c1")
        _resolve_dimension_binding(snap_v1, "root_model", "sector")
        assert calls["n"] == 1

        # Same content under a NEW registry hash — must re-resolve.
        snap_v2 = self._remote_snapshot("binding-cache-c2")
        _resolve_dimension_binding(snap_v2, "root_model", "sector")
        assert calls["n"] == 2

    def test_empty_registry_hash_is_never_cached(self, monkeypatch):
        calls = self._install_counting_path_search(monkeypatch)
        snap = self._remote_snapshot("")

        _resolve_dimension_binding(snap, "root_model", "sector")
        _resolve_dimension_binding(snap, "root_model", "sector")

        import cerebro_mcp.semantic.planner as planner_mod

        assert calls["n"] == 2  # no snapshot identity -> no memoization
        assert planner_mod._BINDING_CACHE == {}

    def test_failed_resolutions_are_not_cached(self, monkeypatch):
        import cerebro_mcp.semantic.planner as planner_mod

        planner_mod._BINDING_CACHE.clear()
        snap = _make_snapshot(
            models={"root_model": {"dimensions": [{"name": "day"}]}},
            dimension_index={},
            registry_hash="binding-cache-fail",
        )
        with pytest.raises(PlanningError):
            _resolve_dimension_binding(snap, "root_model", "sector")
        assert planner_mod._BINDING_CACHE == {}

    def test_cache_cleared_after_too_many_registry_hashes(self):
        import cerebro_mcp.semantic.planner as planner_mod

        planner_mod._BINDING_CACHE.clear()
        for i in range(6):
            snap = _make_snapshot(
                models={"root_model": {"dimensions": [{"name": "day"}]}},
                registry_hash=f"binding-bound-{i}",
            )
            _resolve_dimension_binding(snap, "root_model", "day")
        # The 6th insert saw 5 (>4) distinct hashes and dropped the cache
        # wholesale before storing its own entry.
        assert {key[0] for key in planner_mod._BINDING_CACHE} == {"binding-bound-5"}


class TestPathCacheBounding:
    def test_path_cache_clears_after_too_many_registry_hashes(self):
        from cerebro_mcp.semantic import graph as graph_mod
        from cerebro_mcp.semantic.graph import build_semantic_graph, find_safest_path

        graph_mod._PATH_CACHE.clear()
        models = {
            "a": {"semantic_status": "approved", "module": "m"},
            "b": {"semantic_status": "approved", "module": "m"},
        }
        relationships = [
            {
                "name": "a_to_b",
                "left_model": "a",
                "right_model": "b",
                "left_keys": ["k"],
                "right_keys": ["k"],
                "cardinality": "many_to_one",
                "quality_tier": "approved",
            }
        ]
        graph, _ = build_semantic_graph(models, relationships)
        for i in range(6):
            find_safest_path(f"path-bound-{i}", graph, "a", "b")
        # Same bounding rule as the binding cache: >4 distinct registry
        # hashes present at insert time -> the cache is dropped wholesale.
        assert {key[0] for key in graph_mod._PATH_CACHE} == {"path-bound-5"}


class TestParallelEdgePathSearch:
    def test_parallel_edges_between_same_pair_do_not_crash_heap(self):
        # Two approved relationships between the SAME model pair (e.g. a
        # bridge's safe-side and owner-side edges) create heap entries with
        # identical (cost, node, models) prefixes; without a tiebreaker the
        # heap falls back to comparing edge dicts and raises TypeError.
        from cerebro_mcp.semantic.graph import build_semantic_graph, find_safest_path

        models = {
            "left_hub": {"semantic_status": "approved", "module": "a"},
            "pair_bridge": {"semantic_status": "approved", "module": "b"},
        }
        def rel(name, lk, rk):
            return {
                "name": name,
                "left_model": "left_hub",
                "right_model": "pair_bridge",
                "left_keys": [lk],
                "right_keys": [rk],
                "cardinality": "one_to_many",
                "quality_tier": "approved",
            }
        relationships = [
            rel("bridge_safe_side", "user_pseudonym", "safe_user_pseudonym"),
            rel("bridge_owner_side", "user_pseudonym", "owner_user_pseudonym"),
        ]
        graph, _ = build_semantic_graph(models, relationships)
        result = find_safest_path("parallel-edges-test", graph, "left_hub", "pair_bridge")
        # one_to_many base 5.0 + cross-module 0.5
        assert result.cost == 5.5
        assert len(result.edges) == 1


# ─── relationship metadata: row-level enrichment guards ──────────────


def _enrichment_snapshot(
    relationship_overrides: dict, registry_hash: str
):
    """Root model + one remote provider of `sector`, connected by a single
    approved relationship whose metadata the test varies."""
    from cerebro_mcp.semantic.graph import build_semantic_graph

    models = {
        "root_model": {
            "name": "root_model",
            "semantic_status": "approved",
            "module": "m",
            "dimensions": [{"name": "day"}],
        },
        "dim_provider": {
            "name": "dim_provider",
            "semantic_status": "approved",
            "module": "m",
            "dimensions": [{"name": "sector", "expr": "sector"}],
        },
    }
    relationship = {
        "name": "root_to_provider",
        "left_model": "root_model",
        "right_model": "dim_provider",
        "left_keys": ["k"],
        "right_keys": ["k"],
        "cardinality": "many_to_one",
        "quality_tier": "approved",
        **relationship_overrides,
    }
    graph, _ = build_semantic_graph(models, [relationship])
    return _make_snapshot(
        models=models,
        dimension_index={
            "sector": [
                {
                    "provider_model": "dim_provider",
                    "semantic_status": "approved",
                    "dimension": {"name": "sector", "expr": "sector"},
                }
            ]
        },
        graph=graph,
        registry_hash=registry_hash,
    )


class TestEnrichmentRelationshipMetadataGuards:
    """Dimension enrichment is a row-level join: paths crossing an
    aggregate_then_join_only edge, an edge explicitly marked unsafe for
    dimension enrichment, or ANY fan-out cardinality must be rejected —
    joining through them multiplies root rows and inflates measures."""

    def test_aggregate_then_join_only_edge_rejects_binding(self):
        snap = _enrichment_snapshot(
            {"aggregate_then_join_only": True}, "meta-guard-atj"
        )
        with pytest.raises(
            PlanningError,
            match=(
                r"Rejected paths: dim_provider: edge root_to_provider is "
                r"aggregate_then_join_only"
            ),
        ):
            _resolve_dimension_binding(snap, "root_model", "sector")

    def test_safe_for_dimension_enrichment_false_rejects_binding(self):
        snap = _enrichment_snapshot(
            {"safe_for_dimension_enrichment": False}, "meta-guard-sfde"
        )
        with pytest.raises(
            PlanningError, match=r"safe_for_dimension_enrichment"
        ):
            _resolve_dimension_binding(snap, "root_model", "sector")

    def test_one_to_many_path_rejects_binding_regardless_of_flags(self):
        # No explicit flags at all — cardinality alone must block it.
        snap = _enrichment_snapshot(
            {"cardinality": "one_to_many"}, "meta-guard-o2m"
        )
        with pytest.raises(
            PlanningError, match=r"one_to_many cardinality"
        ):
            _resolve_dimension_binding(snap, "root_model", "sector")

    def test_many_to_many_path_rejects_binding(self):
        snap = _enrichment_snapshot(
            {"cardinality": "many_to_many"}, "meta-guard-m2m"
        )
        with pytest.raises(
            PlanningError, match=r"many_to_many cardinality"
        ):
            _resolve_dimension_binding(snap, "root_model", "sector")

    def test_many_to_one_path_still_binds(self):
        snap = _enrichment_snapshot({}, "meta-guard-m2o")
        binding, rejected = _resolve_dimension_binding(snap, "root_model", "sector")
        assert binding["provider_model"] == "dim_provider"
        assert binding["local"] is False
        assert binding["path"] == ["root_model", "dim_provider"]
        assert rejected is None

    def test_blocked_provider_surfaces_as_rejection_next_to_good_binding(self):
        # Two providers of `sector`: one behind an aggregate_then_join_only
        # edge, one behind a plain many_to_one edge. The clean one binds;
        # the blocked one comes back as the rejection record that
        # plan_metric_query appends to plan['rejected_paths'].
        from cerebro_mcp.semantic.graph import build_semantic_graph

        models = {
            "root_model": {
                "name": "root_model",
                "semantic_status": "approved",
                "module": "m",
                "dimensions": [{"name": "day"}],
            },
            "blocked_provider": {
                "name": "blocked_provider",
                "semantic_status": "approved",
                "module": "m",
                "dimensions": [{"name": "sector", "expr": "sector"}],
            },
            "good_provider": {
                "name": "good_provider",
                "semantic_status": "approved",
                "module": "m",
                "dimensions": [{"name": "sector", "expr": "sector"}],
            },
        }
        relationships = [
            {
                "name": "root_to_blocked",
                "left_model": "root_model",
                "right_model": "blocked_provider",
                "left_keys": ["k"],
                "right_keys": ["k"],
                "cardinality": "many_to_one",
                "quality_tier": "approved",
                "aggregate_then_join_only": True,
            },
            {
                "name": "root_to_good",
                "left_model": "root_model",
                "right_model": "good_provider",
                "left_keys": ["k"],
                "right_keys": ["k"],
                "cardinality": "many_to_one",
                "quality_tier": "approved",
            },
        ]
        graph, _ = build_semantic_graph(models, relationships)
        snap = _make_snapshot(
            models=models,
            dimension_index={
                "sector": [
                    {
                        "provider_model": "blocked_provider",
                        "semantic_status": "approved",
                        "dimension": {"name": "sector", "expr": "sector"},
                    },
                    {
                        "provider_model": "good_provider",
                        "semantic_status": "approved",
                        "dimension": {"name": "sector", "expr": "sector"},
                    },
                ]
            },
            graph=graph,
            registry_hash="meta-guard-mixed",
        )
        binding, rejected = _resolve_dimension_binding(snap, "root_model", "sector")
        assert binding["provider_model"] == "good_provider"
        assert rejected is not None
        assert rejected["provider_model"] == "blocked_provider"
        assert "aggregate_then_join_only" in rejected["reason"]


# ─── relationship metadata: preferred_bridge cost cap ────────────────


class TestPreferredBridgeEdgeCost:
    """preferred_bridge may only discount non-fanning traversals. Before
    this guard, a preferred one_to_many bridge cost 0.5 and the path
    search happily routed row-level enrichment through fan-out edges."""

    def test_preferred_bridge_no_longer_discounts_one_to_many(self):
        from cerebro_mcp.semantic.graph import _edge_cost

        rel = {"cardinality": "one_to_many", "preferred_bridge": True}
        assert _edge_cost(rel, reverse=False) == 5.0

    def test_preferred_bridge_no_longer_discounts_many_to_many(self):
        from cerebro_mcp.semantic.graph import _edge_cost

        rel = {"cardinality": "many_to_many", "preferred_bridge": True}
        assert _edge_cost(rel, reverse=False) == 5.0
        assert _edge_cost(rel, reverse=True) == 5.0

    def test_preferred_bridge_still_discounts_safe_cardinalities(self):
        from cerebro_mcp.semantic.graph import _edge_cost

        assert _edge_cost(
            {"cardinality": "many_to_one", "preferred_bridge": True}, reverse=False
        ) == 0.5
        assert _edge_cost(
            {"cardinality": "one_to_one", "preferred_bridge": True}, reverse=False
        ) == 0.5

    def test_reverse_traversal_of_preferred_many_to_one_not_discounted(self):
        from cerebro_mcp.semantic.graph import _edge_cost

        # Crossing a many_to_one bridge backwards is one_to_many: fan-out.
        assert _edge_cost(
            {"cardinality": "many_to_one", "preferred_bridge": True}, reverse=True
        ) == 5.0

    def test_graph_path_cost_for_preferred_one_to_many_bridge(self):
        from cerebro_mcp.semantic.graph import build_semantic_graph, find_safest_path

        models = {
            "a": {"semantic_status": "approved", "module": "m"},
            "b": {"semantic_status": "approved", "module": "m"},
        }
        relationships = [
            {
                "name": "a_to_b",
                "left_model": "a",
                "right_model": "b",
                "left_keys": ["k"],
                "right_keys": ["k"],
                "cardinality": "one_to_many",
                "quality_tier": "approved",
                "preferred_bridge": True,
            }
        ]
        graph, _ = build_semantic_graph(models, relationships)
        result = find_safest_path("preferred-bridge-cost", graph, "a", "b")
        assert result.cost == 5.0  # base one_to_many, NOT the 0.5 bridge cap


# ─── Ratio / derived metrics (same-root post-aggregation MVP) ─────────


def _derived_snapshot(input_tier: str = "approved"):
    """Two same-root metrics + a ratio and a derived over them, plus a
    second-root metric for cross-root rejection tests. `input_tier`
    downgrades the denominator input to exercise executable gating."""
    return SimpleNamespace(
        registry_hash="derived-metric-test",
        graph={},
        synonym_index={},
        models={
            "api_gpay_tx_daily": {
                "name": "api_gpay_tx_daily",
                "relation_name": "dbt.api_gpay_tx_daily",
                "semantic_status": "approved",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "day",
                     "type_params": {"time_granularity": "day"}},
                ],
                "measures": [
                    {"name": "tx_success_value", "agg": "sum", "expr": "success_cnt"},
                    {"name": "tx_total_value", "agg": "sum", "expr": "total_cnt"},
                ],
            },
            "api_other_daily": {
                "name": "api_other_daily",
                "relation_name": "dbt.api_other_daily",
                "semantic_status": "approved",
                "dimensions": [
                    {"name": "day", "type": "time", "expr": "day",
                     "type_params": {"time_granularity": "day"}},
                ],
                "measures": [
                    {"name": "other_total_value", "agg": "sum", "expr": "cnt"},
                ],
            },
        },
        metrics={
            "tx_success": {
                "name": "tx_success",
                "type": "simple",
                "root_model": "api_gpay_tx_daily",
                "measure": "tx_success_value",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "tx_total": {
                "name": "tx_total",
                "type": "simple",
                "root_model": "api_gpay_tx_daily",
                "measure": "tx_total_value",
                "quality_tier": input_tier,
                "semantic_status": input_tier,
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "other_total": {
                "name": "other_total",
                "type": "simple",
                "root_model": "api_other_daily",
                "measure": "other_total_value",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "tx_success_rate": {
                "name": "tx_success_rate",
                "type": "ratio",
                "type_params": {"numerator": "tx_success", "denominator": "tx_total"},
                "root_model": "api_gpay_tx_daily",
                "measure": "",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "tx_failed": {
                "name": "tx_failed",
                "type": "derived",
                "type_params": {
                    "expr": "tx_total - tx_success",
                    "metrics": ["tx_total", "tx_success"],
                },
                "root_model": "api_gpay_tx_daily",
                "measure": "",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
            "cross_rate": {
                "name": "cross_rate",
                "type": "ratio",
                "type_params": {"numerator": "tx_success", "denominator": "other_total"},
                "root_model": "api_gpay_tx_daily",
                "measure": "",
                "quality_tier": "approved",
                "semantic_status": "approved",
                "allowed_dimensions": ["day"],
                "default_filters": [],
            },
        },
        dimension_index={},
    )


class TestDerivedMetricPlanning:
    def test_ratio_expands_inputs_onto_same_branch(self):
        snap = _derived_snapshot()
        plan = plan_metric_query(
            snap,
            requested_metrics=["tx_success_rate"],
            requested_dimensions=["day"],
        )
        assert plan["derived_metrics"] == [
            {
                "name": "tx_success_rate",
                "kind": "ratio",
                "inputs": ["tx_success", "tx_total"],
                "expr": "",
            }
        ]
        # Inputs aggregate on ONE branch; the derived metric itself is not
        # a branch metric (it has no measure).
        assert len(plan["branches"]) == 1
        assert plan["branches"][0]["metrics"] == ["tx_success", "tx_total"]
        assert plan["planner_mode"] == "single_model"
        assert plan["root_models"] == ["api_gpay_tx_daily"]

    def test_derived_records_expr(self):
        snap = _derived_snapshot()
        plan = plan_metric_query(
            snap,
            requested_metrics=["tx_failed"],
            requested_dimensions=["day"],
        )
        spec = plan["derived_metrics"][0]
        assert spec["kind"] == "derived"
        assert spec["expr"] == "tx_total - tx_success"
        assert spec["inputs"] == ["tx_total", "tx_success"]

    def test_cross_root_inputs_rejected_with_guidance(self):
        snap = _derived_snapshot()
        with pytest.raises(
            PlanningError,
            match=r"Cross-root derived metrics are not supported.*separately",
        ):
            plan_metric_query(
                snap,
                requested_metrics=["cross_rate"],
                requested_dimensions=["day"],
            )

    def test_unknown_input_rejected(self):
        snap = _derived_snapshot()
        snap.metrics["tx_success_rate"]["type_params"]["denominator"] = "nope"
        with pytest.raises(PlanningError, match="Unknown metric: nope"):
            plan_metric_query(
                snap,
                requested_metrics=["tx_success_rate"],
                requested_dimensions=["day"],
            )

    def test_missing_denominator_rejected(self):
        snap = _derived_snapshot()
        del snap.metrics["tx_success_rate"]["type_params"]["denominator"]
        with pytest.raises(PlanningError, match="numerator.*denominator"):
            plan_metric_query(
                snap,
                requested_metrics=["tx_success_rate"],
                requested_dimensions=["day"],
            )

    def test_candidate_input_gates_ratio_by_default(self):
        # Executable iff ALL inputs are executable.
        snap = _derived_snapshot(input_tier="candidate")
        with pytest.raises(PlanningError, match="not approved for semantic execution"):
            plan_metric_query(
                snap,
                requested_metrics=["tx_success_rate"],
                requested_dimensions=["day"],
            )

    def test_candidate_input_allowed_with_opt_in(self):
        snap = _derived_snapshot(input_tier="candidate")
        plan = plan_metric_query(
            snap,
            requested_metrics=["tx_success_rate"],
            requested_dimensions=["day"],
            allow_candidate=True,
        )
        assert plan["branches"][0]["metrics"] == ["tx_success", "tx_total"]

    def test_nested_derived_input_rejected(self):
        snap = _derived_snapshot()
        snap.metrics["tx_failed"]["type_params"]["metrics"] = [
            "tx_total", "tx_success_rate",
        ]
        with pytest.raises(PlanningError, match="nested ratio/derived"):
            plan_metric_query(
                snap,
                requested_metrics=["tx_failed"],
                requested_dimensions=["day"],
            )

    def test_ratio_plus_same_root_input_dedupes_branch_metrics(self):
        # Requesting an input alongside the ratio must not aggregate the
        # input twice (duplicate output alias).
        snap = _derived_snapshot()
        plan = plan_metric_query(
            snap,
            requested_metrics=["tx_success", "tx_success_rate"],
            requested_dimensions=["day"],
        )
        assert plan["branches"][0]["metrics"] == ["tx_success", "tx_total"]

    def test_simple_plan_has_no_derived_metrics_key(self):
        snap = _derived_snapshot()
        plan = plan_metric_query(
            snap,
            requested_metrics=["tx_success"],
            requested_dimensions=["day"],
        )
        assert "derived_metrics" not in plan
