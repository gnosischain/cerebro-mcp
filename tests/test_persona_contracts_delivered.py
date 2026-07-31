"""A persona must receive the contracts it declares mandatory.

30 personas open with "you MUST apply every rule in
`_shared_quality_rules.md`", and the report gate's own rejection message tells
the model to "follow the rules in `_shared_quality_rules.md`". Nothing
delivered that file: `get_agent_persona` read exactly `{role}.md`, the file is
not a valid role, and a relative markdown link is not resolvable by a client
with no filesystem access to the installed package.

The four SQL-discipline rules the gate enforces were stated only there, so the
requirements most likely to refuse a report were also the ones no model could
read. Three template runs against a real agent failed adversarial review with
fabricated statistics and correlations quoted without stationarity handling —
precisely the rules in the undelivered file.
"""

from __future__ import annotations

import importlib.resources

import pytest

from cerebro_mcp.tools.governance.agents import _VALID_ROLES, load_persona

SHARED_RULES = "_shared_quality_rules.md"
FORENSIC_STANDARDS = "_forensic_standards.md"


def _raw(filename: str) -> str:
    return (
        importlib.resources.files("cerebro_mcp.prompts.agents")
        .joinpath(filename)
        .read_text("utf-8")
    )


def _roles_declaring(contract: str) -> list[str]:
    out = []
    for role in sorted(_VALID_ROLES):
        if contract in _raw(f"{role}.md"):
            out.append(role)
    return out


def test_some_personas_actually_declare_the_shared_rules():
    """Guards the guard: if the declaration disappeared, every delivery
    assertion below would pass vacuously."""
    assert len(_roles_declaring(SHARED_RULES)) >= 20


@pytest.mark.parametrize("role", _roles_declaring(SHARED_RULES))
def test_declared_quality_rules_are_delivered(role):
    text = load_persona(role)
    # A distinctive heading, not the filename — the filename appears in the
    # persona's own reference, so matching it would pass without delivery.
    assert "Denominator discipline" in text, (
        f"{role} declares {SHARED_RULES} mandatory but does not receive it"
    )
    assert "Stock vs flow" in text


@pytest.mark.parametrize("role", _roles_declaring(FORENSIC_STANDARDS))
def test_declared_forensic_standards_are_delivered(role):
    text = load_persona(role)
    assert f"Inlined: {FORENSIC_STANDARDS}" in text


def test_a_persona_that_declares_nothing_stays_lean():
    """Delivery is driven by the persona's own reference, so a persona with no
    dependency must not pay for one."""
    assert SHARED_RULES not in _raw("ui_designer.md")
    text = load_persona("ui_designer")
    assert "Denominator discipline" not in text
    assert len(text) == len(_raw("ui_designer.md"))


def test_every_enforced_discipline_rule_is_written_down():
    """The gate cites `_shared_quality_rules.md` by name when it refuses. Each
    rule it enforces must therefore exist there — `aggregator_volume_dedup` was
    enforced with no corresponding rule in the file, so the refusal pointed at
    guidance that did not exist."""
    rules = _raw(SHARED_RULES).lower()
    for topic in ("stock vs flow", "denominator discipline",
                  "stationarity", "aggregator"):
        assert topic in rules, f"gate enforces {topic} but the file omits it"


def test_the_prompt_loaders_deliver_the_same_thing():
    """`get_agent_persona` and the @mcp.prompt() loaders are two front doors to
    the same persona; they must not disagree about its contents."""
    from cerebro_mcp.prompts.templates import (
        load_gnosis_research_analyst_markdown,
    )

    assert load_gnosis_research_analyst_markdown() == load_persona(
        "gnosis_research_analyst"
    )
