from pathlib import Path

from cerebro_mcp.config import settings


def init_ssl_trust() -> bool:
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except Exception:
        return False


def validate_remote_transport_auth(auth_token: str | None) -> None:
    if auth_token or settings.ALLOW_INSECURE_REMOTE_TRANSPORT:
        return
    raise RuntimeError(
        "MCP_AUTH_TOKEN is required for SSE unless "
        "ALLOW_INSECURE_REMOTE_TRANSPORT=true"
    )


def ensure_writable_dir(path: Path) -> None:
    normalized = path.expanduser()
    try:
        normalized.mkdir(parents=True, exist_ok=True)
        probe = normalized / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"Directory '{normalized}' is not writable. "
            "Set CEREBRO_RESEARCH_DIR to a writable local path before starting "
            "the server."
        ) from exc
