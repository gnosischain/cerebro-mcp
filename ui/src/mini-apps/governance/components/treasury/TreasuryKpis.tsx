import { MaKpi, MaKpiGrid } from "../../../shared/MiniAppChrome";
import { chainName } from "../../model/treasuryChains";
import { fmtCount, fmtUnitsCompact, fmtUsd } from "../../model/treasuryFormat";

// Headline tiles. The total is ALWAYS shown with its composition — the
// hub-priced part (dbt price hub, historical-grade) and the spot part
// (CoinGecko, today only) — because they are different kinds of number and a
// single figure would launder the second into the first.

export interface KpiChain {
  chainId: number;
  hubUsd: number | null;
  spotUsd: number;
}

export interface TreasuryKpiProps {
  hubUsd: number | null;
  spotUsd: number;
  /** Spot overlay not loaded yet: the spot part is pending, not zero. */
  spotPending: boolean;
  chains: KpiChain[];
  showChainTiles: boolean;
  gnoUnits: number | null;
  gnoUnitsExLtd: number | null;
  exLtd: boolean;
  wallets: number;
  walletPairs: number;
  /** Assets as the Assets tab lists them, by how each is valued. */
  assets: { listed: number; hub: number; spot: number; unpriced: number; retired: number };
}

/** "hub $X · spot $Y". The spot part is "pending" until the overlay lands
 * (never a spot $0 that is really "not loaded"), and omitted when nothing is
 * spot-valued. */
export function splitLine(hubUsd: number | null, spotUsd: number, spotPending: boolean): string {
  if (spotPending) return `hub ${fmtUsd(hubUsd)} · spot pending`;
  return spotUsd > 0 ? `hub ${fmtUsd(hubUsd)} · spot ${fmtUsd(spotUsd)}` : `hub ${fmtUsd(hubUsd)}`;
}

export function TreasuryKpis(props: TreasuryKpiProps) {
  const total = props.hubUsd === null ? null : props.hubUsd + props.spotUsd;
  const gno = props.exLtd ? props.gnoUnitsExLtd : props.gnoUnits;
  const gnoOther = props.exLtd
    ? `incl. Gnosis Ltd. ${fmtUnitsCompact(props.gnoUnits)}`
    : `ex-Gnosis Ltd. ${fmtUnitsCompact(props.gnoUnitsExLtd)}`;
  const priced = props.assets.hub + props.assets.spot;
  const valuable = priced + props.assets.unpriced;
  return (
    <div className="gov-trs-kpis">
      <MaKpiGrid>
        <MaKpi
          label={props.exLtd ? "Token holdings ex-Ltd." : "Token holdings"}
          value={fmtUsd(total)}
          delta={splitLine(props.hubUsd, props.spotUsd, props.spotPending)}
        />
        {props.showChainTiles
          ? props.chains.map((chain) => (
            <MaKpi
              key={chain.chainId}
              label={chainName(chain.chainId)}
              value={fmtUsd(chain.hubUsd === null ? null : chain.hubUsd + chain.spotUsd)}
              delta={splitLine(chain.hubUsd, chain.spotUsd, props.spotPending)}
            />
          ))
          : null}
        <MaKpi label={props.exLtd ? "GNO held ex-Ltd." : "GNO held"} value={fmtUnitsCompact(gno)} delta={gnoOther} />
        <MaKpi
          label="Wallets"
          value={fmtCount(props.wallets)}
          delta={`${fmtCount(props.walletPairs)} wallet-chain pair${props.walletPairs === 1 ? "" : "s"}`}
        />
        <MaKpi
          label="Assets"
          value={fmtCount(props.assets.listed)}
          delta={`${fmtCount(props.assets.hub)} hub · ${fmtCount(props.assets.spot)} spot · ${fmtCount(props.assets.unpriced)} unpriced`}
        />
        <MaKpi
          label="Price coverage"
          value={`${fmtCount(priced)} / ${fmtCount(valuable)}`}
          delta="unpriced = unknown, not zero"
        />
      </MaKpiGrid>
    </div>
  );
}
