"""Case table for the templates benchmark suite.

IMPORT-PURE (stdlib only) per benchmarks/cases convention — compare/docs can
enumerate cases without booting a server. The single source of truth for
template content is ``catalog/templates/*.md`` compiled by
``scripts/dev/gen_instruction_catalog.py`` into the checked-in
``ui/src/mini-apps/report-studio/model/catalog.gen.json``; this module only
loads that file and pins the concrete parameter fills used for measurement.

Default fills come from each param's frontmatter ``example`` value, so the
template files themselves define a benchmarkable configuration; PARAM_FILLS
overrides exist only where the benchmark wants a specific heavier/realer value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = (
    REPO_ROOT / "ui" / "src" / "mini-apps" / "report-studio" / "model" / "catalog.gen.json"
)

#: Per-template parameter overrides for measurement (defaults = param examples).
PARAM_FILLS: dict[str, dict[str, str]] = {
    "cross_product_behavior": {"PRODUCT_A": "Gnosis Pay", "PRODUCT_B": "Aave"},
}


@dataclass(frozen=True)
class TemplateCase:
    id: str
    label: str
    tier: str
    verify: str
    verify_personas: tuple[str, ...]
    runs: int
    timeout_s: int
    budget_usd: float
    instructions: str
    params: dict[str, str] = field(default_factory=dict)

    @property
    def case_id(self) -> str:
        return f"templates/{self.id}"


def load_template_cases() -> list[TemplateCase]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    cases: list[TemplateCase] = []
    for entry in catalog["templates"]:
        fills = {p["name"]: p["example"] for p in entry["params"]}
        fills.update(PARAM_FILLS.get(entry["id"], {}))
        bench = entry["benchmark"]
        cases.append(
            TemplateCase(
                id=entry["id"],
                label=entry["label"],
                tier=entry["tier"],
                verify=bench["verify"],
                verify_personas=tuple(entry.get("verify_personas", [])),
                runs=int(bench["runs"]),
                timeout_s=int(bench["timeout_s"]),
                budget_usd=float(bench["budget_usd"]),
                instructions=entry["instructions"],
                params=fills,
            )
        )
    return cases
