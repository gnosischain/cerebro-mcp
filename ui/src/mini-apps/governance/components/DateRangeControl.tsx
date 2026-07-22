import { SegmentedControl } from "../../shared/SegmentedControl";
import type { GovFilterDraft } from "../state/toolArgs";

// Shared 90d / 1y / All / Custom date control writing the draft's frozen
// `days` semantics (90 | 365 presets, 0 all-history, null = custom ISO pair).
// The section's Apply button sends the draft; this control only edits it.

type PresetId = "90" | "365" | "all" | "custom";

function presetOf(draft: GovFilterDraft): PresetId {
  if (draft.days === 90) return "90";
  if (draft.days === 365) return "365";
  if (draft.days === null) return "custom";
  return "all";
}

/** datetime-local value ("YYYY-MM-DDTHH:MM") → frozen ISO UTC token. */
function toIso(value: string): string {
  if (!value) return "";
  return `${value.length === 16 ? `${value}:00` : value}Z`;
}

function fromIso(value: string): string {
  return value ? value.replace("Z", "").slice(0, 16) : "";
}

export function DateRangeControl({ draft, onChange }: {
  draft: GovFilterDraft;
  onChange: (next: GovFilterDraft) => void;
}) {
  const preset = presetOf(draft);
  return (
    <div className="gov-daterange">
      <SegmentedControl<PresetId>
        ariaLabel="Date range"
        size="sm"
        value={preset}
        options={[
          { value: "90", label: "90d", ariaLabel: "Last 90 days" },
          { value: "365", label: "1y", ariaLabel: "Last year" },
          { value: "all", label: "All", ariaLabel: "All history" },
          { value: "custom", label: "Custom", ariaLabel: "Custom UTC range" },
        ]}
        onChange={(next) => {
          if (next === "90") onChange({ ...draft, days: 90, start: "", end: "" });
          else if (next === "365") onChange({ ...draft, days: 365, start: "", end: "" });
          else if (next === "all") onChange({ ...draft, days: 0, start: "", end: "" });
          else onChange({ ...draft, days: null });
        }}
      />
      {preset === "custom" && (
        <>
          <label>
            Start UTC
            <input
              type="datetime-local"
              value={fromIso(draft.start)}
              onChange={(event) => onChange({ ...draft, days: null, start: toIso(event.target.value) })}
            />
          </label>
          <label>
            End UTC
            <input
              type="datetime-local"
              value={fromIso(draft.end)}
              onChange={(event) => onChange({ ...draft, days: null, end: toIso(event.target.value) })}
            />
          </label>
        </>
      )}
    </div>
  );
}
