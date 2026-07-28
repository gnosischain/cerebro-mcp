// Solver directory table — custom (not CuratedTable) because rows aggregate
// BY SOLVER NAME across networks and expand into per-chain address rows.
//
// Aggregation contract (pure, unit-tested):
// - Registry-known addresses group under their registry name (solverInfo /
//   legacy solverName fallback); unregistered addresses stay their own row
//   (short address + monogram) — a registry-staleness signal, never hidden.
// - Activity is judged against each row's OWN chain_anchor_at (the chain's
//   newest settlement): a stale indexer must not fake solver inactivity.
//   "Last seen Nd ago" is measured against the freshest chain anchor.

import { Fragment, useState } from "react";
import { formatTime } from "./cells";
import { ChainBadge } from "../../shared/ChainBadge";
import { shortAddr } from "../../../utils/format";
import { solverInfo, solverName, type SolverEnv } from "../model/solverRegistry";
import type { EntityType } from "../types";

type Row = Record<string, unknown>;

const ACTIVE_WINDOW_MS = 7 * 86_400_000;

export interface DirectoryChainRow {
  chainId: number;
  address: string;
  env: SolverEnv | null;
  firstAt: string;
  lastAt: string;
  anchorAt: string;
  settlements: number;
  competitions: number;
  wins: number;
  /** last settlement within 7d of THIS chain's own anchor. */
  active: boolean;
}

export interface DirectoryGroup {
  /** Stable identity: `name:<registry name>` or `addr:<address>`. */
  key: string;
  name: string;
  registered: boolean;
  envs: SolverEnv[];
  chains: number[];
  settlements: number;
  competitions: number;
  wins: number;
  /** Active on ANY chain (per-chain anchor honesty, see above). */
  active: boolean;
  /** Whole days between the freshest chain anchor and this solver's newest
   * settlement; null when the solver has no parseable settlement time. */
  lastSeenDays: number | null;
  rows: DirectoryChainRow[];
}

function parseMs(value: unknown): number | null {
  const t = Date.parse(String(value ?? ""));
  return Number.isFinite(t) ? t : null;
}

/** Aggregate raw solver_directory rows ((chain, solver) grain) into one
 * entry per solver identity. */
export function aggregateDirectory(rows: Row[]): DirectoryGroup[] {
  let freshestAnchor: number | null = null;
  for (const row of rows) {
    const anchor = parseMs(row.chain_anchor_at);
    if (anchor !== null && (freshestAnchor === null || anchor > freshestAnchor)) {
      freshestAnchor = anchor;
    }
  }
  const groups = new Map<string, DirectoryGroup & { lastMs: number | null }>();
  for (const row of rows) {
    const chainId = Number(row.chain_id);
    const address = String(row.solver ?? "").toLowerCase();
    if (!address) continue;
    const info = solverInfo(chainId, address);
    const legacyName = info ? "" : solverName(chainId, address);
    const registered = Boolean(info || legacyName);
    const name = info?.name ?? (legacyName || shortAddr(address));
    const key = registered ? `name:${name}` : `addr:${address}`;
    const lastMs = parseMs(row.last_settlement_at);
    const anchorMs = parseMs(row.chain_anchor_at);
    const active = lastMs !== null && anchorMs !== null && lastMs >= anchorMs - ACTIVE_WINDOW_MS;
    const chainRow: DirectoryChainRow = {
      chainId,
      address,
      env: info?.env ?? null,
      firstAt: String(row.first_settlement_at ?? ""),
      lastAt: String(row.last_settlement_at ?? ""),
      anchorAt: String(row.chain_anchor_at ?? ""),
      settlements: Number(row.settlements_all_time ?? 0),
      competitions: Number(row.competitions_all ?? 0),
      wins: Number(row.wins_all ?? 0),
      active,
    };
    const entry = groups.get(key) ?? {
      key,
      name,
      registered,
      envs: [],
      chains: [],
      settlements: 0,
      competitions: 0,
      wins: 0,
      active: false,
      lastSeenDays: null,
      rows: [],
      lastMs: null,
    };
    if (chainRow.env && !entry.envs.includes(chainRow.env)) entry.envs.push(chainRow.env);
    if (!entry.chains.includes(chainId)) entry.chains.push(chainId);
    entry.settlements += chainRow.settlements;
    entry.competitions += chainRow.competitions;
    entry.wins += chainRow.wins;
    entry.active = entry.active || active;
    if (lastMs !== null && (entry.lastMs === null || lastMs > entry.lastMs)) entry.lastMs = lastMs;
    entry.rows.push(chainRow);
    groups.set(key, entry);
  }
  const result: DirectoryGroup[] = [];
  for (const entry of groups.values()) {
    const { lastMs, ...group } = entry;
    group.chains.sort((a, b) => a - b);
    group.envs.sort(); // "barn" < "prod"; render order handled by the pill map
    group.rows.sort((a, b) => a.chainId - b.chainId);
    group.lastSeenDays = lastMs !== null && freshestAnchor !== null
      ? Math.max(0, Math.floor((freshestAnchor - lastMs) / 86_400_000))
      : null;
    result.push(group);
  }
  return result.sort((a, b) =>
    b.settlements - a.settlements || b.wins - a.wins || a.name.localeCompare(b.name));
}

export interface DirectoryFilters {
  /** Case-insensitive substring over name and every observed address. */
  search: string;
  /** Empty = all networks; otherwise the group must touch ANY listed chain. */
  chains: number[];
  env: "all" | "prod" | "barn" | "unknown";
  activeOnly: boolean;
}

export function filterDirectory(groups: DirectoryGroup[], filters: DirectoryFilters): DirectoryGroup[] {
  const needle = filters.search.trim().toLowerCase();
  return groups.filter((group) => {
    if (filters.activeOnly && !group.active) return false;
    if (filters.chains.length > 0 && !group.chains.some((chain) => filters.chains.includes(chain))) return false;
    if (filters.env === "unknown" && group.envs.length > 0) return false;
    if ((filters.env === "prod" || filters.env === "barn") && !group.envs.includes(filters.env)) return false;
    if (needle) {
      const inName = group.name.toLowerCase().includes(needle);
      const inAddress = group.rows.some((row) => row.address.includes(needle));
      if (!inName && !inAddress) return false;
    }
    return true;
  });
}

/** Deterministic monogram hue from the solver identity. */
export function monogramHue(seed: string): number {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
  }
  return hash % 360;
}

function monogramText(group: DirectoryGroup): string {
  if (group.registered) return group.name.replace(/[^a-zA-Z0-9]/g, "").slice(0, 2).toUpperCase() || "?";
  return group.rows[0]?.address.slice(2, 4).toUpperCase() ?? "?";
}

function EnvPills({ group }: { group: DirectoryGroup }) {
  if (group.envs.length === 0) {
    return <span className="cow-dir-pill cow-dir-pill--unknown" title="Address not in the bundled solver registry">unknown</span>;
  }
  const order: SolverEnv[] = ["prod", "barn"];
  return (
    <>
      {order.filter((env) => group.envs.includes(env)).map((env) => (
        <span key={env} className={`cow-dir-pill cow-dir-pill--${env}`}>{env}</span>
      ))}
    </>
  );
}

function Activity({ group }: { group: DirectoryGroup }) {
  if (group.active) {
    return (
      <span className="cow-dir-activity" title="Settled within 7 days of its chain's newest settlement (per-chain anchor)">
        <span className="cow-dir-dot cow-dir-dot--active" /> active
      </span>
    );
  }
  if (group.lastSeenDays !== null) {
    return <span className="cow-dir-activity">last seen {group.lastSeenDays}d ago</span>;
  }
  return <span className="cow-dir-activity cow-dir-activity--never">no settlements</span>;
}

export interface SolverDirectoryTableProps {
  groups: DirectoryGroup[];
  onEntity: (entityType: EntityType, identifier: string, chainId?: number) => void;
}

export function SolverDirectoryTable({ groups, onEntity }: SolverDirectoryTableProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState("");
  const toggle = (key: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const copy = (address: string) => {
    void navigator.clipboard?.writeText(address).then(() => {
      setCopied(address);
      window.setTimeout(() => setCopied((value) => (value === address ? "" : value)), 1500);
    });
  };
  if (groups.length === 0) {
    return <div className="cow-empty">No solvers match the current filters.</div>;
  }
  return (
    <div className="cow-matrix-scroll">
      <table className="cow-dir">
        <thead>
          <tr>
            <th aria-label="Expand" />
            <th>Solver</th>
            <th>Networks</th>
            <th>Env</th>
            <th>Activity</th>
            <th className="cow-dir__num">Settlements</th>
            <th className="cow-dir__num">Wins</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => {
            const isOpen = expanded.has(group.key);
            return (
              <Fragment key={group.key}>
                <tr className="cow-dir__row" onClick={() => toggle(group.key)}>
                  <td className="cow-dir__chevron">
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-label={`${isOpen ? "Collapse" : "Expand"} ${group.name}`}
                      onClick={(event) => { event.stopPropagation(); toggle(group.key); }}
                    >
                      {isOpen ? "▾" : "▸"}
                    </button>
                  </td>
                  <td>
                    <span className="cow-dir-solver">
                      <span
                        className="cow-dir-avatar"
                        style={{ background: `hsl(${monogramHue(group.key)} 45% 32%)` }}
                        aria-hidden="true"
                      >
                        {monogramText(group)}
                      </span>
                      <span className={group.registered ? "cow-dir-name" : "cow-dir-name cow-dir-name--raw"}>
                        {group.name}
                      </span>
                    </span>
                  </td>
                  <td>
                    <span className="cow-dir-chains">
                      {group.chains.map((chainId) => <ChainBadge key={chainId} chainId={chainId} showName={false} />)}
                    </span>
                  </td>
                  <td><EnvPills group={group} /></td>
                  <td><Activity group={group} /></td>
                  <td className="cow-dir__num">{group.settlements.toLocaleString()}</td>
                  <td className="cow-dir__num">{group.wins.toLocaleString()}</td>
                </tr>
                {isOpen && group.rows.map((row) => (
                  <tr key={`${group.key}:${row.chainId}:${row.address}`} className="cow-dir__sub">
                    <td />
                    <td>
                      <span className="cow-dir-sub-addr">
                        <code title={row.address}>{shortAddr(row.address, 10, 6)}</code>
                        <button
                          type="button"
                          className="cow-dir-copy"
                          title="Copy address"
                          onClick={(event) => { event.stopPropagation(); copy(row.address); }}
                        >
                          {copied === row.address ? "✓" : "⎘"}
                        </button>
                      </span>
                    </td>
                    <td><ChainBadge chainId={row.chainId} /></td>
                    <td>
                      {row.env
                        ? <span className={`cow-dir-pill cow-dir-pill--${row.env}`}>{row.env}</span>
                        : <span className="cow-dir-pill cow-dir-pill--unknown">unknown</span>}
                    </td>
                    <td className="cow-dir-sub-dates">
                      {row.firstAt ? formatTime(row.firstAt) : "—"} → {row.lastAt ? formatTime(row.lastAt) : "—"}
                      {row.active && <span className="cow-dir-dot cow-dir-dot--active" title="Active on this chain" />}
                    </td>
                    <td className="cow-dir__num">{row.settlements.toLocaleString()}</td>
                    <td className="cow-dir__num">
                      {row.wins.toLocaleString()}
                      <button
                        type="button"
                        className="cow-dir-open"
                        title="Open solver detail"
                        onClick={(event) => { event.stopPropagation(); onEntity("solver", row.address, row.chainId); }}
                      >
                        Open →
                      </button>
                    </td>
                  </tr>
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
