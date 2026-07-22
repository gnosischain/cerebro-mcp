import { MaIdentity, MaKpi, MaKpiGrid, MaSection } from "../../shared/MiniAppChrome";
import type { DatasetDescriptor, PageRowsResponse } from "../../shared/miniAppTypes";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import type { CowExplorerViewState, EntityType } from "../types";
import { ChartCard } from "../../../components/ChartCard";
import { CuratedTable } from "../components/CuratedTable";
import { InfoBlocks, InfoPopover } from "../components/InfoPopover";
import { KeyValueGrid } from "../components/KeyValueGrid";
import { buildTransactionExecutionGraph } from "../model/executionGraph";
import { transactionExecutionGraphOption } from "../model/chartOptions";
import { datasetError } from "../model/datasetGroups";
import { DATASET_DOCS } from "../model/datasetDocs";

type FetchRows = (viewId: string, datasetKey: string, pageToken?: string) => Promise<PageRowsResponse | null>;

interface Props {
  state: CowExplorerViewState;
  descriptors: Record<string, DatasetDescriptor>;
  hydrated?: Record<string, HydratedDataset>;
  viewId: string;
  fetchRows: FetchRows;
  onBack: () => void;
  onEntity: (entityType: EntityType, identifier: string, chainId?: number) => void;
  openExternal: (url: string) => void;
}

export function buildExternalExplorerUrl(state: CowExplorerViewState): string {
  const entity = state.selected_entity;
  const explorer = state.explorer;
  if (!entity || !explorer) return "";
  const encoded = encodeURIComponent(entity.identifier);
  if (entity.entity_type === "transaction") return explorer.transaction_url_template.replace("{hash}", encoded);
  if (entity.entity_type === "token") return explorer.token_url_template.replace("{address}", encoded);
  if (["address", "solver"].includes(entity.entity_type)) return explorer.address_url_template.replace("{address}", encoded);
  return "";
}

//: The single-row "header" dataset per entity type (rendered as KeyValueGrid)
export const ENTITY_HEADER: Record<EntityType, string> = {
  order: "order_detail",
  transaction: "transaction_detail",
  address: "address_summary",
  token: "token_detail",
  auction: "auction_detail",
  solver: "solver_summary",
};

const IMBALANCE_DISCLOSURE =
  "Order-level, trade-implied accounting over the last 30 indexed days: per settlement, net token flow between traders and the settlement contract, valued at the auction's clearing prices (native_wei = atoms × price / 1e18). It shows what the solver had to source externally (negative) or what accrued (positive). AMM leg amounts, plain ERC20 transfers, and buffer balances are NOT indexed in cow_db, so this is a behavioral signal — not audited buffer books.";

const SCORE_GAP_DISCLOSURE =
  "Winning solution score vs this solver's protocol reference score, per auction won. reference_score is a JSON map keyed by solver address; rows where either value fails to parse are flagged scores_parsed=false rather than dropped. Multi-winner combinatorial auctions are live — a winner with ranking≠1 is expected, not a violation.";

//: Ordered list datasets per entity type. Only these keys render — the view
//: retains other sections' datasets too, so an uncurated dump would leak them.
export const ENTITY_LAYOUT: Record<EntityType, Array<{ key: string; title: string; note?: string }>> = {
  order: [
    { key: "order_quality", title: "Execution quality vs limit", note: "Realized surplus in basis points against the order's limit price — positive means better than limit for either order kind. Fill ratio and creation-to-first-fill latency come from indexed data only." },
    { key: "order_trades", title: "Settled fills" },
    { key: "order_events", title: "Observed lifecycle events" },
    { key: "order_fees", title: "Fee-policy amounts" },
    { key: "order_app_data", title: "App data" },
  ],
  transaction: [
    { key: "transaction_trades", title: "Settled fills" },
    { key: "transaction_interactions", title: "Settlement interactions" },
    { key: "transaction_competition", title: "Competition mapping" },
  ],
  address: [
    { key: "address_trades", title: "Owned settled fills" },
    { key: "address_orders", title: "Owned orders" },
    { key: "address_solver_activity", title: "Solver and executor roles" },
  ],
  token: [
    { key: "token_pairs", title: "Indexed execution pairs" },
    { key: "token_execution_prices", title: "Execution prices (settled fills)" },
    { key: "token_native_prices", title: "Native-price API observations" },
  ],
  auction: [
    { key: "auction_solutions", title: "Competition solutions" },
    { key: "auction_orders", title: "Auction orders" },
    { key: "auction_prices", title: "Auction price vector" },
    { key: "auction_transactions", title: "Settlement transactions" },
  ],
  solver: [
    { key: "solver_imbalance_tokens", title: "Token imbalance — order-level, trade-implied (30d)", note: IMBALANCE_DISCLOSURE },
    { key: "solver_imbalance_settlements", title: "Settlement imbalance — order-level, trade-implied (30d)", note: IMBALANCE_DISCLOSURE },
    { key: "solver_score_gap", title: "Winning vs reference score (where parseable)", note: SCORE_GAP_DISCLOSURE },
    { key: "solver_competitions", title: "Competition entries" },
    { key: "solver_solutions", title: "Ranking distribution" },
    { key: "solver_settlements", title: "Settlement executor transactions" },
  ],
};

function entityCoverageMeta(descriptor?: DatasetDescriptor): string {
  const coverage = descriptor?.provenance?.coverage as
    | { actual_start?: string | null; actual_end?: string | null; mode?: string; fetched_at?: string | null; truncated?: boolean }
    | undefined;
  if (!coverage) return "Indexed window disclosed in source metadata";
  const range = [coverage.actual_start, coverage.actual_end].filter(Boolean).join(" → ");
  return [coverage.mode, range, coverage.fetched_at ? `fetched ${coverage.fetched_at}` : "", coverage.truncated ? "result truncated" : ""].filter(Boolean).join(" · ") || "No matching rows in indexed window";
}

/** First preview row of a (single-row) summary descriptor as an object. */
function firstRowObject(descriptor?: DatasetDescriptor): Record<string, unknown> | null {
  if (!descriptor || !descriptor.preview_rows.length) return null;
  const row = descriptor.preview_rows[0];
  return Object.fromEntries(descriptor.columns.map((column, index) => [column.name, row[index]]));
}

export function EntityDetail(props: Props) {
  const entity = props.state.selected_entity;
  if (!entity) return <div className="cow-empty">No entity selected.</div>;
  const url = buildExternalExplorerUrl(props.state);
  const graphDatasets = Object.fromEntries(
    ["transaction_detail", "transaction_trades", "transaction_interactions", "transaction_competition"].map((key) => {
      const hydrated = props.hydrated?.[key];
      const descriptor = props.descriptors[key];
      return [key, hydrated
        ? { columns: hydrated.columns, rows: hydrated.rows }
        : descriptor
          ? { columns: descriptor.columns.map((column) => column.name), rows: descriptor.preview_rows }
          : undefined];
    }),
  );
  const executionGraph = entity.entity_type === "transaction"
    ? buildTransactionExecutionGraph(graphDatasets, entity.identifier)
    : { nodes: [], edges: [] };
  const headerDescriptor = props.descriptors[ENTITY_HEADER[entity.entity_type]];
  const layout = ENTITY_LAYOUT[entity.entity_type] ?? [];
  // Dashboard KPI header for solver/address entities: the summary datasets
  // are single-row — surface their headline numbers as KPI cards instead of
  // burying them inside a key-value table.
  const summaryRow = firstRowObject(headerDescriptor);
  const pct = (value: unknown) =>
    Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(1)}%` : "—";
  const num = (value: unknown) =>
    Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "—";
  const kpiItems: Array<{ label: string; value: string }> =
    entity.entity_type === "solver" && summaryRow
      ? [
          { label: "Competitions", value: num(summaryRow.competitions) },
          { label: "Wins", value: num(summaryRow.wins) },
          { label: "Win rate", value: pct(summaryRow.win_rate) },
          { label: "Executed settlements", value: num(summaryRow.executed_settlements) },
          { label: "Multi-winner share", value: pct(summaryRow.multi_winner_share) },
        ]
      : entity.entity_type === "address" && summaryRow
        ? [
            { label: "Owned fills", value: num(summaryRow.owned_fills) },
            { label: "Owned orders", value: num(summaryRow.owned_orders) },
            { label: "Executed settlements", value: num(summaryRow.executed_settlements) },
            { label: "Submitted solutions", value: num(summaryRow.submitted_solutions) },
          ]
        : [];
  return (
    <>
      <div className="cow-breadcrumbs">
        <button type="button" onClick={props.onBack}>← Back to explorer</button>
        <span>{entity.chain_name}</span>
        {props.state.breadcrumbs.map((crumb, index) => (
          <span key={`${crumb.chain_id}-${crumb.entity_type}-${crumb.identifier}`} className="cow-breadcrumb-item">
            <span>/</span>
            {index === props.state.breadcrumbs.length - 1 ? (
              <span>{crumb.label}</span>
            ) : (
              <button type="button" onClick={() => props.onEntity(crumb.entity_type, crumb.identifier, crumb.chain_id)}>{crumb.label}</button>
            )}
          </span>
        ))}
      </div>
      <MaIdentity
        label={`${entity.entity_type.toUpperCase()} · ${entity.chain_name}`}
        value={entity.identifier}
        onCopy={() => void navigator.clipboard?.writeText(entity.identifier)}
        rightSlot={url ? (
          <a
            className="cow-external"
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => { event.preventDefault(); props.openExternal(url); }}
          >
            Open in {props.state.explorer?.brand}
          </a>
        ) : undefined}
      />
      <div className="cow-entity-grid">
        {headerDescriptor && (
          <MaSection
            title={headerDescriptor.title}
            meta={<InfoPopover label="Coverage">All indexed entity evidence available to cow_db for this chain. Fetch and source windows are retained in the dataset metadata.</InfoPopover>}
          >
            <KeyValueGrid descriptor={headerDescriptor} state={props.state} />
          </MaSection>
        )}
        {kpiItems.length > 0 && (
          <MaKpiGrid>
            {kpiItems.map((item) => <MaKpi key={item.label} label={item.label} value={item.value} />)}
          </MaKpiGrid>
        )}
        {executionGraph.nodes.length > 0 && (
          <MaSection
            title="Indexed execution evidence"
            meta={<InfoPopover label="Graph methodology">The graph is composed only from indexed fills, settlement events, interaction events, and explicit competition mappings. Interaction targets are not inferred liquidity routes or call traces. Dashed competition edges are auction-scoped; competition identities are never merged with the settlement executor.</InfoPopover>}
          >
            <ChartCard
              renderer="svg"
              chartId="cow-transaction-execution"
              hideId
              spec={transactionExecutionGraphOption(executionGraph)}
              onEvents={{
                click: (params) => {
                  const data = (params as { data?: { entityType?: EntityType; identifier?: string } }).data;
                  if (data?.entityType && data.identifier) props.onEntity(data.entityType, data.identifier, entity.chain_id);
                },
              }}
            />
          </MaSection>
        )}
        {layout.map(({ key, title, note }) => {
          const descriptor = props.descriptors[key];
          if (!descriptor) return null;
          const error = datasetError(descriptor);
          if (error) {
            return (
              <MaSection key={key} title={title}>
                <div className="cow-dataset-error" role="alert">
                  <div className="cow-dataset-error__msg">
                    <strong>This dataset failed to load.</strong>
                    <span>{error}</span>
                  </div>
                </div>
              </MaSection>
            );
          }
          return (
            <MaSection key={key} title={title} meta={<InfoPopover label="About this data"><InfoBlocks what={DATASET_DOCS[key]?.what} method={note ?? DATASET_DOCS[key]?.method} coverage={entityCoverageMeta(descriptor)} /></InfoPopover>}>
              <CuratedTable
                datasetKey={key}
                descriptor={descriptor}
                state={props.state}
                viewId={props.viewId}
                fetchRows={props.fetchRows}
                onEntity={(entityType, identifier, chainId) =>
                  props.onEntity(entityType, identifier, chainId ?? entity.chain_id)
                }
                maxHeight="440px"
              />
            </MaSection>
          );
        })}
      </div>
    </>
  );
}
