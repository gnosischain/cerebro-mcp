import { useMemo, useState, type ReactNode } from "react";

import { SparkLine } from "../../../cow-explorer/components/SparkLine";
import { chainShortName } from "../../../shared/chainIcons";
import { TokenIdentity } from "../../../shared/TokenIdentity";
import { useDebouncedValue } from "../../../shared/useDebouncedValue";
import { chainName } from "../../model/treasuryChains";
import { SPAM_REASON_COPY, TOKEN_CLASS_COPY } from "../../model/treasuryCopy";
import { fmtCount, fmtPrice, fmtShare, fmtStamp, fmtUnits } from "../../model/treasuryFormat";
import { SPAM_REASONS, TOKEN_CLASSES, type HoldingRow } from "../../model/treasuryRows";
import type { AssetRow } from "../../model/treasuryValue";
import type { AssetFilter } from "../../state/treasuryView";
import { TokenClassBadge } from "./TokenClassBadge";
import { ValueCell } from "./ValueCell";

// The asset table: every token the treasury holds, one row per asset (merged
// across chains by the reviewed registry's asset key) or per chain.
//
// Rules it keeps:
//   * Identity is the ADDRESS. Registry assets show their trusted symbol; any
//     other row shows its sanitized on-chain symbol WITH its address, so a
//     look-alike can never pass as the real token.
//   * Spam rows exist in the DOM only when "Show hidden tokens" is on.
//   * No silent cap: 50 rows, then "Show all N".
//   * An unpriced value is the word "unpriced", never "$0".
//
// SparkLine is a plain SVG polyline on purpose — one chart runtime per row
// would be absurd.

export type AssetSort = "value" | "units" | "holders" | "name";

const SORTS: Array<{ key: AssetSort; label: string }> = [
  { key: "value", label: "Value" },
  { key: "units", label: "Units" },
  { key: "holders", label: "Holders" },
  { key: "name", label: "Name" },
];

export const DEFAULT_ASSET_LIMIT = 50;

const FILTER_LABELS: Record<AssetFilter, string> = {
  all: "All",
  hub: "Hub-priced",
  spot: "Spot",
  unpriced: "Unpriced",
  retired: "Retired",
  hidden: "Hidden",
};

function matchesFilter(row: AssetRow, filter: AssetFilter): boolean {
  switch (filter) {
    case "hub":
      return row.kind === "hub" || row.kind === "mixed";
    case "spot":
      return row.kind === "spot" || row.kind === "mixed";
    case "unpriced":
      return row.kind === "unpriced" || row.kind === "refused";
    case "retired":
      return row.kind === "retired";
    case "hidden":
      return row.hidden;
    default:
      return true;
  }
}

/** Rows a filter chip + search selects. Spam never passes unless shown. */
export function filterAssets(
  rows: AssetRow[],
  opts: { filter: AssetFilter; showHidden: boolean; query: string },
): AssetRow[] {
  const needle = opts.query.trim().toLowerCase();
  return rows.filter((row) => (
    (opts.showHidden || !row.hidden)
    && matchesFilter(row, opts.filter)
    && (!needle || row.search.includes(needle) || row.label.toLowerCase().includes(needle))
  ));
}

/** Count per chip, over the rows the table could show. */
export function filterCounts(rows: AssetRow[], showHidden: boolean): Record<AssetFilter, number> {
  const visible = rows.filter((row) => showHidden || !row.hidden);
  const count = (filter: AssetFilter) => visible.filter((row) => matchesFilter(row, filter)).length;
  return {
    all: visible.length,
    hub: count("hub"),
    spot: count("spot"),
    unpriced: count("unpriced"),
    retired: count("retired"),
    hidden: count("hidden"),
  };
}

function descNullsLast(a: number | null, b: number | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return b - a;
}

export function sortAssets(rows: AssetRow[], sort: AssetSort): AssetRow[] {
  return [...rows].sort((a, b) => {
    let primary = 0;
    if (sort === "value") primary = descNullsLast(a.usd, b.usd) || descNullsLast(a.units, b.units);
    else if (sort === "units") primary = descNullsLast(a.units, b.units);
    else if (sort === "holders") primary = descNullsLast(a.holders, b.holders);
    else {
      // Named rows alphabetically; unnamed (address-only) rows after them.
      if (a.label && !b.label) primary = -1;
      else if (!a.label && b.label) primary = 1;
      else primary = a.label.localeCompare(b.label);
    }
    return primary || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
  });
}

/** Classification summary: tokens per class and hidden tokens per reason —
 * counts only, never names. */
export function classification(holdings: HoldingRow[]): {
  byClass: Array<{ key: string; label: string; count: number; description: string }>;
  byReason: Array<{ key: string; label: string; count: number; description: string }>;
} {
  const byClass = TOKEN_CLASSES.map((cls) => ({
    key: cls,
    label: TOKEN_CLASS_COPY[cls].label,
    count: holdings.filter((holding) => holding.tokenClass === cls).length,
    description: TOKEN_CLASS_COPY[cls].description,
  }));
  const byReason = SPAM_REASONS.filter((reason) => reason !== "").map((reason) => ({
    key: reason,
    label: SPAM_REASON_COPY[reason as Exclude<typeof reason, "">].label,
    count: holdings.filter((holding) => holding.tokenClass === "spam" && holding.spamReason === reason).length,
    description: SPAM_REASON_COPY[reason as Exclude<typeof reason, "">].description,
  }));
  return { byClass, byReason };
}

export interface AssetTableProps {
  /** Rows merged across chains by asset key. */
  merged: AssetRow[];
  /** One row per (chain, token). */
  byChain: AssetRow[];
  /** Offer the "Merge chains" toggle (only meaningful with several chains). */
  mergeAvailable: boolean;
  filter: AssetFilter;
  onFilter: (filter: AssetFilter) => void;
  showHidden: boolean;
  /** Inline "Show hidden tokens" toggle (wallet page; the section has it in
   * the toolbar). */
  onToggleHidden?: (next: boolean) => void;
  hiddenCount: number;
  onOpen: (chainId: number, token: string) => void;
  iconFor: (chainId: number, token: string) => string;
  /** Sparkline values by `chain:token` / `asset:<key>`. */
  spark?: Map<string, number[]>;
  spotAt: string;
  /** Denominator of the Share column (the valued total in scope). */
  totalUsd: number | null;
  /** "wallet": one wallet's positions on one chain (no Chains column; the
   * last column is the wallet's share of the treasury's position). */
  mode?: "section" | "wallet";
  exportSlot?: ReactNode;
  limit?: number;
}

const SPARK_W = 72;
const SPARK_H = 20;

export function AssetTable({
  merged,
  byChain,
  mergeAvailable,
  filter,
  onFilter,
  showHidden,
  onToggleHidden,
  hiddenCount,
  onOpen,
  iconFor,
  spark,
  spotAt,
  totalUsd,
  mode = "section",
  exportSlot,
  limit = DEFAULT_ASSET_LIMIT,
}: AssetTableProps) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 200);
  const [sort, setSort] = useState<AssetSort>("value");
  const [merge, setMerge] = useState(true);
  const [showAll, setShowAll] = useState(false);

  const source = mergeAvailable && merge ? merged : byChain;
  const counts = useMemo(() => filterCounts(source, showHidden), [source, showHidden]);
  const rows = useMemo(
    () => sortAssets(filterAssets(source, { filter, showHidden, query: debounced }), sort),
    [source, filter, showHidden, debounced, sort],
  );
  const cap = Math.max(1, Math.floor(limit));
  const shown = showAll ? rows : rows.slice(0, cap);
  const chips: AssetFilter[] = showHidden
    ? ["all", "hub", "spot", "unpriced", "retired", "hidden"]
    : ["all", "hub", "spot", "unpriced", "retired"];
  const walletMode = mode === "wallet";

  return (
    <div className="gov-trs-assets">
      <div className="gov-trs-tablebar">
        <input
          className="gov-trs-search"
          type="search"
          value={query}
          placeholder="Search symbol, label or address"
          aria-label="Search assets"
          onChange={(event) => {
            setQuery(event.target.value);
            setShowAll(false);
          }}
        />
        <div className="gov-panel-modes" role="group" aria-label="Filter by class">
          {chips.map((chip) => (
            <button
              key={chip}
              type="button"
              className={chip === filter ? "is-active" : undefined}
              aria-pressed={chip === filter}
              onClick={() => onFilter(chip)}
            >
              {FILTER_LABELS[chip]} ({fmtCount(counts[chip])})
            </button>
          ))}
        </div>
        <div className="gov-panel-modes" role="group" aria-label="Sort assets by">
          <span className="gov-caption">Sort</span>
          {SORTS.map((option) => (
            <button
              key={option.key}
              type="button"
              className={option.key === sort ? "is-active" : undefined}
              aria-pressed={option.key === sort}
              onClick={() => setSort(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
        {mergeAvailable ? (
          <label className="gov-trs-toggle" title="Show GNO on Ethereum and GNO on Gnosis Chain as one asset (reviewed registry assets only).">
            <input type="checkbox" checked={merge} onChange={(event) => setMerge(event.target.checked)} />
            Merge chains
          </label>
        ) : null}
        {onToggleHidden ? (
          <label className="gov-trs-toggle">
            <input type="checkbox" checked={showHidden} onChange={(event) => onToggleHidden(event.target.checked)} />
            Show hidden tokens ({fmtCount(hiddenCount)})
          </label>
        ) : null}
        {exportSlot ? <span className="gov-trs-tablebar__end">{exportSlot}</span> : null}
      </div>

      {rows.length === 0 ? (
        <div className="gov-empty">
          {debounced ? "No asset matches this search." : "No assets in this selection."}
          {!showHidden && hiddenCount > 0 ? ` ${fmtCount(hiddenCount)} hidden token${hiddenCount === 1 ? "" : "s"} not shown.` : ""}
        </div>
      ) : (
        <div className="gov-trs-tablewrap">
          <table className={walletMode ? "gov-trs-table gov-trs-table--assets gov-trs-table--wallet" : "gov-trs-table gov-trs-table--assets"}>
            <thead>
              <tr>
                <th scope="col">Asset</th>
                {walletMode ? null : <th scope="col" className="gov-trs-col-chains">Chains</th>}
                <th scope="col" className="gov-trs-num">Value</th>
                <th scope="col" className="gov-trs-num gov-trs-col-share">Share</th>
                <th scope="col" className="gov-trs-num gov-trs-col-units">Units</th>
                <th scope="col" className="gov-trs-num gov-trs-col-price">Price</th>
                <th scope="col" className="gov-trs-num gov-trs-col-holders">
                  {walletMode ? "Of treasury" : "Holders"}
                </th>
                <th scope="col" className="gov-trs-col-spark">24 months</th>
                <th scope="col" className="gov-trs-col-chev"><span className="gov-trs-sr">Open</span></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <AssetTableRow
                  key={row.key}
                  row={row}
                  walletMode={walletMode}
                  onOpen={onOpen}
                  iconFor={iconFor}
                  spark={spark}
                  spotAt={spotAt}
                  totalUsd={totalUsd}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="gov-caption gov-trs-tablefoot">
        {rows.length > shown.length ? (
          <>
            Showing {fmtCount(shown.length)} of {fmtCount(rows.length)} assets.{" "}
            <button type="button" className="gov-trs-linkbtn" onClick={() => setShowAll(true)}>
              Show all {fmtCount(rows.length)}
            </button>
          </>
        ) : (
          <>All {fmtCount(rows.length)} asset{rows.length === 1 ? "" : "s"} shown.</>
        )}
        {!showHidden && hiddenCount > 0 ? (
          <> {fmtCount(hiddenCount)} hidden token{hiddenCount === 1 ? " is" : "s are"} not listed (spam, never valued).</>
        ) : null}
      </p>
    </div>
  );
}

function AssetTableRow({
  row,
  walletMode,
  onOpen,
  iconFor,
  spark,
  spotAt,
  totalUsd,
}: {
  row: AssetRow;
  walletMode: boolean;
  onOpen: (chainId: number, token: string) => void;
  iconFor: (chainId: number, token: string) => string;
  spark?: Map<string, number[]>;
  spotAt: string;
  totalUsd: number | null;
}) {
  const lead = row.members[0].holding;
  const open = () => onOpen(lead.chainId, lead.token);
  const share = row.usd !== null && totalUsd !== null && totalUsd > 0
    ? Math.min(1, Math.max(0, row.usd / totalUsd))
    : null;
  const sparkValues = spark?.get(row.key.startsWith("asset:") ? row.key : `${lead.chainId}:${lead.token}`) ?? [];
  const priceDate = row.kind === "spot" ? fmtStamp(spotAt).slice(0, 10) : row.priceDate;
  const lastShare = walletMode ? lead.treasuryShare : null;
  return (
    <tr className={row.hidden ? "gov-trs-row is-hidden" : "gov-trs-row"} onClick={open}>
      <td className="gov-trs-cell-asset">
        <button
          type="button"
          className="gov-trs-asset"
          onClick={(event) => {
            event.stopPropagation();
            open();
          }}
          title={row.trusted ? `${row.label} — ${lead.token}` : `Unverified label — identity is the address ${lead.token}`}
        >
          <TokenIdentity
            address={lead.token}
            iconUrl={iconFor(lead.chainId, lead.token)}
            symbol={row.label}
            // Untrusted symbols always carry their address.
            ambiguous={!row.trusted}
          />
        </button>
        {row.tokenClass !== "priced" ? (
          <TokenClassBadge tokenClass={row.tokenClass} spamReason={row.spamReason} compact />
        ) : null}
      </td>
      {walletMode ? null : (
        <td className="gov-trs-col-chains">
          <span className="gov-trs-chainchips">
            {row.members.map((member) => (
              <button
                key={member.holding.key}
                type="button"
                className="gov-trs-chainchip"
                title={`Open ${row.label || "this token"} on ${chainName(member.holding.chainId)}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onOpen(member.holding.chainId, member.holding.token);
                }}
              >
                {chainShortName(member.holding.chainId)}
              </button>
            ))}
          </span>
        </td>
      )}
      <td className="gov-trs-num">
        <ValueCell kind={row.kind} usd={row.usd} hubUsd={row.hubUsd} spotUsd={row.spotUsd} spotAt={spotAt} proxy={row.proxy} />
      </td>
      <td className="gov-trs-num gov-trs-col-share">
        {share === null ? (
          <span className="gov-trs-value--none">—</span>
        ) : (
          <span className="gov-trs-share" title={`${fmtShare(share)} of the valued total in view`}>
            <span className="gov-trs-share__bar" aria-hidden="true"><span style={{ width: `${share * 100}%` }} /></span>
            {fmtShare(share)}
          </span>
        )}
      </td>
      <td className="gov-trs-num gov-trs-col-units">
        {row.units === null ? (
          <span
            className="gov-trs-value--none"
            title={lead.balanceRaw
              ? `Token decimals were never observed, so units cannot be derived. Raw on-chain integer: ${lead.balanceRaw}`
              : "No balance recorded"}
          >
            {row.members.length === 1 && lead.balanceRaw ? "raw" : "—"}
          </span>
        ) : (
          <span className="gov-trs-mono">{fmtUnits(row.units)}</span>
        )}
      </td>
      <td className="gov-trs-num gov-trs-col-price">
        {row.priceUsd === null || row.kind === "hidden" ? (
          <span className="gov-trs-value--none">—</span>
        ) : (
          <span className="gov-trs-price">
            <span className="gov-trs-mono">{fmtPrice(row.priceUsd)}</span>
            {priceDate ? <small>{row.kind === "spot" ? `spot ${priceDate}` : priceDate}</small> : null}
          </span>
        )}
      </td>
      <td
        className="gov-trs-num gov-trs-col-holders"
        title={walletMode
          ? "This wallet's share of the treasury's whole position in the token"
          : row.members.length > 1
            ? "Wallet-chain positions holding it — an address holding it on both chains counts twice"
            : "Treasury wallets holding it"}
      >
        {walletMode ? fmtShare(lastShare) : fmtCount(row.holders)}
      </td>
      <td className="gov-trs-col-spark">
        {sparkValues.length > 1 ? (
          <span className="gov-trs-spark" title="USD value at each month-end (gaps left blank)">
            <SparkLine values={sparkValues} width={SPARK_W} height={SPARK_H} />
          </span>
        ) : null}
      </td>
      <td className="gov-trs-col-chev" aria-hidden="true">›</td>
    </tr>
  );
}
