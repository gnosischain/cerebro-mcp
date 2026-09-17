import { useEffect, useRef, useState, type ReactNode } from "react";

import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import { DATASET_DOCS } from "../model/datasetDocs";

// Single-open coordination: opening one popover closes any other. A module
// listener set keeps this dependency-free (CoW copy with .plx- classes).
const closers = new Set<() => void>();

export function InfoPopover({ label = "About this data", children }: { label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    closers.add(close);
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      closers.delete(close);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="plx-info" ref={rootRef} data-open={open ? "true" : undefined}>
      <button
        type="button"
        className="plx-info__summary"
        aria-expanded={open}
        aria-label={label}
        onClick={() => {
          if (!open) {
            for (const close of [...closers]) close();
            setOpen(true);
          } else {
            setOpen(false);
          }
        }}
      >
        ⓘ <span>{label}</span>
      </button>
      {open && <div className="plx-info__panel">{children}</div>}
    </div>
  );
}

/** Standard three-block info body: What this is · How it's computed · Coverage. */
export function InfoBlocks({ what, method, coverage }: { what?: string; method?: string; coverage?: string }) {
  return (
    <div className="plx-info-blocks">
      {what && (
        <div>
          <strong>What this is</strong>
          <p>{what}</p>
        </div>
      )}
      {method && (
        <div>
          <strong>How it&apos;s computed</strong>
          <p>{method}</p>
        </div>
      )}
      {coverage && (
        <div>
          <strong>Coverage</strong>
          <p className="plx-info-blocks__coverage">{coverage}</p>
        </div>
      )}
    </div>
  );
}

export function coverageMeta(descriptor?: DatasetDescriptor): string {
  const coverage = descriptor?.provenance?.coverage as
    | { actual_start?: string | null; actual_end?: string | null; mode?: string; truncated?: boolean; basis?: string }
    | undefined;
  if (!coverage) return "Publication window disclosed in source metadata";
  const range = [coverage.actual_start, coverage.actual_end].filter(Boolean).join(" → ");
  return [
    coverage.mode,
    range,
    coverage.basis ?? "",
    coverage.truncated ? "result truncated" : "",
  ].filter(Boolean).join(" · ") || "No rows at this publication";
}

/** The (i) for one dataset key: docs + the descriptor's coverage line. */
export function DatasetInfo({ datasetKey, descriptor }: { datasetKey: string; descriptor?: DatasetDescriptor }) {
  const doc = DATASET_DOCS[datasetKey];
  return (
    <InfoPopover>
      <InfoBlocks what={doc?.what} method={doc?.method} coverage={coverageMeta(descriptor)} />
    </InfoPopover>
  );
}
