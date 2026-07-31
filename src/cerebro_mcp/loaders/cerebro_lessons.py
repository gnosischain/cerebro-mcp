"""Loader for THIS repo's own lesson records.

Deliberately much simpler than `agent_context.py`, which serves dbt-cerebro's
corpus. That one fetches a built artifact over HTTP and has to reason about
staleness against a dbt manifest. This one reads package data that ships with the
code, so:

  - there is no artifact to build and no build step to forget;
  - there is no staleness window — the records ARE the source of truth;
  - it works identically from a checkout, a wheel and the Docker image, because
    `src/cerebro_mcp/prompts/lessons/` is inside the package (`docs/` is not
    shipped by either `pyproject.toml` or the Dockerfile).

Ranking is NOT reimplemented here: `score_lessons` is imported from
`agent_context` so both corpora rank identically and the same retrieval-eval bar
applies to both.

Profiles map a path prefix onto the rules and hazards that apply there. They match
CLASSES of paths and never enumerate files, so dropping a new module into a
directory inherits that directory's hazards with no edit to profiles.yml.
"""

from __future__ import annotations

import importlib.resources
import logging
import re
from functools import lru_cache
from typing import Any, Optional

import yaml

from cerebro_mcp.loaders.agent_context import score_lessons

logger = logging.getLogger(__name__)

PACKAGE = "cerebro_mcp.prompts.lessons"

#: The only statuses a record may declare. dbt-cerebro documents an identical
#: vocabulary but never validates it, and an undocumented fifth value ("primer")
#: has already drifted into that corpus — so this one is enforced by test.
VALID_STATUSES = frozenset({"proposed", "observed", "remediated", "enforced"})

#: Sections every record body must carry. Also unvalidated upstream.
REQUIRED_SECTIONS = (
    "## Symptom",
    "## Root cause",
    "## Forbidden action",
    "## Detection",
    "## Safe remediation",
    "## Enforcement",
)

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


def _parse_record(name: str, text: str) -> dict[str, Any]:
    """Split a record into frontmatter fields + body. Raises on malformed input.

    Raising rather than skipping is the point: a record that silently fails to
    parse is worse than no record, because the index still advertises it.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        raise ValueError(f"lesson {name!r}: missing or malformed YAML frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"lesson {name!r}: frontmatter is not a mapping")
    meta["body"] = match.group(2)
    meta["path"] = f"src/cerebro_mcp/prompts/lessons/{name}.md"
    meta.setdefault("id", name)
    return meta


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, dict], dict[str, Any]]:
    """Read every record + profiles.yml out of package data. Cached per process."""
    root = importlib.resources.files(PACKAGE)
    lessons: dict[str, dict] = {}
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        name = entry.name
        if not name.endswith(".md") or name == "INDEX.md":
            continue
        lessons[name[:-3]] = _parse_record(name[:-3], entry.read_text("utf-8"))
    profiles = yaml.safe_load(root.joinpath("profiles.yml").read_text("utf-8")) or {}
    return lessons, profiles


class CerebroLessonLoader:
    """Read-only access to this repo's lesson corpus."""

    def __init__(self) -> None:
        self.searches = 0
        self.search_hits = 0
        self.change_packets = 0

    @property
    def lessons(self) -> dict[str, dict]:
        return _load()[0]

    @property
    def profiles(self) -> dict[str, Any]:
        return _load()[1]

    def index_text(self) -> str:
        return (
            importlib.resources.files(PACKAGE).joinpath("INDEX.md").read_text("utf-8")
        )

    def get(self, lesson_id: str) -> Optional[dict]:
        return self.lessons.get(lesson_id)

    def profiles_for_path(self, path: str) -> list[dict[str, Any]]:
        """Every profile whose `path_prefix` matches, LEAST specific first.

        Sorted by prefix length rather than left in declaration order, so "most
        specific wins" is a property of the data and not of how the file happens to
        be arranged. `queries/` sits under `tools/visualization/`, so both match and
        the deeper one must come last for any scalar merge to be correct.

        `path_prefix` may be a single string or a list. A build layer is not one
        directory — Makefile, Dockerfile and .github/workflows/ are the same class of
        change — and forcing one prefix per profile meant either three near-duplicate
        profiles or, as happened, no coverage at all for two of the three.

        Normalisation strips a leading `./` as a PREFIX, not as a character set:
        `lstrip("./")` ate the dot off every dotfile, so `.github/workflows/x.yml`
        became `github/...` and could never match a declared `.github/` prefix.
        """
        path = (path or "").removeprefix("./").lstrip("/")
        matched = []
        for profile in self.profiles.get("profiles") or []:
            prefixes = (profile.get("match") or {}).get("path_prefix")
            if isinstance(prefixes, str):
                prefixes = [prefixes]
            hits = [p for p in prefixes or [] if path.startswith(p.rstrip("/"))]
            if hits:
                # Rank on the LONGEST matching prefix, so a profile does not become
                # less specific merely by also declaring a short one.
                matched.append((max(len(p) for p in hits), profile))
        matched.sort(key=lambda pair: pair[0])
        return [profile for _, profile in matched]

    def resolve(self, path: str) -> dict[str, Any]:
        """Rules, hazards, validation and guides that apply to `path`.

        Global layer first, then each matching profile. Lists merge with de-dup
        (first mention wins, so the global rule stays at the top); `guide`
        accumulates because an agent should read the repo guide AND the layer one.
        """
        merged: dict[str, Any] = {
            "path": path,
            "rules": [],
            "hazards": [],
            "validation": [],
            "guides": [],
            "profiles": [],
        }
        layers = [self.profiles.get("global") or {}] + self.profiles_for_path(path)
        for layer in layers:
            if layer.get("name"):
                merged["profiles"].append(layer["name"])
            for rule in layer.get("rules") or []:
                if rule not in merged["rules"]:
                    merged["rules"].append(rule)
            for key in ("hazards", "validation"):
                for item in layer.get(key) or []:
                    if item not in merged[key]:
                        merged[key].append(item)
            guide = layer.get("guide")
            if guide and guide not in merged["guides"]:
                merged["guides"].append(guide)
        return merged

    def search(self, query: str, path: Optional[str] = None, limit: int = 5) -> list[dict]:
        """Ranked lesson search, boosting the hazards of `path` when given."""
        self.searches += 1
        boost = set(self.resolve(path)["hazards"]) if path else set()
        results = score_lessons(
            self.lessons.values(), query, limit=limit, boost_ids=boost
        )
        if results:
            self.search_hits += 1
        return results

    def stats(self) -> dict[str, Any]:
        return {
            "lessons": len(self.lessons),
            "profiles": len(self.profiles.get("profiles") or []),
            "searches": self.searches,
            "search_hits": self.search_hits,
            "change_packets": self.change_packets,
        }


cerebro_lessons = CerebroLessonLoader()
