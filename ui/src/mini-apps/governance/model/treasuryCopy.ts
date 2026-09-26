// Fixed treasury copy. One place so the words for a class, a spam reason or
// the valuation method are identical on every surface that uses them, and so
// the render tests can pin them.

import type { SpamReason, TokenClass } from "./treasuryRows";

export const TOKEN_CLASS_COPY: Record<TokenClass, { label: string; short: string; description: string }> = {
  priced: {
    label: "Hub-priced",
    short: "hub",
    description: "Reviewed token valued from the dbt price hub's daily USD price, matched by address through the reviewed registry (directly, or through a pegged asset's series).",
  },
  listed: {
    label: "Listed",
    short: "listed",
    description: "Reviewed, real token with no hub price. Valued at CoinGecko spot for today only, when a plausible quote exists; never in history.",
  },
  unverified: {
    label: "Unverified",
    short: "unverified",
    description: "Not in the reviewed registry. Shown, but never valued — not even at spot.",
  },
  spam: {
    label: "Spam",
    short: "spam",
    description: "Hidden by default and never valued.",
  },
  retired_mirror: {
    label: "Retired mirror",
    short: "retired",
    description: "EURe/GBPe v1 after the 2024-08-25 migration: it mirrors the v2 token, so it is excluded from totals to avoid double counting. Not spam.",
  },
};

export const SPAM_REASON_COPY: Record<Exclude<SpamReason, "">, { label: string; description: string }> = {
  impersonation: {
    label: "Impersonation",
    description: "Claims the name or symbol of a reviewed token from a different address.",
  },
  lure: {
    label: "Lure",
    description: "Its name or symbol carries a web address or a call to action (visit, claim, airdrop).",
  },
  obfuscated: {
    label: "Obfuscated",
    description: "Uses invisible or look-alike characters to pass as another token.",
  },
  malformed: {
    label: "Malformed",
    description: "Unreadable or broken token metadata.",
  },
  mass_airdrop: {
    label: "Mass airdrop",
    description: "Sent unsolicited to most treasury wallets (at least 75% of the active wallets on its chain).",
  },
};

/** The reason in words, or a generic line for an unrecognised reason. */
export function spamReasonText(reason: SpamReason): { label: string; description: string } {
  return reason ? SPAM_REASON_COPY[reason] : { label: "Flagged", description: "Flagged as spam by the classifier." };
}

export const SCOPE_NOTE =
  "ERC-20 token holdings only — native ETH/xDAI balances and non-tokenized positions are not indexed.";

export const PROVENANCE_LINE =
  "ERC-20 balances at finalized blocks · daily historical prices (dbt price hub) · CoinGecko spot fallback";

// The approved wording said unpriced AND unverified tokens get today's spot in
// current totals; the contract since narrowed spot to 'listed' tokens only
// (unverified tokens are never valued), so the caption says exactly that.
export const METHOD_CAPTION =
  "Month-end balances × the dbt price hub's daily USD price on each month-end. Hub-priced assets only; "
  + "listed tokens without a hub price are valued at today's CoinGecko spot in current totals and never in "
  + "history; unverified tokens are never valued.";

export const PROXY_NOTE =
  "Hub-priced through a pegged asset's series (e.g. stETH with WETH, DAI with xDAI).";

export const SPOT_NOTE = "CoinGecko spot, today only — never used in history.";

export const HIDDEN_NOTE = "Spam tokens are hidden by default and never valued.";

export const UNKNOWN_NOT_ZERO = "Unpriced means unknown, not zero.";

export const LABEL_ATTRIBUTION_PREFIX = "Label from";
