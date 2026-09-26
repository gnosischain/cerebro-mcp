import { useState } from "react";

import { shortAddr } from "../../../../utils/format";
import { LABEL_ATTRIBUTION_PREFIX } from "../../model/treasuryCopy";

// A treasury wallet: its community label (when it has one) AND its address,
// always both — a label is a claim someone made, the address is the identity.
// The label's attribution travels in the tooltip, never instead of the label.

function attribution(label: string, labelSource: string): string {
  if (!label) return "No community label for this address.";
  return labelSource
    ? `"${label}" — ${LABEL_ATTRIBUTION_PREFIX} ${labelSource}.`
    : `"${label}" — label source not recorded.`;
}

export function WalletIdentity({
  address,
  label,
  labelSource,
  isLtd = false,
}: {
  address: string;
  label: string;
  labelSource: string;
  isLtd?: boolean;
}) {
  return (
    <span className="gov-trs-wallet" title={`${attribution(label, labelSource)}\n${address}`}>
      {label ? <span className="gov-trs-wallet__label">{label}</span> : null}
      <code className={label ? "gov-trs-wallet__addr" : "gov-trs-wallet__addr gov-trs-wallet__addr--solo"}>
        {shortAddr(address)}
      </code>
      {isLtd ? <span className="gov-ltd-badge">Ltd.</span> : null}
    </span>
  );
}

/** Page header for a wallet: label, full address, copy, explorer link. */
export function WalletHeader({
  kicker,
  address,
  label,
  labelSource,
  isLtd = false,
  explorer,
  onOpenExplorer,
}: {
  kicker: string;
  address: string;
  label: string;
  labelSource: string;
  isLtd?: boolean;
  /** Explorer URL ("" hides the link). */
  explorer: string;
  onOpenExplorer: (url: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(address);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <div className="gov-trs-identity">
      <div className="gov-trs-identity__kicker">{kicker}</div>
      <div className="gov-trs-identity__name">
        <span title={attribution(label, labelSource)}>{label || "Unlabelled wallet"}</span>
        {isLtd ? <span className="gov-ltd-badge">Gnosis Ltd.</span> : null}
      </div>
      <div className="gov-trs-identity__addr">
        <code>{address}</code>
        <button type="button" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
        {explorer ? (
          <button type="button" onClick={() => onOpenExplorer(explorer)}>Explorer ↗</button>
        ) : null}
      </div>
      {label ? (
        <div className="gov-trs-identity__source">
          {LABEL_ATTRIBUTION_PREFIX} {labelSource || "an unrecorded source"}
        </div>
      ) : null}
    </div>
  );
}
