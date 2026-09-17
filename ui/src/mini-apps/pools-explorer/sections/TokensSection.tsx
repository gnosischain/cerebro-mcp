import { DatasetPanel } from "../components/DatasetPanel";
import { DatasetInfo } from "../components/InfoPopover";
import { PlxTable } from "../components/PlxTable";
import { fmtInt } from "../model/format";
import { TOKEN_SORTS } from "../state/toolArgs";
import { GroupGate, type PlxViewContext } from "./common";

export function TokensSection({ ctx }: { ctx: PlxViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const descriptor = ctx.descriptors.token_directory;
  const total = descriptor?.stats?.source_rows ?? descriptor?.stats?.row_count ?? null;
  const apply = () => ctx.apply("tokens", undefined, { asOf: ctx.state.as_of, window: ctx.state.window });
  return (
    <>
      <div className="plx-filterbar">
        <label>
          Search
          <input
            type="text"
            value={ctx.draft.query}
            placeholder="symbol or address prefix"
            onChange={(event) => ctx.setDraft((current) => ({ ...current, query: event.target.value }))}
            onKeyDown={(event) => {
              if (event.key === "Enter") apply();
            }}
          />
        </label>
        <label>
          Sort
          <select
            value={ctx.draft.sort_by}
            onChange={(event) => ctx.setDraft((current) => ({ ...current, sort_by: event.target.value }))}
          >
            {TOKEN_SORTS.map((sort) => <option key={sort.id || "default"} value={sort.id}>{sort.label}</option>)}
          </select>
        </label>
        <button type="button" className="plx-filterbar__apply" disabled={ctx.loading} onClick={apply}>Apply</button>
      </div>
      <GroupGate ctx={ctx} section="tokens" group="core">
        <DatasetPanel
          title="Token directory"
          descriptor={descriptor}
          groupLoaded={groups["tokens.core"]}
          onRetry={() => ctx.retryGroup("tokens", "core")}
          emptyLabel="No token matches."
          meta={(
            <span className="plx-section-actions">
              <span className="plx-chip">{fmtInt(total)} tokens</span>
              <DatasetInfo datasetKey="token_directory" descriptor={descriptor} />
            </span>
          )}
        >
          <PlxTable
            datasetKey="token_directory"
            descriptor={descriptor}
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            onEntity={ctx.onEntity}
            overlay={ctx.overlay}
            onPageLoaded={ctx.onPageLoaded}
            maxHeight="640px"
          />
          <div className="plx-hint">
            Symbols are untrusted metadata (sanitized); a token with no readable name is shown by its address.
            A <span className="plx-chainmark">chain</span> marker means the symbol was read from current chain state over RPC, not from a verified indexer snapshot.
          </div>
        </DatasetPanel>
      </GroupGate>
    </>
  );
}
