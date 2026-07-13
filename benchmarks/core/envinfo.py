"""Environment/provenance block for benchmark result files.

The config fingerprint captures the settings that move benchmark numbers, so
``compare`` can warn when two runs are apples-to-oranges. ``cerebro_mcp`` is
imported lazily (env redirection must happen before any ``cerebro_mcp``
import — see ``benchmarks/__init__``).
"""

from __future__ import annotations

import hashlib
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

# Settings that materially affect benchmark numbers.
_FINGERPRINT_FIELDS = (
    "SEMANTIC_ENABLED",
    "ENFORCE_CHART_PRECONDITIONS",
    "REPORT_REQUIRES_EXPLICIT_MODE",
    "LEAN_CORE_ENABLED",
    "RPC_SCAN_ENABLED",
    "TOOL_RESPONSE_MAX_CHARS",
    "TOOL_RESULT_MAX_ROWS",
    "MAX_ROWS",
    "QUERY_TIMEOUT_SECONDS",
    "CLICKHOUSE_MAX_QUERY_MEMORY_GB",
    "MIN_MODELS_DETAILED",
    "MIN_MODELS_DETAILED_LITE",
    "MIN_TABLES_VERIFIED",
    "MIN_CHARTS_FOR_REPORT",
    "MIN_STATISTICAL_QUERIES",
    "MIN_CORRELATION_QUERIES",
    "MIN_EXPLORATORY_QUERIES",
    "REQUIRE_CHART_DIVERSITY",
    "REQUIRE_DIMENSIONAL_BREAKDOWN",
    "REQUIRE_RELATIONAL_CHART",
    "ENFORCE_DISCOVERED_MODEL_COVERAGE",
    "SEMANTIC_AUTOLOAD_ON_LOCAL_MTIME",
)


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=10,
            cwd=Path(__file__).resolve().parent,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def config_fingerprint() -> dict[str, Any]:
    from cerebro_mcp.config import settings  # lazy: env must be set first

    fp: dict[str, Any] = {}
    for name in _FINGERPRINT_FIELDS:
        fp[name] = getattr(settings, name, None)
    return fp


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except Exception:
        return None


def collect_environment(**extra: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
        "config_fingerprint": config_fingerprint(),
    }
    env.update(extra)
    return env
