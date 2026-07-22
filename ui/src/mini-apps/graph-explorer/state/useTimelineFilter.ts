// Client-local timeline playback: cursor / playing / speed live HERE and are
// NEVER synced to the server (playback would round-trip per step and thrash
// the sync echo-suppression). The interval index memoizes on the hydrated
// timeline rows + model + axis, gated on hydration completion so playback
// can't fight the geometric hydration publishes.

import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphModel } from "../model/parseRows";
import {
  buildBucketAxis,
  buildTimelineIndex,
  computeFrame,
  type TimelineFrame,
  type TimelineIndex,
} from "../model/timelineIndex";
import type { TimelineState } from "../types";

const STEP_MS = 400;

export interface TimelineFilter {
  axis: string[];
  index: TimelineIndex | null;
  frame: TimelineFrame | null;
  cursor: number;
  maxCursor: number;
  playing: boolean;
  speed: number;
  showStatic: boolean;
  /** "2026-06-15 → 2026-07-13" text for the current window. */
  windowLabel: string;
  setCursor: (c: number) => void;
  togglePlay: () => void;
  setSpeed: (s: number) => void;
  setShowStatic: (v: boolean) => void;
}

export function useTimelineFilter(
  timeline: TimelineState | undefined,
  rows: unknown[][] | undefined,
  model: GraphModel,
  hydrating: boolean,
  windowBuckets: number,
  /** Deep-linked cursor (?tcur=) — applied to the FIRST axis only; later
   * loads/grain changes reset to the latest window. */
  initialCursor?: number,
): TimelineFilter {
  const axis = useMemo(
    () =>
      buildBucketAxis(
        timeline?.range_start ?? "",
        timeline?.range_end ?? "",
        timeline?.grain ?? "week",
      ),
    [timeline?.range_start, timeline?.range_end, timeline?.grain],
  );

  const index = useMemo(() => {
    if (hydrating || !rows?.length || !model.n || !axis.length) return null;
    return buildTimelineIndex(
      rows,
      model,
      axis,
      timeline?.profile_shapes ?? {},
    );
  }, [rows, model, axis, hydrating, timeline?.profile_shapes]);

  const win = Math.max(1, windowBuckets);
  const maxCursor = Math.max(0, axis.length - win);
  const [cursor, setCursorRaw] = useState(() =>
    initialCursor != null && initialCursor >= 0 && initialCursor <= maxCursor
      ? initialCursor
      : maxCursor,
  );
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [showStatic, setShowStatic] = useState(true);

  // A new axis (fresh load / grain change) resets the cursor to the latest
  // window — "now" is the natural starting point.
  const axisKey = `${axis[0] ?? ""}|${axis.length}|${win}`;
  const prevAxisKey = useRef(axisKey);
  const consumedInitial = useRef(axis.length > 0);
  useEffect(() => {
    if (prevAxisKey.current !== axisKey) {
      prevAxisKey.current = axisKey;
      const useInitial =
        !consumedInitial.current &&
        initialCursor != null &&
        initialCursor >= 0 &&
        initialCursor <= maxCursor;
      consumedInitial.current = true;
      setCursorRaw(useInitial ? initialCursor : maxCursor);
      setPlaying(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [axisKey, maxCursor]);

  const setCursor = (c: number) =>
    setCursorRaw(Math.max(0, Math.min(maxCursor, Math.floor(c))));

  // Interval-stepped playback (bucket stepping is discrete — no rAF needed).
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setCursorRaw((c) => {
        if (c >= maxCursor) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, STEP_MS / Math.max(0.25, speed));
    return () => window.clearInterval(timer);
  }, [playing, speed, maxCursor]);

  const frame = useMemo(() => {
    if (!index || !model.n) return null;
    return computeFrame(index, model, cursor, win, { showStatic });
  }, [index, model, cursor, win, showStatic]);

  const windowLabel = axis.length
    ? `${axis[Math.min(cursor, axis.length - 1)]} → ${
        axis[Math.min(cursor + win - 1, axis.length - 1)]
      }`
    : "";

  return {
    axis,
    index,
    frame,
    cursor,
    maxCursor,
    playing,
    speed,
    showStatic,
    windowLabel,
    setCursor,
    togglePlay: () => setPlaying((wasPlaying) => {
      if (!wasPlaying && cursor >= maxCursor && maxCursor > 0) {
        setCursorRaw(0);
      }
      return !wasPlaying;
    }),
    setSpeed,
    setShowStatic,
  };
}
