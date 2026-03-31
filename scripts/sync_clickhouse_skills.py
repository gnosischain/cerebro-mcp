#!/usr/bin/env python3
"""Vendor a pinned subset of ClickHouse agent skills into package static assets."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_REPO_URL = "https://github.com/ClickHouse/agent-skills"
DEFAULT_DEST = "src/cerebro_mcp/static/clickhouse_agent_skills"
SKILL_SUBPATH = Path("skills") / "clickhouse-best-practices"
COMPILED_RULES_PATH = SKILL_SUBPATH / "AGENTS.md"
UPSTREAM_TOP_LEVEL_FILES = ("LICENSE", "NOTICE")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        help="Path to a checked-out ClickHouse agent-skills repository",
    )
    parser.add_argument(
        "--dest",
        default=DEFAULT_DEST,
        help="Destination for the vendored skill bundle",
    )
    parser.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help="Source repository URL recorded in the vendored bundle manifest",
    )
    parser.add_argument(
        "--ref",
        default="",
        help="Pinned source commit or tag. If omitted, detect HEAD from the local checkout.",
    )
    return parser.parse_args()


def _detect_ref(source: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return completed.stdout.strip()


def _copy_required_files(source: Path, dest: Path) -> None:
    skill_source = source / SKILL_SUBPATH
    skill_dest = dest / SKILL_SUBPATH
    if not skill_source.exists():
        raise SystemExit(f"Missing skill directory in source repo: {skill_source}")

    if dest.exists():
        shutil.rmtree(dest)
    skill_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_source, skill_dest)

    for filename in UPSTREAM_TOP_LEVEL_FILES:
        src = source / filename
        if not src.exists():
            raise SystemExit(f"Missing required upstream file: {src}")
        shutil.copy2(src, dest / filename)


def _write_bundle_manifest(dest: Path, *, repo_url: str, ref: str) -> None:
    manifest = {
        "source_repo_url": repo_url,
        "source_ref": ref,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skill_directory": str(SKILL_SUBPATH).replace("\\", "/"),
        "compiled_rules_path": str(COMPILED_RULES_PATH).replace("\\", "/"),
        "required_files": [
            str(COMPILED_RULES_PATH).replace("\\", "/"),
            str((SKILL_SUBPATH / "SKILL.md")).replace("\\", "/"),
            str((SKILL_SUBPATH / "metadata.json")).replace("\\", "/"),
            *UPSTREAM_TOP_LEVEL_FILES,
        ],
    }
    (dest / "bundle_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    source = Path(args.source).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Source path does not exist: {source}")

    ref = args.ref.strip() or _detect_ref(source)
    if not ref:
        raise SystemExit("Unable to determine source ref. Provide --ref with a pinned commit or tag.")

    _copy_required_files(source, dest)
    _write_bundle_manifest(dest, repo_url=args.repo_url.strip() or DEFAULT_REPO_URL, ref=ref)
    print(f"Vendored ClickHouse agent skills into {dest}")
    print(f"  source_repo={args.repo_url}")
    print(f"  source_ref={ref}")
    print(f"  compiled_rules={COMPILED_RULES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
