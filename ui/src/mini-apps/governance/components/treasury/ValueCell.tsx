import { PROXY_NOTE } from "../../model/treasuryCopy";
import { fmtStamp, fmtUsd } from "../../model/treasuryFormat";
import type { AssetKind } from "../../model/treasuryValue";

// One position's value, saying HOW it is known. Hub values print plainly; a
// spot value carries a "spot" chip; an unpriced position says "unpriced"; a
// refused quote says why; spam and retired mirrors say they are not valued.
// Never "$0" for something unknown.

export function ValueCell({
  kind,
  usd,
  hubUsd,
  spotUsd,
  spotAt = "",
  proxy = false,
}: {
  kind: AssetKind;
  usd: number | null;
  /** For a merged "mixed" row: the hub part. */
  hubUsd?: number;
  /** For a merged "mixed" row: the spot part. */
  spotUsd?: number;
  spotAt?: string;
  /** Hub-priced through a pegged asset's series. */
  proxy?: boolean;
}) {
  const spotTitle = `CoinGecko spot${spotAt ? ` captured ${fmtStamp(spotAt)}` : ""} — today only, never used in history`;
  switch (kind) {
    case "hub":
      return (
        <span className="gov-trs-value" title={proxy ? PROXY_NOTE : "dbt price hub price on the as-of date"}>
          {fmtUsd(usd)}
          {proxy ? <> <span className="gov-trs-chip" title={PROXY_NOTE}>peg proxy</span></> : null}
        </span>
      );
    case "spot":
      return (
        <span className="gov-trs-value" title={spotTitle}>
          {fmtUsd(usd)} <span className="gov-trs-chip gov-trs-chip--spot">spot</span>
        </span>
      );
    case "mixed":
      return (
        <span
          className="gov-trs-value"
          title={`${fmtUsd(hubUsd ?? null)} hub-priced + ${fmtUsd(spotUsd ?? null)} at ${spotTitle}`}
        >
          {fmtUsd(usd)} <span className="gov-trs-chip gov-trs-chip--spot">incl. spot</span>
        </span>
      );
    case "refused":
      return (
        <span
          className="gov-trs-value gov-trs-value--none"
          title="A CoinGecko quote exists but was refused as implausible for this holding (a majority of the token's supply, or larger than the chain's hub-priced total). Value unknown, not zero."
        >
          unpriced
        </span>
      );
    case "hidden":
      return <span className="gov-trs-value gov-trs-value--none" title="Spam is never valued.">not valued</span>;
    case "retired":
      return (
        <span
          className="gov-trs-value gov-trs-value--none"
          title="Retired mirror of the v2 token after the 2024-08-25 migration — excluded from totals to avoid double counting."
        >
          mirror
        </span>
      );
    default:
      return (
        <span
          className="gov-trs-value gov-trs-value--none"
          title="No hub price and no usable spot quote. The balance is real; its USD value is unknown, not zero."
        >
          unpriced
        </span>
      );
  }
}
