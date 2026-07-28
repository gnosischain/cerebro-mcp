// Per-chain block-explorer links.
//
// Three call sites hardcoded `https://gnosis.blockscout.com/...`, which meant
// a Base transaction linked to a Gnosis explorer — a dead end at best, and at
// worst a page for an unrelated transaction that happens to share the hash.
// The server publishes each configured chain's explorer base in
// `transactions.chain_options`; these helpers resolve against that, and fall
// back to Gnosis only when the chain is genuinely unknown.

import type { ChainOption } from "../types";

export const GNOSIS_CHAIN_ID = 100;
const GNOSIS_EXPLORER = "https://gnosis.blockscout.com";

export function explorerBase(
  chainId: number | undefined,
  options: ChainOption[] | undefined,
): string {
  const match = (options ?? []).find((o) => o.chain_id === chainId);
  return (match?.explorer || GNOSIS_EXPLORER).replace(/\/+$/, "");
}

export function txUrl(
  hash: string,
  chainId: number | undefined,
  options: ChainOption[] | undefined,
): string {
  return `${explorerBase(chainId, options)}/tx/${hash}`;
}

export function addressUrl(
  address: string,
  chainId: number | undefined,
  options: ChainOption[] | undefined,
): string {
  return `${explorerBase(chainId, options)}/address/${address}`;
}
