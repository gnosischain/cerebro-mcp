// Kind-dispatched value renderers used by PlxTable. One implementation so
// token identity, raw-unit disclosure, price orientation and date formatting
// can never drift between tables.

import type { ReactNode } from "react";

import { shortAddr } from "../../../utils/format";
import type { CellKind } from "../model/columns";
import {
  classLabel, familyLabel, fmtAmountWithOverlay, fmtBool, fmtDate, fmtInt, fmtPct, fmtRaw,
  fmtTick, fmtTime, truthy,
} from "../model/format";
import { coerceStringArray } from "../model/parseRows";
import {
  overlayAmountTitle, resolveDecimals, type TokenOverlay,
} from "../model/tokenOverlay";
import type { PlxEntityType } from "../types";
import { ClassBadge } from "./ClassBadge";
import { FeeChip } from "./FeeChip";
import { LiquidityCell } from "./LiquidityCell";
import { PriceCell } from "./PriceCell";
import { ProbeBadge } from "./ProbeBadge";
import { TokenLabel } from "./TokenLabel";

/** Columns whose value is ALREADY decimals-adjusted (and NULL when either
 * side's decimals are unknown), so they must not carry the raw marker. */
const ADJUSTED_PRICE_COLUMNS = new Set(["price_of_token_in_counter"]);

export interface CellContext {
  columnIndex: Map<string, number>;
  onEntity?: (entityType: PlxEntityType, identifier: string) => void;
  /** Chain-state token metadata (view_state.token_overlay). Fills symbol /
   * decimals holes ONLY — an indexer value always wins — and everything it
   * supplies renders with the `chain` marker. */
  overlay?: TokenOverlay;
  /** The token entity this table belongs to, for datasets whose rows carry the
   * token's decimals but not its address (`token_pools`). */
  entityAddress?: string;
}

export function siblingValue(ctx: CellContext, row: unknown[], name: string): unknown {
  const index = ctx.columnIndex.get(name);
  return index === undefined ? undefined : row[index];
}

function symbolFor(ctx: CellContext, row: unknown[], column: string): string | null {
  const names = column === "token_address" ? ["symbol"] : [`${column}_symbol`, "symbol"];
  for (const name of names) {
    const value = siblingValue(ctx, row, name);
    if (typeof value === "string" && value) return value;
  }
  return null;
}

function resolvedFor(ctx: CellContext, row: unknown[], column: string): boolean | null {
  const names = column === "token_address" ? ["is_resolved"] : [`${column}_resolved`];
  for (const name of names) {
    const value = siblingValue(ctx, row, name);
    if (value !== undefined && value !== null) return truthy(value);
  }
  return null;
}

function decimalsFor(ctx: CellContext, row: unknown[], base: string): unknown {
  return siblingValue(ctx, row, `${base}_decimals`);
}

/** The address whose decimals scale `${base}_raw`. `token0` / `token1` have
 * their own columns; everything else is the row's own token, falling back to
 * the table's entity (token_pools carries `token_decimals` but no address). */
function addressForAmount(ctx: CellContext, row: unknown[], base: string): unknown {
  const slot = base.match(/([01])$/)?.[1];
  if (slot !== undefined) return siblingValue(ctx, row, `token${slot}`);
  return siblingValue(ctx, row, "token_address") ?? ctx.entityAddress;
}

/** One amount cell: indexer units, else chain-state units (marked), else raw
 * base units (marked). The three states never blur into each other. */
function amountCell(
  ctx: CellContext,
  row: unknown[],
  base: string,
  raw: unknown,
  decimals: unknown,
  units: unknown,
  /** Title for the plain (indexer-adjusted) state; the two marked states carry
   * their own disclosure. */
  plainTitle?: string,
) {
  const address = addressForAmount(ctx, row, base);
  const fromChain = resolveDecimals(decimals, address, ctx.overlay);
  const amount = fmtAmountWithOverlay(
    raw, decimals, units,
    fromChain.source === "overlay" ? fromChain.decimals : null,
  );
  const className = amount.rawUnits
    ? "plx-amount plx-amount--raw"
    : amount.chain
      ? "plx-amount plx-amount--chain"
      : "plx-amount";
  return (
    <span
      className={className}
      title={amount.rawUnits
        ? "Raw base units — token decimals unknown"
        : amount.chain ? overlayAmountTitle(fromChain.blockNumber) : plainTitle}
    >
      {amount.text}
      {amount.rawUnits && <sup>raw</sup>}
      {amount.chain && <sup>chain</sup>}
    </span>
  );
}

/** Kind-dispatched renderer; returns undefined to fall back to plain text. */
export function renderPlxCell(
  kind: CellKind | undefined,
  column: string,
  value: unknown,
  row: unknown[],
  ctx: CellContext,
): ReactNode | undefined {
  if (value === null || value === undefined || value === "") {
    if (kind === "tokenList" || kind === "list") return "—";
    // A `*_units` column is NULL exactly when the server could not scale the
    // raw amount — the hole the chain-state overlay exists to fill. Fall
    // through so the `amount` case can decide; with no overlay decimals it
    // returns undefined and the table renders its dash, as before.
    if (kind !== "amount") return undefined;
  }
  switch (kind) {
    case "pool": {
      const address = String(value);
      const name = siblingValue(ctx, row, "pool_name");
      return (
        <span className="plx-pool-cell" title={address}>
          <code>{shortAddr(address)}</code>
          {typeof name === "string" && name ? <span className="plx-pool-cell__name">{name}</span> : null}
        </span>
      );
    }
    case "token":
      return (
        <TokenLabel
          address={String(value)}
          symbol={symbolFor(ctx, row, column)}
          resolved={resolvedFor(ctx, row, column)}
          overlay={ctx.overlay}
        />
      );
    case "tokenList": {
      const tokens = coerceStringArray(value);
      const labels = coerceStringArray(siblingValue(ctx, row, "counter_labels"));
      return (
        <span className="plx-token-list">
          {tokens.map((token, index) => (
            <TokenLabel
              key={token}
              address={token}
              symbol={labels[index] && !/…|\.\.\./.test(labels[index]) ? labels[index] : null}
              overlay={ctx.overlay}
              onClick={ctx.onEntity ? (address) => ctx.onEntity?.("token", address) : undefined}
            />
          ))}
        </span>
      );
    }
    case "class":
      return <ClassBadge poolClass={String(value)} />;
    case "family":
      return <span className="plx-family">{familyLabel(value)}</span>;
    case "fee":
      return <FeeChip fee={Number(value)} poolClass={String(siblingValue(ctx, row, "pool_class") ?? "")} />;
    case "feeBand":
      return <span className="plx-family">{String(value)}</span>;
    case "probe":
      return (
        <ProbeBadge
          family={String(siblingValue(ctx, row, "pool_family") ?? "cl")}
          probed={truthy(value)}
        />
      );
    case "price": {
      if (ADJUSTED_PRICE_COLUMNS.has(column)) {
        return <PriceCell raw={null} adjusted={value} dec0={0} dec1={0} />;
      }
      const isAdjusted = column.endsWith("_adjusted");
      const rawColumn = isAdjusted ? column.replace(/_adjusted$/, "_raw") : column;
      const adjustedColumn = isAdjusted ? column : column.replace(/_raw$/, "_adjusted");
      // `price_raw_at_tick` has no adjusted twin — it stays raw, as marked.
      return (
        <PriceCell
          raw={siblingValue(ctx, row, rawColumn) ?? value}
          adjusted={isAdjusted ? value : siblingValue(ctx, row, adjustedColumn)}
          dec0={decimalsFor(ctx, row, "token0")}
          dec1={decimalsFor(ctx, row, "token1")}
          overlay={ctx.overlay}
          token0={siblingValue(ctx, row, "token0")}
          token1={siblingValue(ctx, row, "token1")}
        />
      );
    }
    case "liquidity":
      return <LiquidityCell value={value} raw={siblingValue(ctx, row, column.replace(/_float$/, "_raw"))} />;
    case "amount": {
      // reserve0_units -> reserve0_raw + token0_decimals; balance_units ->
      // balance_raw + decimals; fees0_units_est -> fees0_raw_est + token0_decimals.
      const base = column.replace(/_units_est$/, "").replace(/_units$/, "");
      const raw = siblingValue(ctx, row, `${base}_raw`) ?? siblingValue(ctx, row, `${base}_raw_est`);
      const tokenSlot = base.match(/([01])$/)?.[1];
      const decimals = tokenSlot !== undefined
        ? decimalsFor(ctx, row, `token${tokenSlot}`)
        : (siblingValue(ctx, row, "decimals") ?? siblingValue(ctx, row, "token_decimals"));
      if (value === null || value === undefined || value === "") {
        // The server had no decimals to scale by. Only the overlay can answer;
        // anything else stays the dash this cell has always rendered.
        const from = resolveDecimals(decimals, addressForAmount(ctx, row, base), ctx.overlay);
        if (from.source !== "overlay") return undefined;
      }
      return amountCell(ctx, row, base, raw, decimals, value);
    }
    case "raw": {
      // reserve0_raw / reserve1_raw without a `_units` twin: units when the
      // token's decimals are known, raw base units otherwise.
      const slot = column.match(/^reserve([01])_raw$/)?.[1];
      if (slot !== undefined) {
        return amountCell(ctx, row, `reserve${slot}`, value, decimalsFor(ctx, row, `token${slot}`), undefined, String(value));
      }
      return <span className="plx-amount plx-amount--raw" title={String(value)}>{fmtRaw(value)}<sup>raw</sup></span>;
    }
    case "int":
      return <span className="plx-num">{fmtInt(value)}</span>;
    case "share":
      return <span className="plx-num">{fmtPct(value)}</span>;
    case "tick":
      return <span className="plx-num">{fmtTick(value)}</span>;
    case "date":
      return <span className="plx-time">{fmtDate(value)}</span>;
    case "time":
      return <span className="plx-time">{fmtTime(value)}</span>;
    case "bool": {
      // "No" on is_live is ambiguous for a CL pool with no state row at all —
      // say which it is (the `has_state` column exists for exactly this).
      if (column === "is_live" && !truthy(value)) {
        const family = String(siblingValue(ctx, row, "pool_family") ?? "");
        const hasState = siblingValue(ctx, row, "has_state");
        if (family !== "reserves_only" && hasState !== undefined && !truthy(hasState)) {
          return (
            <span className="plx-bool" title="No concentrated-liquidity state row was published for this pool — not the same as liquidity being zero">
              no state row
            </span>
          );
        }
      }
      return <span className={truthy(value) ? "plx-bool plx-bool--yes" : "plx-bool"}>{fmtBool(value)}</span>;
    }
    case "address":
    case "hash":
      return <code title={String(value)}>{shortAddr(String(value))}</code>;
    case "list": {
      const entries = coerceStringArray(value);
      return (
        <span className="plx-checks">
          {entries.map((entry) => (
            <span key={entry} className={`plx-check${entry === "cl_below_active_threshold" ? " plx-check--warn" : ""}`}>{entry}</span>
          ))}
        </span>
      );
    }
    default:
      if (column === "pool_class") return classLabel(value);
      return undefined;
  }
}
