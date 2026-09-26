import { useMemo, useState } from "react";

import { useDebouncedValue } from "../../../shared/useDebouncedValue";
import { chainName } from "../../model/treasuryChains";
import { fmtCount, fmtUnits, fmtUsd } from "../../model/treasuryFormat";
import type { WalletRow } from "../../model/treasuryRows";
import {
  matchesQuery,
  primaryChainOf,
  sortWalletGroups,
  type WalletGroup,
  type WalletGrouping,
  type WalletSortKey,
} from "../../model/treasuryWallets";
import { WalletIdentity } from "./WalletIdentity";

// Every treasury wallet, one row per ADDRESS, with a chip per chain that
// opens that chain's wallet page. No cap: the census is ~23 addresses, and a
// "top 25" cut once hid the Gnosis Chain wallets entirely. What the filters
// hide is counted in the footer, never silently dropped.
//
// Value is the hub-priced value (the wallet page adds any spot-valued tokens,
// which need the per-token composition this dataset does not carry).

const SORTS: Array<{ key: WalletSortKey; label: string }> = [
  { key: "value", label: "Value" },
  { key: "gno", label: "GNO" },
  { key: "tokens", label: "Tokens" },
  { key: "name", label: "Name" },
];

/** A wallet's value cell. Unknown is a dash; a wallet with positions but none
 * hub-priced is a dash too — "$0" would claim it is worth nothing. */
export function walletValueText(row: Pick<WalletRow, "navUsd" | "pricedPositions" | "tokensHeld">): string {
  if (row.navUsd === null) return "—";
  if (row.navUsd === 0 && !((row.pricedPositions ?? 0) > 0)) return "—";
  return fmtUsd(row.navUsd);
}

function walletValueTitle(row: Pick<WalletRow, "navUsd" | "pricedPositions" | "unpricedPositions" | "tokensHeld">): string {
  if (row.navUsd === null) return "Not measured.";
  if (row.navUsd === 0 && !((row.pricedPositions ?? 0) > 0)) {
    return (row.tokensHeld ?? 0) > 0
      ? `No hub-priced positions; ${fmtCount(row.unpricedPositions)} unpriced (value unknown, not zero).`
      : "No positions.";
  }
  return "Hub-priced value (dbt price hub) on the as-of date.";
}

export function WalletTable({
  grouping,
  chainFilter,
  exLtd,
  onOpen,
}: {
  grouping: WalletGrouping;
  chainFilter: number;
  exLtd: boolean;
  onOpen: (chainId: number, wallet: string) => void;
}) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 150);
  const [sort, setSort] = useState<WalletSortKey>("value");
  const rows = useMemo(
    () => sortWalletGroups(grouping.groups.filter((group) => matchesQuery(group, debounced)), sort),
    [grouping.groups, debounced, sort],
  );

  return (
    <div className="gov-trs-wallets">
      <div className="gov-trs-tablebar">
        <input
          className="gov-trs-search"
          type="search"
          value={query}
          placeholder="Search label or address"
          aria-label="Search wallets"
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="gov-panel-modes" role="group" aria-label="Sort wallets by">
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
      </div>

      {rows.length === 0 ? (
        <div className="gov-empty">{debounced ? "No wallet matches this search." : "No wallets in this selection."}</div>
      ) : (
        <div className="gov-trs-tablewrap">
          <table className="gov-trs-table gov-trs-table--wallets">
            <thead>
              <tr>
                <th scope="col">Wallet</th>
                <th scope="col">Chains</th>
                <th scope="col" className="gov-trs-num">Value</th>
                <th scope="col" className="gov-trs-num gov-trs-col-gno">GNO</th>
                <th scope="col" className="gov-trs-num gov-trs-col-tokens">Tokens</th>
                <th scope="col" className="gov-trs-col-chev"><span className="gov-trs-sr">Open</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((group) => (
                <WalletTableRow key={group.address} group={group} onOpen={onOpen} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="gov-caption gov-trs-tablefoot">
        {fmtCount(rows.length)} address{rows.length === 1 ? "" : "es"} · {fmtCount(grouping.pairs)} wallet-chain
        {grouping.pairs === 1 ? " pair" : " pairs"}.
        {grouping.hiddenByChain.pairs > 0 ? (
          <>
            {" "}The {chainName(chainFilter)} filter hides {fmtCount(grouping.hiddenByChain.pairs)} wallet-chain
            {grouping.hiddenByChain.pairs === 1 ? " row" : " rows"}
            {grouping.hiddenByChain.addresses > 0
              ? ` (${fmtCount(grouping.hiddenByChain.addresses)} address${grouping.hiddenByChain.addresses === 1 ? "" : "es"} tracked only on the other chain)`
              : ""}.
          </>
        ) : null}
        {exLtd && grouping.hiddenByLtd.addresses > 0 ? (
          <>
            {" "}Gnosis Ltd. excluded: {fmtCount(grouping.hiddenByLtd.addresses)} address
            {grouping.hiddenByLtd.addresses === 1 ? "" : "es"} ({fmtCount(grouping.hiddenByLtd.pairs)} wallet-chain
            {grouping.hiddenByLtd.pairs === 1 ? " row" : " rows"}) hidden.
          </>
        ) : null}
      </p>
    </div>
  );
}

function WalletTableRow({
  group,
  onOpen,
}: {
  group: WalletGroup;
  onOpen: (chainId: number, wallet: string) => void;
}) {
  const primary = primaryChainOf(group);
  const open = () => onOpen(primary, group.address);
  const valueRow = {
    navUsd: group.navUsd,
    pricedPositions: group.pricedPositions,
    unpricedPositions: group.unpricedPositions,
    tokensHeld: group.tokensHeld,
  };
  return (
    <tr className="gov-trs-row" onClick={open}>
      <td className="gov-trs-cell-wallet">
        <button
          type="button"
          className="gov-trs-asset"
          onClick={(event) => {
            event.stopPropagation();
            open();
          }}
        >
          <WalletIdentity
            address={group.address}
            label={group.label}
            labelSource={group.labelSource}
            isLtd={group.isLtd}
          />
        </button>
      </td>
      <td>
        <span className="gov-trs-chainchips">
          {group.chains.map((row) => (
            <button
              key={row.chainId}
              type="button"
              className="gov-trs-chainchip"
              title={`Open this wallet on ${chainName(row.chainId)}: ${walletValueTitle(row)}`}
              onClick={(event) => {
                event.stopPropagation();
                onOpen(row.chainId, group.address);
              }}
            >
              {chainName(row.chainId)} <strong>{walletValueText(row)}</strong>
            </button>
          ))}
        </span>
      </td>
      <td className="gov-trs-num" title={walletValueTitle(valueRow)}>
        <span className={walletValueText(valueRow) === "—" ? "gov-trs-value gov-trs-value--none" : "gov-trs-value"}>
          {walletValueText(valueRow)}
        </span>
      </td>
      <td className="gov-trs-num gov-trs-mono gov-trs-col-gno">{fmtUnits(group.gnoUnits)}</td>
      <td className="gov-trs-num gov-trs-col-tokens gov-trs-mono">
        {fmtCount(group.tokensHeld)}
        {(group.hiddenPositions ?? 0) > 0 ? (
          <small title="Hidden positions (spam, retired mirrors) — not counted, never valued"> +{fmtCount(group.hiddenPositions)} hidden</small>
        ) : null}
      </td>
      <td className="gov-trs-col-chev" aria-hidden="true">›</td>
    </tr>
  );
}
