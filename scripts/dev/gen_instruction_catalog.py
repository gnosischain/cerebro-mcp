#!/usr/bin/env python3
"""Compile catalog/templates/*.md into the checked-in instruction catalog JSON.

The Template Gallery mini-app (ui/src/mini-apps/report-studio) and the
benchmarks templates suite (benchmarks/cases/template_cases.py) BOTH consume
the generated file, so the single source of truth for template content is the
markdown files and this script is the validation choke point.

Template file format (stdlib-parseable "JSON frontmatter"):

    ---
    { ...one JSON object... }
    ---

    <markdown body = the copyable instruction text>

Frontmatter fields:
    id            str, must equal the filename stem, unique across the catalog
    label         str, card title
    purpose       str, one-line card subtitle
    category      one of CATEGORIES
    tier          one of TIERS
    deliverable   str, what the user gets when an agent executes the template
    params        [{name, description, example}] — name is UPPER_SNAKE
    personas      [role, ...] shown on the card (may be empty)
    verify_personas  optional [role, ...] the benchmark harness asserts were
                     adopted via get_agent_persona in the run's session trace;
                     defaults to `personas`
    requires      [str, ...] env-gate labels (empty in v1; reserved)
    benchmark     {runs, timeout_s, budget_usd, verify} — verify in VERIFY_KINDS

Body placeholders use `{{UPPER_SNAKE}}`. The regex is deliberately strict
(uppercase names only) so report-engine directives such as `{{grid:3}}` and
`{{chart:ID}}` inside instruction text are NOT treated as parameters.

Output is deterministic (sorted ids, sorted keys, no timestamps) so
`make gen-catalog && git diff --exit-code` works as a freshness check.

Usage:  python scripts/dev/gen_instruction_catalog.py [--check]
    --check   validate + compare against the committed output; exit 1 on drift
              without rewriting it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "catalog" / "templates"
OUTPUT_PATH = (
    REPO_ROOT / "ui" / "src" / "mini-apps" / "report-studio" / "model" / "catalog.gen.json"
)

SCHEMA_VERSION = 1

CATEGORIES = {
    "answer",
    "chart",
    "sector_health",
    "deep_dive",
    "narrative",
    "attribution",
    "forecast",
    "governance",
    "utility",
}
TIERS = {"quick_answer", "single_chart", "lite_report", "full_report", "persona_workflow"}
VERIFY_KINDS = {"report_file", "charts", "answer", "export"}

#: Mirror of _VALID_ROLES in src/cerebro_mcp/tools/governance/agents.py.
#: Kept literal (this script must stay stdlib-only / import-pure); the backend
#: catalog contract test cross-checks the two sets so drift fails CI.
KNOWN_ROLES = {
    "analytics_reporter",
    "ui_designer",
    "reality_checker",
    "storyteller_orchestrator",
    "storyteller_context",
    "storyteller_narrative",
    "storyteller_visual_designer",
    "storyteller_writer",
    "storyteller_critic",
    "storyteller_accessibility",
    "forecasting_analyst",
    "growth_analyst",
    "tokenomics_analyst",
    "defi_analyst",
    "network_health_analyst",
    "bridge_security_analyst",
    "marketing_analyst",
    "esg_analyst",
    "statistical_reviewer",
    "mmm_analyst",
    "mmm_causal_reviewer",
    "mmm_simulator",
    "mta_analyst",
    "unified_causal_reviewer",
    "unified_allocator",
    "cerebro_dispatcher",
    "grafana_architect",
    "chain_forensics",
    "transaction_forensics",
    "pattern_forensics",
    "forensic_reviewer",
    "gnosis_research_analyst",
    "cow_analyst",
    "dao_governance_analyst",
    "chain_state_analyst",
}

PARAM_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


class CatalogError(Exception):
    pass


def parse_template_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise CatalogError(f"{path.name}: must start with a '---' frontmatter fence")
    try:
        _, front, body = text.split("---\n", 2)
    except ValueError as exc:
        raise CatalogError(f"{path.name}: missing closing '---' frontmatter fence") from exc
    try:
        meta = json.loads(front)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path.name}: frontmatter is not valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise CatalogError(f"{path.name}: frontmatter must be a JSON object")
    meta["instructions"] = body.strip()
    return meta


def validate_template(meta: dict, filename_stem: str) -> dict:
    name = f"{filename_stem}.md"

    def need(key: str, kind: type) -> object:
        if key not in meta:
            raise CatalogError(f"{name}: missing frontmatter field '{key}'")
        value = meta[key]
        if not isinstance(value, kind):
            raise CatalogError(f"{name}: field '{key}' must be {kind.__name__}")
        return value

    template_id = need("id", str)
    if template_id != filename_stem:
        raise CatalogError(f"{name}: id '{template_id}' must equal the filename stem")
    for key in ("label", "purpose", "deliverable"):
        if not str(need(key, str)).strip():
            raise CatalogError(f"{name}: field '{key}' must be non-empty")
    category = need("category", str)
    if category not in CATEGORIES:
        raise CatalogError(f"{name}: category '{category}' not in {sorted(CATEGORIES)}")
    tier = need("tier", str)
    if tier not in TIERS:
        raise CatalogError(f"{name}: tier '{tier}' not in {sorted(TIERS)}")

    params = need("params", list)
    declared: set[str] = set()
    for entry in params:
        if not isinstance(entry, dict):
            raise CatalogError(f"{name}: each param must be an object")
        for key in ("name", "description", "example"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise CatalogError(f"{name}: param field '{key}' must be a non-empty string")
        if not PARAM_NAME_RE.match(entry["name"]):
            raise CatalogError(f"{name}: param name '{entry['name']}' must be UPPER_SNAKE")
        if entry["name"] in declared:
            raise CatalogError(f"{name}: duplicate param '{entry['name']}'")
        declared.add(entry["name"])

    body = meta["instructions"]
    if not body:
        raise CatalogError(f"{name}: instruction body is empty")
    used = set(PLACEHOLDER_RE.findall(body))
    if used - declared:
        raise CatalogError(
            f"{name}: body uses undeclared params {sorted(used - declared)}"
        )
    if declared - used:
        raise CatalogError(
            f"{name}: declared params never used in body {sorted(declared - used)}"
        )

    personas = need("personas", list)
    for role in personas:
        if role not in KNOWN_ROLES:
            raise CatalogError(f"{name}: unknown persona '{role}'")
    verify_personas = meta.get("verify_personas", list(personas))
    if not isinstance(verify_personas, list):
        raise CatalogError(f"{name}: verify_personas must be a list")
    for role in verify_personas:
        if role not in KNOWN_ROLES:
            raise CatalogError(f"{name}: unknown verify persona '{role}'")

    requires = need("requires", list)
    if any(not isinstance(item, str) for item in requires):
        raise CatalogError(f"{name}: requires entries must be strings")

    benchmark = need("benchmark", dict)
    if not isinstance(benchmark.get("runs"), int) or benchmark["runs"] < 1:
        raise CatalogError(f"{name}: benchmark.runs must be a positive int")
    if not isinstance(benchmark.get("timeout_s"), int) or benchmark["timeout_s"] < 30:
        raise CatalogError(f"{name}: benchmark.timeout_s must be an int >= 30")
    if not isinstance(benchmark.get("budget_usd"), (int, float)) or benchmark["budget_usd"] <= 0:
        raise CatalogError(f"{name}: benchmark.budget_usd must be a positive number")
    if benchmark.get("verify") not in VERIFY_KINDS:
        raise CatalogError(
            f"{name}: benchmark.verify '{benchmark.get('verify')}' not in {sorted(VERIFY_KINDS)}"
        )

    return {
        "id": template_id,
        "label": meta["label"],
        "purpose": meta["purpose"],
        "category": category,
        "tier": tier,
        "deliverable": meta["deliverable"],
        "params": params,
        "personas": personas,
        "verify_personas": verify_personas,
        "requires": requires,
        "benchmark": {
            "runs": benchmark["runs"],
            "timeout_s": benchmark["timeout_s"],
            "budget_usd": benchmark["budget_usd"],
            "verify": benchmark["verify"],
        },
        "instructions": body,
    }


def build_catalog() -> dict:
    if not TEMPLATES_DIR.is_dir():
        raise CatalogError(f"templates directory not found: {TEMPLATES_DIR}")
    files = sorted(TEMPLATES_DIR.glob("*.md"))
    if not files:
        raise CatalogError(f"no template files in {TEMPLATES_DIR}")
    templates = []
    seen: set[str] = set()
    for path in files:
        meta = parse_template_file(path)
        entry = validate_template(meta, path.stem)
        if entry["id"] in seen:
            raise CatalogError(f"duplicate template id '{entry['id']}'")
        seen.add(entry["id"])
        templates.append(entry)
    templates.sort(key=lambda item: item["id"])
    return {"schema_version": SCHEMA_VERSION, "templates": templates}


def render(catalog: dict) -> str:
    return json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    try:
        rendered = render(build_catalog())
    except CatalogError as exc:
        print(f"gen_instruction_catalog: {exc}", file=sys.stderr)
        return 1
    if check_only:
        current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
        if current != rendered:
            print(
                "gen_instruction_catalog: catalog.gen.json is stale — run "
                "`make gen-catalog` and commit the result",
                file=sys.stderr,
            )
            return 1
        print(f"catalog OK ({OUTPUT_PATH.relative_to(REPO_ROOT)})")
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    count = rendered.count('"id":')
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
