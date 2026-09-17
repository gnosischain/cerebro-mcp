import { useEffect, useState } from "react";

import { EMPTY_DRAFT, POOL_SORTS, type PlxFilterDraft } from "../state/toolArgs";
import { FEE_BANDS, POOL_CLASSES } from "../types";
import { classLabel } from "../model/format";
import type { PlxViewContext } from "../sections/common";

// Server-side directory filters (the directory is server-paged, so nothing
// here filters client-side). Edits accumulate in the draft; Apply sends ONE
// section load. A fee band and an exact fee are mutually exclusive on the
// wire — picking one clears the other.

export function PoolFilterBar({ ctx }: { ctx: PlxViewContext }) {
  const { draft, setDraft } = ctx;
  const [asOf, setAsOf] = useState(ctx.state.as_of ?? "");
  useEffect(() => {
    setAsOf(ctx.state.as_of ?? "");
  }, [ctx.state.as_of]);
  const patch = (next: Partial<PlxFilterDraft>) => setDraft((current) => ({ ...current, ...next }));
  const apply = () => ctx.apply("pools", undefined, { asOf, window: ctx.state.window });
  const reset = () => {
    setDraft({ ...EMPTY_DRAFT });
    setAsOf("");
    ctx.apply("pools", { ...EMPTY_DRAFT }, { asOf: "", window: ctx.state.window });
  };
  const submitOnEnter = (event: React.KeyboardEvent) => {
    if (event.key === "Enter") apply();
  };
  return (
    <div className="plx-filterbar">
      <label>
        Search
        <input
          type="text"
          value={draft.query}
          placeholder="pool name or address prefix"
          onChange={(event) => patch({ query: event.target.value })}
          onKeyDown={submitOnEnter}
        />
      </label>
      <label>
        Class
        <select value={draft.pool_class} onChange={(event) => patch({ pool_class: event.target.value })}>
          <option value="">All</option>
          {POOL_CLASSES.map((cls) => <option key={cls} value={cls}>{classLabel(cls)}</option>)}
        </select>
      </label>
      <label>
        Family
        <select value={draft.pool_family} onChange={(event) => patch({ pool_family: event.target.value })}>
          <option value="">All</option>
          <option value="cl">Concentrated</option>
          <option value="reserves_only">Reserves only</option>
        </select>
      </label>
      <label>
        Fee band
        <select
          value={draft.fee_band}
          onChange={(event) => patch({ fee_band: event.target.value, fee: event.target.value ? 0 : draft.fee })}
        >
          <option value="">Any</option>
          {FEE_BANDS.map((band) => <option key={band.id} value={band.id}>{band.label}</option>)}
        </select>
      </label>
      <label>
        Fee (pips)
        <input
          type="number"
          min={0}
          step={1}
          value={draft.fee || ""}
          placeholder="3000"
          disabled={Boolean(draft.fee_band)}
          title={draft.fee_band ? "Clear the fee band to filter on an exact fee" : "Exact fee in pips (3000 = 0.30%)"}
          onChange={(event) => patch({ fee: Math.max(0, Math.floor(Number(event.target.value) || 0)) })}
          onKeyDown={submitOnEnter}
        />
      </label>
      <label>
        Token
        <input
          type="text"
          value={draft.token}
          placeholder="0x… (pool holds this token)"
          onChange={(event) => patch({ token: event.target.value })}
          onKeyDown={submitOnEnter}
        />
      </label>
      <label className="plx-filterbar__check">
        <input type="checkbox" checked={draft.live_only} onChange={(event) => patch({ live_only: event.target.checked })} />
        live only
      </label>
      <label className="plx-filterbar__check">
        <input type="checkbox" checked={draft.probed_only} onChange={(event) => patch({ probed_only: event.target.checked })} />
        probed only
      </label>
      <label>
        Sort
        <select value={draft.sort_by} onChange={(event) => patch({ sort_by: event.target.value })}>
          {POOL_SORTS.map((sort) => <option key={sort.id || "default"} value={sort.id}>{sort.label}</option>)}
        </select>
      </label>
      <label>
        As of
        <input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
      </label>
      <button type="button" className="plx-filterbar__apply" disabled={ctx.loading} onClick={apply}>Apply</button>
      <button type="button" onClick={reset}>Reset</button>
    </div>
  );
}
