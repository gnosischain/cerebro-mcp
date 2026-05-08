"""Lint tests for the docs/measurement subtree.

The unified MMM + MTA workflow ships with a dedicated docs subtree at
docs/measurement/. These tests check that:

- The expected pages exist and are non-empty.
- Worked examples include the artifacts that make them executable as
  references (dispatcher manifest, verdict block, coverage figure, SQL).
- Top-level docs (README.md, CLAUDE.md) cite the new workflow sections.
- The new persona files cross-link into docs/measurement/ so the conceptual
  framing and the operational rules don't drift apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs" / "measurement"
EXAMPLES_DIR = DOCS_DIR / "examples"
PERSONAS_DIR = (
    REPO_ROOT / "src" / "cerebro_mcp" / "prompts" / "agents"
)


REQUIRED_PAGES = [
    "README.md",
    "mmm_overview.md",
    "mta_overview.md",
    "unified_measurement.md",
    "causal_review.md",
    "identity_grain.md",
    "glossary.md",
]

REQUIRED_EXAMPLES = [
    "mta_app_topups.md",
    "unified_pay_subsidy.md",
]

NEW_PERSONAS = [
    "mta_analyst.md",
    "unified_causal_reviewer.md",
    "unified_allocator.md",
]


@pytest.mark.parametrize("page", REQUIRED_PAGES)
def test_measurement_doc_exists(page):
    p = DOCS_DIR / page
    assert p.exists(), f"missing docs/measurement/{page}"
    assert p.stat().st_size > 200, f"docs/measurement/{page} looks empty"


@pytest.mark.parametrize("page", REQUIRED_EXAMPLES)
def test_measurement_example_exists(page):
    p = EXAMPLES_DIR / page
    assert p.exists(), f"missing docs/measurement/examples/{page}"
    assert p.stat().st_size > 500, f"docs/measurement/examples/{page} looks empty"


def test_unified_example_contains_chain_artifacts():
    """The unified example must walk the full chain so it's actually useful."""
    text = (EXAMPLES_DIR / "unified_pay_subsidy.md").read_text("utf-8")
    lower = text.lower()
    assert "dispatch manifest" in lower
    assert "verdict" in lower
    assert "coverage" in lower
    # Calibration math must appear
    assert "calibrated" in lower
    assert "unexplained" in lower or "untracked" in lower
    # At least one ClickHouse SQL block
    assert "```sql" in text or "windowFunnel" in text or "INTERVAL" in text


def test_mta_example_contains_artifacts():
    text = (EXAMPLES_DIR / "mta_app_topups.md").read_text("utf-8")
    lower = text.lower()
    assert "dispatch manifest" in lower
    assert "coverage" in lower
    assert "identity grain" in lower
    assert "```sql" in text or "windowFunnel" in text or "INTERVAL" in text


def test_readme_table_lists_unified_workflow():
    text = (REPO_ROOT / "README.md").read_text("utf-8")
    assert "MTA workflow" in text or "MTA Workflow" in text
    assert "Unified measurement" in text or "Unified Measurement" in text
    # Cross-reference to docs/measurement
    assert "docs/measurement" in text


def test_claude_md_has_mta_and_unified_sections():
    text = (REPO_ROOT / "CLAUDE.md").read_text("utf-8")
    assert "MTA Workflow" in text
    assert "Unified MMM + MTA Workflow" in text or "Unified Workflow" in text


@pytest.mark.parametrize("persona", NEW_PERSONAS)
def test_persona_references_docs_measurement(persona):
    """Persona files must cross-link to the conceptual docs subtree."""
    text = (PERSONAS_DIR / persona).read_text("utf-8")
    assert "docs/measurement/" in text, (
        f"persona {persona} must link to at least one docs/measurement/ page"
    )
