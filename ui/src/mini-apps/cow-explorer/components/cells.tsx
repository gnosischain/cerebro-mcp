// Shared kind-dispatched value renderers used by CuratedTable (tables) and
// KeyValueGrid (single-row detail panels). One implementation so token
// identity, solver naming, amount fallbacks, and time formatting can never
// drift between surfaces.

import type { ReactNode } from "react";
import type { CellKind } from "../model/columns";
import { displayAmount, shortAddr } from "../model/identity";
import { solverName } from "../model/solverRegistry";
import type { CowExplorerViewState } from "../types";
import { ChainBadge } from "./ChainBadge";
import { TokenIdentity } from "./TokenIdentity";

export interface CellContext {
  state: CowExplorerViewState;
  columnIndex: Map<string, number>;
}

export function siblingValue(ctx: CellContext, row: unknown[], name: string): unknown {
  const index = ctx.columnIndex.get(name);
  return index === undefined ? undefined : row[index];
}

export function rowChainId(ctx: CellContext, row: unknown[]): number {
  const value = siblingValue(ctx, row, "chain_id");
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : ctx.state.chain_id;
}

export function symbolFor(ctx: CellContext, row: unknown[], column: string): string {
  const names = column === "token"
    ? ["token_symbol", "symbol"]
    : column === "token0" || column === "token1"
      ? [`${column}_symbol`]
      : column.endsWith("_token")
        ? [`${column.replace(/_token$/, "")}_symbol`, `${column}_symbol`]
        : [`${column}_symbol`];
  for (const name of names) {
    const value = siblingValue(ctx, row, name);
    if (typeof value === "string" && value) return value;
  }
  return "";
}

export function formatTime(value: unknown): string {
  const text = String(value ?? "");
  const match = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/);
  return match ? `${match[1]} ${match[2]}` : text;
}

/** Kind-dispatched renderer; returns undefined to fall back to plain text. */
export function renderKindValue(
  kind: CellKind | undefined,
  column: string,
  value: unknown,
  row: unknown[],
  ctx: CellContext,
): ReactNode | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  if (kind === "token" && typeof value === "string") {
    const chainId = rowChainId(ctx, row);
    const overlayIcon =
      ctx.state.icon_overlay?.[String(chainId)]?.[value.toLowerCase()] ?? "";
    return (
      <TokenIdentity
        address={value}
        iconUrl={overlayIcon}
        symbol={symbolFor(ctx, row, column)}
      />
    );
  }
  if (kind === "chain") {
    return <ChainBadge chainId={Number(value)} />;
  }
  if (kind === "solver" && typeof value === "string") {
    const name = solverName(rowChainId(ctx, row), value);
    return (
      <span className="cow-solver" title={value}>
        {name || shortAddr(value)}
      </span>
    );
  }
  if ((kind === "hash" || kind === "orderUid" || kind === "address") && typeof value === "string") {
    return <code title={value}>{shortAddr(value)}</code>;
  }
  if (kind === "amount") {
    const base = column
      .replace(/_amount_normalized$/, "")
      .replace(/_amount$/, "")
      .replace(/^remaining_/, "");
    const raw =
      siblingValue(ctx, row, `${column}_raw`)
      ?? siblingValue(ctx, row, `${base}_amount_raw`)
      ?? siblingValue(ctx, row, "amount_raw");
    const decimals =
      siblingValue(ctx, row, `${base}_decimals`)
      ?? siblingValue(ctx, row, "token_decimals");
    const display = displayAmount(
      raw as string | number | null | undefined,
      typeof value === "number" ? value : Number(value),
      decimals as number | null | undefined,
    );
    return (
      <span
        className={display.rawUnits || display.suspect ? "cow-amount cow-amount--raw" : "cow-amount"}
        title={
          display.rawUnits
            ? "Raw base units — token decimals unknown"
            : display.suspect
              ? "decimals=0 is ambiguous in the indexer — verify before trusting scale"
              : undefined
        }
      >
        {display.text}
        {(display.rawUnits || display.suspect) && <sup>⚠</sup>}
      </span>
    );
  }
  if (kind === "time") {
    return <span className="cow-time">{formatTime(value)}</span>;
  }
  return undefined;
}
