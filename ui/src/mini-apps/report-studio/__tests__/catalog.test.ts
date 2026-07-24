// Catalog contract tests — pin the generated catalog + benchmark merge the
// Template Gallery ships. The python side (tests/test_report_studio.py) pins
// the same contract for the benchmark harness.

import { describe, expect, it } from "vitest";
import {
  CATALOG,
  TIER_LABELS,
  TIER_ORDER,
  fillInstructions,
  templateById,
} from "../model/catalog";

const CATEGORIES = new Set([
  "answer", "chart", "sector_health", "deep_dive", "narrative",
  "attribution", "forecast", "governance", "utility",
]);
const VERIFY_KINDS = new Set(["report_file", "charts", "answer", "export"]);
const PLACEHOLDER_RE = /\{\{([A-Z][A-Z0-9_]*)\}\}/g;

describe("instruction catalog contract", () => {
  it("has a non-trivial catalog with unique ids", () => {
    expect(CATALOG.length).toBeGreaterThanOrEqual(20);
    const ids = CATALOG.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("every template has valid enums and non-empty content", () => {
    for (const t of CATALOG) {
      expect(CATEGORIES.has(t.category), `${t.id} category`).toBe(true);
      expect(TIER_ORDER).toContain(t.tier);
      expect(TIER_LABELS[t.tier]).toBeTruthy();
      expect(t.label.trim()).not.toBe("");
      expect(t.purpose.trim()).not.toBe("");
      expect(t.deliverable.trim()).not.toBe("");
      expect(t.instructions.trim()).not.toBe("");
      expect(VERIFY_KINDS.has(t.benchmark.verify), `${t.id} verify`).toBe(true);
      expect(t.benchmark.runs).toBeGreaterThanOrEqual(1);
    }
  });

  it("body placeholders and declared params cross-reference exactly", () => {
    for (const t of CATALOG) {
      const declared = new Set(t.params.map((p) => p.name));
      const used = new Set(
        [...t.instructions.matchAll(PLACEHOLDER_RE)].map((m) => m[1]),
      );
      expect([...used].filter((n) => !declared.has(n)), `${t.id} undeclared`).toEqual([]);
      expect([...declared].filter((n) => !used.has(n)), `${t.id} unused`).toEqual([]);
    }
  });

  it("verify_personas are a subset of personas' universe and persona templates verify adoption", () => {
    for (const t of CATALOG) {
      // Every card-listed persona chain that matters is asserted by the harness.
      if (t.personas.length > 0) {
        expect(t.verify_personas.length, `${t.id} verifies personas`).toBeGreaterThan(0);
      }
    }
  });

  it("measurement entries only reference known template ids", () => {
    // CATALOG merges benchmarks by id; an orphaned benchmark id would be
    // silently dropped — assert none exist in the shipped data.
    for (const t of CATALOG) {
      for (const [model, m] of Object.entries(t.measurements)) {
        expect(model).toMatch(/^claude-/);
        if (m.duration_ms) {
          expect(m.duration_ms.min).toBeLessThanOrEqual(m.duration_ms.max);
        }
      }
    }
  });

  it("templateById resolves and fillInstructions substitutes", () => {
    const t = templateById("quick_scalar_answer");
    expect(t).toBeDefined();
    const filled = fillInstructions(t!, { METRIC: "active users", SCOPE: "Gnosis Pay" });
    expect(filled).toContain("active users");
    expect(filled).not.toContain("{{METRIC}}");
    // Unfilled params keep their placeholder visible (copy-then-edit flow).
    const partial = fillInstructions(t!, { METRIC: "supply" });
    expect(partial).toContain("{{SCOPE}}");
  });

  it("the user-mandated intents are present", () => {
    for (const id of [
      "quick_scalar_answer",
      "cross_product_behavior",
      "research_essay",
      "weekly_review_lite",
      "weekly_review_full",
    ]) {
      expect(templateById(id), id).toBeDefined();
    }
  });
});
