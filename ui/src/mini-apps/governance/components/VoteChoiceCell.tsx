import { renderVoteChoice } from "../model/choices";

function asIndex(value: unknown): number | null {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const n = Number(value);
  return Number.isInteger(n) ? n : null;
}

/** One vote's choice from the backend's structured columns
 * (`choice_kind` + `choice_index` / `choice_indexes`, optional server-resolved
 * `choice_label`) against the proposal's choice list: 1-based single label,
 * ordered ranked preferences, or a flagged unsupported shape (never guessed).
 * With no choice list available (voter vote history), falls back to raw
 * index rendering. */
export function VoteChoiceCell({ kind, index, indexes, label, choices }: {
  kind: unknown;
  index?: unknown;
  indexes?: unknown;
  label?: unknown;
  choices: string[];
}) {
  // Server-resolved label (voter_votes single case) wins outright.
  if (typeof label === "string" && label !== "") {
    return <span title={label}>{label}</span>;
  }
  if (kind === "single") {
    const n = asIndex(index);
    if (n !== null) {
      if (choices.length === 0) {
        return <span title={`Choice index ${n} (labels unavailable)`}>Choice {n}</span>;
      }
      const rendered = renderVoteChoice(n, choices);
      return (
        <span>
          {rendered.text}
          {rendered.outOfRange && <span className="gov-choice-flag"> (out of range)</span>}
        </span>
      );
    }
  }
  if (kind === "ranked") {
    const list = Array.isArray(indexes) ? indexes.map(asIndex) : [];
    if (list.length > 0 && list.every((n) => n !== null)) {
      if (choices.length === 0) {
        const text = list.join(" > ");
        return <span title="Ranked preference indexes (labels unavailable)">{text}</span>;
      }
      const rendered = renderVoteChoice(list, choices);
      return (
        <span title={rendered.text}>
          {rendered.text}
          {rendered.outOfRange && <span className="gov-choice-flag"> (index out of range)</span>}
        </span>
      );
    }
  }
  return <span className="gov-choice-flag" title={String(kind ?? "")}>Unsupported choice shape</span>;
}
