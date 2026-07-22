// Snapshot choice/score helpers. Vocabulary rule: Snapshot is off-chain
// signaling — nothing here ever says "passed" / "failed" / "winner"; the
// strongest wording is "leading choice".

export interface ChoiceEntry {
  index: number; // 1-based, matching Snapshot's vote encoding
  label: string;
  score: number | null;
}

export interface PairedChoices {
  entries: ChoiceEntry[];
  /** true when choices/scores arrays disagree in length or fail to parse. */
  mismatch: boolean;
}

function parseJsonArray(json: unknown): unknown[] | null {
  if (Array.isArray(json)) return json;
  if (typeof json !== "string" || json.trim() === "") return null;
  try {
    const parsed = JSON.parse(json);
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Zip the proposal's choices JSON with its scores JSON. Scores may be
 * missing/short while `scores_state` is pending — that is a mismatch flag,
 * not an error; entries keep `score: null`. */
export function pairChoices(choicesJson: unknown, scoresJson: unknown): PairedChoices {
  const choices = parseJsonArray(choicesJson);
  if (!choices) return { entries: [], mismatch: true };
  const scores = parseJsonArray(scoresJson);
  const entries: ChoiceEntry[] = choices.map((choice, i) => {
    const rawScore = scores?.[i];
    const score = typeof rawScore === "number" && Number.isFinite(rawScore) ? rawScore : null;
    return { index: i + 1, label: String(choice), score };
  });
  const mismatch = scores === null || scores.length !== choices.length;
  return { entries, mismatch };
}

export interface LeadingChoice {
  index: number;
  label: string;
  score: number;
  /** Leading score / total score (0..1); null when total is 0. */
  share: number | null;
  /** true when another choice holds the same top score. */
  tie: boolean;
}

/** The choice with the highest score. Null when there are no entries, no
 * scores, or every score is zero (nothing is "leading" yet). */
export function leadingChoice(entries: ChoiceEntry[]): LeadingChoice | null {
  const scored = entries.filter((e) => e.score !== null && e.score > 0) as
    Array<ChoiceEntry & { score: number }>;
  if (scored.length === 0) return null;
  let top = scored[0];
  for (const entry of scored) if (entry.score > top.score) top = entry;
  const tie = scored.some((e) => e.index !== top.index && e.score === top.score);
  const total = entries.reduce((sum, e) => sum + (e.score ?? 0), 0);
  return {
    index: top.index,
    label: top.label,
    score: top.score,
    share: total > 0 ? top.score / total : null,
    tie,
  };
}

export interface RenderedVoteChoice {
  kind: "single" | "ranked" | "unknown";
  text: string;
  /** Ranked votes only: ordered preference strings ("1st: X", "2nd: Y", ...). */
  parts?: string[];
  /** true when an index falls outside 1..choices.length. */
  outOfRange?: boolean;
}

const ORDINALS = ["1st", "2nd", "3rd"];

function ordinal(position: number): string {
  return ORDINALS[position - 1] ?? `${position}th`;
}

function labelFor(index: number, choices: string[]): { label: string; outOfRange: boolean } {
  if (Number.isInteger(index) && index >= 1 && index <= choices.length) {
    return { label: choices[index - 1], outOfRange: false };
  }
  return { label: `choice ${index} (out of range)`, outOfRange: true };
}

/** Render one vote's raw `choice` value against the proposal's choice list.
 * Snapshot encodes: Int = single choice (1-based); Int array = ranked
 * ordered preferences. Objects / strings / anything else are an unsupported
 * shape and render as `unknown` (flagged, never guessed). */
export function renderVoteChoice(raw: unknown, choices: string[]): RenderedVoteChoice {
  let value = raw;
  if (typeof value === "string" && value.trim() !== "") {
    try {
      value = JSON.parse(value);
    } catch {
      return { kind: "unknown", text: "Unsupported choice shape" };
    }
  }
  if (typeof value === "number" && Number.isInteger(value)) {
    const { label, outOfRange } = labelFor(value, choices);
    return { kind: "single", text: label, outOfRange };
  }
  if (Array.isArray(value) && value.length > 0 && value.every((v) => typeof v === "number" && Number.isInteger(v))) {
    let outOfRange = false;
    const parts = (value as number[]).map((index, i) => {
      const resolved = labelFor(index, choices);
      outOfRange = outOfRange || resolved.outOfRange;
      return `${ordinal(i + 1)}: ${resolved.label}`;
    });
    return { kind: "ranked", text: parts.join(" > "), parts, outOfRange };
  }
  return { kind: "unknown", text: "Unsupported choice shape" };
}
