#!/usr/bin/env python3
"""Repeatable HTTP timing probe for a user-managed Graph Explorer server.

The probe never starts or stops the server. It records the health/bundle
identity beside every result so timings from different builds cannot be mixed.
Use ``--suite full`` only when the server has real ClickHouse/RPC credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ADDRESS = "0x3d2cf5293907667c77a3f2f4cea8f05c9a56ee03"


def _request_json(
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> tuple[dict[str, Any], int, float, bytes]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"malformed_json": raw[:500].decode("utf-8", "replace")}
    return payload, status, elapsed_ms, raw


def _tool(
    base: str,
    token: str,
    name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, status, elapsed_ms, raw = _request_json(
        f"{base}/app/graph_explorer/api/tool/{urllib.parse.quote(name)}",
        token=token,
        body={"arguments": arguments},
    )
    measurement = {
        "tool": name,
        "status": status,
        "elapsed_ms": round(elapsed_ms, 3),
        "response_bytes": len(raw),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "is_error": bool(payload.get("isError")),
    }
    structured = payload.get("structuredContent")
    return (structured if isinstance(structured, dict) else {}), measurement


def _new_view(base: str, token: str, mode: str = "atlas") -> str:
    structured, measurement = _tool(
        base, token, "open_graph_explorer", {"mode": mode}
    )
    view_id = str(structured.get("view_id") or "")
    if not view_id:
        raise RuntimeError(f"open_graph_explorer failed: {measurement}")
    return view_id


def _case_arguments(
    name: str, base: str, token: str, address: str, request_id: int
) -> dict[str, Any]:
    mode = {
        "seed": "investigate",
        "expand": "investigate",
        "flows": "flows",
        "timeline": "timeline",
        "transactions": "transactions",
    }[name]
    view_id = _new_view(base, token, mode)
    if name == "seed":
        return {"view_id": view_id, "seed_node_id": address, "request_id": request_id}
    if name == "expand":
        # Prime the deterministic graph before measuring expansion.
        _tool(
            base,
            token,
            "load_graph_explorer_seed",
            {"view_id": view_id, "seed_node_id": address, "request_id": 1},
        )
        return {"view_id": view_id, "node_id": address, "request_id": request_id}
    if name == "flows":
        return {"view_id": view_id, "seed_node_ids": [address], "request_id": request_id}
    if name == "timeline":
        return {"view_id": view_id, "seed_node_ids": [address], "request_id": request_id}
    return {
        "view_id": view_id,
        "seed_node_id": address,
        "operation": "discover",
        "page_size": 25,
        "request_id": request_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--token", default=os.environ.get("MCP_AUTH_TOKEN", "dev"))
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--suite", choices=("smoke", "full"), default="smoke")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".artifacts/graph-explorer-perf.json"),
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    health, health_status, health_ms, health_raw = _request_json(
        f"{base}/app/graph_explorer/health", token=args.token, timeout=15
    )
    if health_status != 200:
        raise SystemExit(f"Graph Explorer health failed ({health_status}): {health}")

    cases = ["open"] if args.suite == "smoke" else [
        "open", "seed", "expand", "flows", "timeline", "transactions"
    ]
    records: list[dict[str, Any]] = []
    for case in cases:
        samples: list[dict[str, Any]] = []
        for index in range(max(1, args.repeats + 1)):
            if case == "open":
                _, measurement = _tool(base, args.token, "open_graph_explorer", {})
            else:
                arguments = _case_arguments(
                    case, base, args.token, args.address.lower(), index + 1
                )
                tool_name = {
                    "seed": "load_graph_explorer_seed",
                    "expand": "expand_graph_explorer_node",
                    "flows": "load_graph_flows",
                    "timeline": "load_graph_timeline",
                    "transactions": "load_graph_transactions",
                }[case]
                _, measurement = _tool(base, args.token, tool_name, arguments)
            measurement["sample"] = index
            measurement["cache_state"] = "cold" if index == 0 else "warm"
            samples.append(measurement)
        warm = [sample["elapsed_ms"] for sample in samples[1:]]
        records.append(
            {
                "case": case,
                "samples": samples,
                "cold_ms": samples[0]["elapsed_ms"],
                "warm_median_ms": round(statistics.median(warm), 3) if warm else None,
                "warm_p95_ms": (
                    round(sorted(warm)[max(0, int(len(warm) * 0.95) - 1)], 3)
                    if warm
                    else None
                ),
            }
        )

    output = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "subject": args.address.lower(),
        "health_status": health_status,
        "health_elapsed_ms": round(health_ms, 3),
        "health_response_sha256": hashlib.sha256(health_raw).hexdigest(),
        "identity": health,
        "results": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
