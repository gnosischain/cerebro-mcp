ssl_trust_injected: bool = False
current_agent_role: str = "unknown"
# Short-TTL cache for the readiness probe's ClickHouse check, as
# ``(monotonic_ts, connected, detail)``. Readiness runs every few seconds for
# the life of the pod; without this each probe issues its own query.
clickhouse_health: tuple[float, bool, str] | None = None
