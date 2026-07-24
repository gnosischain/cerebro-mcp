// Instruction-template catalog: the merge of two GENERATED files.
//
// - catalog.gen.json    — template content, compiled from catalog/templates/*.md
//                         by scripts/dev/gen_instruction_catalog.py (make gen-catalog).
// - benchmarks.gen.json — measured numbers, distilled from templates-suite runs
//                         by benchmarks/distill_templates.py (make distill-templates).
//
// Never edit either JSON by hand; edit the markdown templates or re-measure.
// A template without a measurement entry renders as "not yet measured".

import catalogData from "./catalog.gen.json";
import benchmarksData from "./benchmarks.gen.json";

export type TemplateTier =
  | "quick_answer"
  | "single_chart"
  | "lite_report"
  | "full_report"
  | "persona_workflow";

export interface TemplateParam {
  name: string;
  description: string;
  example: string;
}

export interface Spread {
  median: number;
  min: number;
  max: number;
}

export interface TemplateMeasurement {
  n_runs: number | null;
  delivered: number | null;
  review_passed: number | null;
  review_total: number | null;
  duration_ms: Spread | null;
  tokens: { in_fresh: number | null; out: number | null; cache_read: number | null };
  cost_usd: Spread | null;
  num_turns_median: number | null;
  measured_at: string | null;
}

export interface InstructionTemplate {
  id: string;
  label: string;
  purpose: string;
  category: string;
  tier: TemplateTier;
  deliverable: string;
  params: TemplateParam[];
  personas: string[];
  verify_personas: string[];
  requires: string[];
  benchmark: { runs: number; timeout_s: number; budget_usd: number; verify: string };
  instructions: string;
  /** model id -> measured numbers; empty when not yet measured. */
  measurements: Record<string, TemplateMeasurement>;
}

export const TIER_LABELS: Record<TemplateTier, string> = {
  quick_answer: "Quick answer",
  single_chart: "Single chart",
  lite_report: "Lite (inline charts)",
  full_report: "Full report",
  persona_workflow: "Specialist workflow",
};

//: Fastest-to-heaviest, used for the default catalog ordering.
export const TIER_ORDER: TemplateTier[] = [
  "quick_answer",
  "single_chart",
  "lite_report",
  "full_report",
  "persona_workflow",
];

interface RawCatalog {
  schema_version: number;
  templates: Array<Omit<InstructionTemplate, "measurements">>;
}

interface RawBenchmarks {
  schema_version: number;
  sources: Record<string, string>;
  templates: Record<string, Record<string, TemplateMeasurement>>;
}

const rawCatalog = catalogData as unknown as RawCatalog;
const rawBenchmarks = benchmarksData as unknown as RawBenchmarks;

export const MEASURED_MODELS: string[] = Object.keys(rawBenchmarks.sources).sort();

export const CATALOG: InstructionTemplate[] = rawCatalog.templates
  .map((entry) => ({
    ...entry,
    measurements: rawBenchmarks.templates[entry.id] ?? {},
  }))
  .sort(
    (a, b) =>
      TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier)
      || a.label.localeCompare(b.label),
  );

export function templateById(id: string): InstructionTemplate | undefined {
  return CATALOG.find((t) => t.id === id);
}

/** Fill {{PARAM}} placeholders; missing values keep the placeholder visible. */
export function fillInstructions(template: InstructionTemplate, values: Record<string, string>): string {
  let text = template.instructions;
  for (const param of template.params) {
    const value = values[param.name]?.trim();
    if (value) text = text.split(`{{${param.name}}}`).join(value);
  }
  return text;
}

export function formatDuration(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  const minutes = ms / 60_000;
  return minutes < 10 ? `${minutes.toFixed(1)} min` : `${Math.round(minutes)} min`;
}

export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${Math.round(count / 1_000)}k`;
  return String(count);
}

export function formatCost(usd: number): string {
  return usd < 0.995 ? `$${usd.toFixed(2)}` : `$${usd.toFixed(1)}`;
}

/** Short display name for a model id ("claude-sonnet-5" -> "Sonnet 5"). */
export function modelLabel(modelId: string): string {
  return modelId
    .replace(/^claude-/, "")
    .split("-")
    .map((part) => (/^\d/.test(part) ? part.replace(/-/g, ".") : part[0]?.toUpperCase() + part.slice(1)))
    .join(" ")
    .replace(/(\d) (\d)/, "$1.$2");
}
