// Flows-mode token filter — compact popover over the server-computed
// token_catalog (top tokens by traced USD). Selection is a DATA operation:
// the parent refetches the trace with the narrowed token set. "All tokens"
// resets to [] (no filter). Mirrors the EdgeTypesMenu popover pattern.

import { useEffect, useRef, useState } from "react";
import type { FlowTokenEntry } from "./types";

interface Props {
  catalog: FlowTokenEntry[];
  /** Selected token addresses ([] = all tokens). */
  selected: string[];
  onChange: (next: string[]) => void;
}

function fmtUsd(v: number): string {
  if (!Number.isFinite(v)) return "—";
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

export function TokenMenu({ catalog, selected, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const selectedSet = new Set(selected);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = (address: string) => {
    const next = selectedSet.has(address)
      ? selected.filter((a) => a !== address)
      : [...selected, address];
    onChange(next);
  };

  return (
    <div className="ge-etypes" ref={wrapRef}>
      <button
        type="button"
        className={`ge-btn ge-etypes-btn ${open ? "active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="Narrow the trace to specific tokens"
      >
        Tokens
        <span className="ge-etypes-count">
          {selected.length ? `${selected.length}` : "all"}
        </span>
        <span aria-hidden>▾</span>
      </button>
      {open ? (
        <div className="ge-etypes-panel" role="group" aria-label="Token filter">
          <button
            type="button"
            className={`ge-chip ${selected.length === 0 ? "active" : ""}`}
            onClick={() => onChange([])}
            title="Trace all whitelisted tokens"
          >
            <span className="ge-chip-dot" aria-hidden />
            <span className="ge-chip-name">All tokens</span>
          </button>
          {!catalog.length ? (
            <div className="ge-token-empty">
              Run a trace first — tokens found on the graph appear here.
            </div>
          ) : (
            <div className="ge-token-list">
              {catalog.map((t) => (
                <button
                  key={t.token_address}
                  type="button"
                  className={`ge-chip ${selectedSet.has(t.token_address) ? "active" : ""}`}
                  onClick={() => toggle(t.token_address)}
                  title={`${t.token_address}\n${fmtUsd(t.amount_usd)} traced`}
                >
                  <span className="ge-chip-dot" aria-hidden />
                  <span className="ge-chip-name">{t.symbol || "?"}</span>
                  <span className="ge-token-usd">{fmtUsd(t.amount_usd)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
