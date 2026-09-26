// Profile as-of date: presets + a bounded <input type=date>. Bounded by the
// pool's first served day with ticks (below it there is no profile) and its
// latest publication. Changes are DEBOUNCED 400 ms into ONE additive
// `pool.profile` group load (PROFILE-DATE-HOOK); the server may shift the
// request to the nearest earlier complete served day (`as_of_shifted`) — the
// applied date comes back in the dataset rows and is shown as a clamped chip.

import { useEffect, useRef, useState } from "react";

import { fmtDate } from "../model/format";

export const PROFILE_DATE_DEBOUNCE_MS = 400;

export interface ProfileDatePickerProps {
  /** Applied profile date (from the pool_profile_at rows); "" while loading. */
  applied: string;
  /** Requested as_of from state ("" = latest). */
  requested: string;
  /** Latest publication for this pool (upper bound). */
  latest: string | null;
  /** First probed publication (lower bound); null = unknown. */
  earliest: string | null;
  disabled?: boolean;
  onChange: (date: string) => void;
}

function shiftDays(date: string, days: number): string {
  const base = new Date(`${date}T00:00:00Z`);
  if (!Number.isFinite(base.getTime())) return date;
  base.setUTCDate(base.getUTCDate() + days);
  return base.toISOString().slice(0, 10);
}

function clamp(date: string, earliest: string | null, latest: string | null): string {
  let out = date;
  if (earliest && out < earliest) out = earliest;
  if (latest && out > latest) out = latest;
  return out;
}

const PRESETS: Array<{ label: string; days: number }> = [
  { label: "−7d", days: 7 },
  { label: "−30d", days: 30 },
  { label: "−90d", days: 90 },
  { label: "−1y", days: 365 },
];

export function ProfileDatePicker(props: ProfileDatePickerProps) {
  const { applied, requested, latest, earliest, disabled } = props;
  const [draft, setDraft] = useState(requested);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    setDraft(requested);
  }, [requested]);
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  const request = (date: string) => {
    setDraft(date);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      props.onChange(date);
    }, PROFILE_DATE_DEBOUNCE_MS);
  };

  const shifted = Boolean(requested && applied && requested !== applied);
  const anchor = latest ?? applied ?? "";

  return (
    <div className="plx-datepicker" role="group" aria-label="Profile as-of date">
      <span className="plx-datepicker__label">As of</span>
      <div className="plx-datepicker__presets">
        <button
          type="button"
          className={!draft ? "is-active" : ""}
          disabled={disabled}
          onClick={() => request("")}
        >
          Latest
        </button>
        {PRESETS.map((preset) => {
          const target = anchor ? clamp(shiftDays(anchor, -preset.days), earliest, latest) : "";
          const reachable = Boolean(target) && (!earliest || target >= earliest);
          return (
            <button
              key={preset.label}
              type="button"
              className={draft && draft === target ? "is-active" : ""}
              disabled={disabled || !reachable}
              title={reachable ? target : "Before the first probed publication"}
              onClick={() => request(target)}
            >
              {preset.label}
            </button>
          );
        })}
      </div>
      <input
        type="date"
        aria-label="Profile date"
        value={draft}
        min={earliest ?? undefined}
        max={latest ?? undefined}
        disabled={disabled}
        onChange={(event) => {
          const value = event.target.value;
          if (!value) {
            request("");
            return;
          }
          request(clamp(value, earliest, latest));
        }}
      />
      {applied && (
        <span className={`plx-chip${shifted ? " plx-chip--warn" : ""}`} title={shifted ? `Requested ${requested}; the nearest earlier complete served day is ${applied}` : "Applied snapshot date"}>
          {shifted ? `shifted to ${fmtDate(applied)}` : fmtDate(applied)}
        </span>
      )}
      {earliest && (
        <span className="plx-datepicker__bounds">profile since {fmtDate(earliest)}</span>
      )}
    </div>
  );
}
