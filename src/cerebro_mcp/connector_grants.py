"""Grant-closure computation for the connector ClickHouse identity.

Operates on the RAW dbt manifest (nodes + sources + parent_map) — never on
the server's filtered indexes, because the privacy predicate must see source
nodes too (``loaders/manifest.py`` filters only model nodes; a source can
carry personal data with no model tag anywhere near it).

Fail-closed rules (connector plan R10 C4, staged identities per C5):

- A model whose transitive closure touches a PRIVACY-EXCLUDED relation
  (``internal_only`` / ``privacy:tier_internal`` tags, or
  ``meta.expose_to_mcp: false``) is EXCLUDED — never granted through.
- Terminal ``source.*`` relations are DEFAULT-DENIED. A source is granted
  only when listed in the reviewed approvals file; models depending on an
  unapproved source land in the REVIEW-REQUIRED worklist, not the grant set.
- Every relation in a granted closure IS granted, INCLUDING intermediate
  views' parents — that is unavoidable, because an invoker-executed view
  cannot read what the caller cannot. The consequence is explicit and must
  be reviewed rather than papered over: granting a narrowing view's parents
  lets a caller query the wider parent directly and bypass the narrowing.
  Where that is unacceptable the answer is a ``SQL SECURITY DEFINER``
  connector view or a connector-safe materialization, decided per case —
  NOT a per-view "passthrough approval" flag, which cannot express the
  distinction (a view's parents are already in its own root's closure, so
  the flag blocked marts while their inputs stayed granted).
- Unknown node types, missing ``schema``/physical identifiers, or lineage
  gaps are hard errors — silence here would become a silent grant hole.

The CLI wrapper (``scripts/generate_connector_grants.py``) emits a staged
``CREATE USER cerebro_connector_v<N>`` + the GRANT set + the manifest SHA to
pin as ``MCP_EXPECTED_MANIFEST_SHA256``. There is no REVOKE choreography:
each reconciliation builds a FRESH versioned identity, verifies it, then the
deployment switches credentials and disables the previous user (R10 C5 —
a partial failure never leaves the serving identity in a mixed state).
"""

from __future__ import annotations

from dataclasses import dataclass, field

PRIVACY_TAGS = {"internal_only", "privacy:tier_internal"}


class GrantClosureError(Exception):
    """Manifest shape violation — fail closed, never emit partial grants."""


@dataclass
class ClosureResult:
    #: physical "db"."table" relations safe to grant, sorted
    granted: list[str] = field(default_factory=list)
    #: root model -> reason, for models excluded by the privacy predicate
    excluded: dict[str, str] = field(default_factory=dict)
    #: root model -> unapproved relations blocking it (unapproved sources)
    review_required: dict[str, list[str]] = field(default_factory=dict)


def _is_privacy_excluded(node: dict) -> bool:
    tags = set(node.get("tags") or [])
    if tags & PRIVACY_TAGS:
        return True
    meta = (node.get("meta") or {}) if isinstance(node.get("meta"), dict) else {}
    return meta.get("expose_to_mcp") is False


def _physical_relation(node: dict, node_id: str) -> str:
    schema = node.get("schema")
    identifier = node.get("alias") or node.get("identifier") or node.get("name")
    if not schema or not identifier:
        raise GrantClosureError(
            f"{node_id}: missing schema/identifier — cannot address the "
            "physical relation; refusing to guess."
        )
    return f"{schema}.{identifier}"


def _node(manifest: dict, node_id: str) -> dict:
    if node_id.startswith("source."):
        found = (manifest.get("sources") or {}).get(node_id)
    else:
        found = (manifest.get("nodes") or {}).get(node_id)
    if found is None:
        raise GrantClosureError(
            f"{node_id}: referenced in lineage but absent from the manifest "
            "— incomplete lineage, refusing to emit grants."
        )
    return found


def compute_grant_closure(
    manifest: dict,
    *,
    approved_sources: set[str],
) -> ClosureResult:
    """Compute the fail-closed grant set for the connector identity.

    Roots are every dbt MODEL not privacy-excluded (the connector profile
    allows ``dbt.*`` caller SQL, so every reachable model must either be
    fully granted or knowingly absent). ``approved_sources`` holds
    physical ``db.table`` names.
    """
    nodes = manifest.get("nodes")
    parent_map = manifest.get("parent_map")
    if not isinstance(nodes, dict) or not isinstance(parent_map, dict):
        raise GrantClosureError("manifest missing nodes/parent_map")

    result = ClosureResult()
    granted: set[str] = set()

    for node_id, node in sorted(nodes.items()):
        rtype = node.get("resource_type")
        if rtype in ("test", "seed", "operation", "analysis", "macro"):
            continue
        if rtype != "model":
            raise GrantClosureError(
                f"{node_id}: unknown resource_type {rtype!r} — refusing to "
                "classify it silently."
            )
        name = node.get("name", node_id)
        if _is_privacy_excluded(node):
            result.excluded[name] = "privacy predicate (tags/meta)"
            continue

        # Transitive closure over parent_map.
        closure_ids: list[str] = []
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            closure_ids.append(current)
            for parent in parent_map.get(current, []) or []:
                if parent.startswith(("test.", "seed.", "macro.")):
                    continue
                stack.append(parent)

        blockers: list[str] = []
        privacy_blocked = False
        relations: list[str] = []
        for cid in closure_ids:
            cnode = _node(manifest, cid)
            if _is_privacy_excluded(cnode):
                privacy_blocked = True
                break
            rel = _physical_relation(cnode, cid)
            if cid.startswith("source."):
                if rel not in approved_sources:
                    blockers.append(f"source:{rel}")
                    continue
            relations.append(rel)

        if privacy_blocked:
            result.excluded[name] = "closure touches a privacy-excluded relation"
        elif blockers:
            result.review_required[name] = sorted(set(blockers))
        else:
            granted.update(relations)

    result.granted = sorted(granted)
    return result


def render_grant_script(
    result: ClosureResult, *, user: str, manifest_sha: str
) -> str:
    """Render the staged-identity SQL script plus the pin to record."""
    lines = [
        f"-- connector grant script for staged identity {user}",
        f"-- manifest sha256 to pin: {manifest_sha}",
        f"-- granted relations: {len(result.granted)}",
        f"-- excluded (privacy): {len(result.excluded)}",
        f"-- review required:    {len(result.review_required)}",
        "",
        f"CREATE USER IF NOT EXISTS {user} IDENTIFIED BY '<from Parameter Store>'",
        "  SETTINGS max_memory_usage = 4000000000, max_execution_time = 30;",
        "-- NO role grants, NO default roles, NO SOURCES/table-function",
        "-- privileges of any kind (verified against system.privileges).",
        "",
    ]
    for rel in result.granted:
        db, _, table = rel.partition(".")
        lines.append(f"GRANT SELECT ON `{db}`.`{table}` TO {user};")
    if result.review_required:
        lines.append("")
        lines.append("-- REVIEW REQUIRED (not granted; fail closed):")
        for model, blockers in sorted(result.review_required.items()):
            lines.append(f"--   {model}: {', '.join(blockers)}")
    return "\n".join(lines) + "\n"
