#!/usr/bin/env python
"""Emit the staged-identity grant script for the connector ClickHouse user.

Usage:
    .venv/bin/python scripts/generate_connector_grants.py \
        --manifest target/manifest.json \
        --review scripts/connector_grant_review.yaml \
        --user cerebro_connector_v1 > grants_v1.sql

Reads the RAW manifest (path or URL is the operator's choice — pass a local
file), applies the reviewed approvals, and prints the SQL script plus the
manifest SHA to pin as MCP_EXPECTED_MANIFEST_SHA256. Approvals are the
review artifact: sources and passthrough views become grantable ONLY by
being listed there (fail closed — see src/cerebro_mcp/connector_grants.py).

The output creates a FRESH versioned identity (R10 C5): apply it, verify as
that user (every granted relation selectable; identity bridges and table
functions rejected), switch the deployment credential, then disable the
previous identity. Never mutate a live identity in place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cerebro_mcp.connector_grants import (  # noqa: E402
    compute_grant_closure,
    render_grant_script,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="raw manifest.json path")
    parser.add_argument(
        "--review",
        required=True,
        help="reviewed approvals YAML (approved_sources, approved_passthrough_views)",
    )
    parser.add_argument("--user", required=True, help="staged identity, e.g. cerebro_connector_v1")
    args = parser.parse_args()

    raw = Path(args.manifest).read_bytes()
    manifest = json.loads(raw)
    manifest_sha = hashlib.sha256(raw).hexdigest()

    review = yaml.safe_load(Path(args.review).read_text()) or {}
    result = compute_grant_closure(
        manifest,
        approved_sources=set(review.get("approved_sources") or []),
        approved_passthrough_views=set(
            review.get("approved_passthrough_views") or []
        ),
    )
    sys.stdout.write(
        render_grant_script(result, user=args.user, manifest_sha=manifest_sha)
    )
    if result.review_required:
        print(
            f"\n{len(result.review_required)} model(s) need review before "
            "they are reachable — see the worklist above.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
