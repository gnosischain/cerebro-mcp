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
