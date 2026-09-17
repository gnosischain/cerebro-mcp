import { MaIdentity, MaKpi, MaKpiGrid } from "../../shared/MiniAppChrome";
import { shortAddr } from "../../../utils/format";
import { fmtAmountWithOverlay, fmtDate, fmtInt, fmtLiquidity, fmtTick } from "../model/format";
import { resolveDecimals, resolveTokenLabel, type TokenOverlay } from "../model/tokenOverlay";
import type { PoolDetailView } from "../model/parseRows";
import { ClassBadge } from "./ClassBadge";
import { FeeChip } from "./FeeChip";
import { PairLabel } from "./PairLabel";
import { PriceCell } from "./PriceCell";
import { ProbeBadge } from "./ProbeBadge";

// Identity + state KPIs for one pool. The identity LABEL is the class plus a
// short address (never a symbol); the pair renders through TokenLabel, which
// discloses unresolved tokens. CL pools show tick / price / L; reserves-only
// pools show their raw reserves per asset.

export function PoolStateHeader({ detail, inverted, overlay, onToken }: {
  detail: PoolDetailView;
  inverted: boolean;
  /** Chain-state fallbacks for assets the indexer never catalogued. */
  overlay?: TokenOverlay;
  onToken: (address: string) => void;
}) {
  const cl = detail.poolFamily !== "reserves_only";
  const assets = detail.assets.length ? detail.assets : [detail.token0, detail.token1].filter(Boolean);
  const symbols = detail.assets.length > 2
    ? detail.assetSymbols
    : [detail.token0Symbol, detail.token1Symbol];
  const decimalsOf = (index: number) => (detail.assets.length > 2
    ? detail.assetDecimals[index] ?? null
    : (index === 0 ? detail.token0Decimals : detail.token1Decimals));
  // A KPI value is a plain string, so the marker is spelled out: the figure is
  // suffixed "raw" (no decimals anywhere) or "chain state" (decimals read live).
  const amountKpi = (raw: unknown, decimals: unknown, address: unknown, units?: unknown) => {
    const from = resolveDecimals(decimals, address, overlay);
    const amount = fmtAmountWithOverlay(raw, decimals, units, from.source === "overlay" ? from.decimals : null);
    return `${amount.text}${amount.rawUnits ? " raw" : amount.chain ? " chain state" : ""}`;
  };
  const labelOf = (symbol: unknown, address: unknown) => resolveTokenLabel(address, symbol, overlay).text;
  const reserveRawFor = (asset: string, index: number): string | null => {
    const at = detail.reserveTokens.indexOf(asset);
    const raw = detail.reservesRaw[at >= 0 ? at : index];
    return raw ?? null;
  };
  const copy = () => {
    void navigator.clipboard?.writeText(detail.address);
  };
  const kpis = cl
    ? [
        { label: "Current tick", value: detail.hasState ? fmtTick(detail.currentTick) : "no state row" },
        { label: "Liquidity (L)", value: fmtLiquidity(detail.liquidity) },
        { label: "Initialized ticks", value: detail.tickCount === null ? (detail.probed ? "—" : "not probed") : fmtInt(detail.tickCount) },
        { label: "As of", value: fmtDate(detail.asOf) },
        { label: "First published", value: fmtDate(detail.firstPublished) },
        { label: "Days live", value: detail.daysLive === null ? "—" : fmtInt(detail.daysLive) },
      ]
    : [
        { label: "Assets", value: fmtInt(detail.nAssets || assets.length) },
        { label: "Reserves as of", value: fmtDate(detail.reservesAsOf) },
        { label: "First published", value: fmtDate(detail.firstPublished) },
        { label: "Days published", value: fmtInt(detail.daysPublished) },
        { label: "Deployed at block", value: fmtInt(detail.deploymentBlock) },
      ];
  return (
    <div className="plx-header">
      <MaIdentity
        label={detail.entityLabel || `${detail.poolClass} · ${shortAddr(detail.address)}`}
        value={detail.name || detail.address}
        onCopy={copy}
        rightSlot={
          <span className="plx-badges">
            <ClassBadge poolClass={detail.poolClass} />
            {cl && <FeeChip fee={detail.fee} poolClass={detail.poolClass} />}
            <ProbeBadge family={detail.poolFamily} probed={detail.probed} hasState={detail.hasState} />
            <span className={`plx-badge ${detail.live ? "plx-badge--live" : "plx-badge--dead"}`}>
              {detail.live ? "live" : cl ? (detail.hasState ? "no liquidity" : "no state row") : "empty"}
            </span>
          </span>
        }
      />
      <div className="plx-header__pair">
        <PairLabel assets={assets} symbols={symbols} overlay={overlay} onToken={onToken} />
        {cl && (
          <span className="plx-header__price">
            price{" "}
            <PriceCell
              raw={detail.priceRaw}
              adjusted={detail.priceAdjusted}
              dec0={detail.token0Decimals}
              dec1={detail.token1Decimals}
              inverted={inverted}
              overlay={overlay}
              token0={detail.token0}
              token1={detail.token1}
            />{" "}
            <span className="plx-header__unit">
              {inverted
                ? `${labelOf(detail.token0Symbol, detail.token0)} per ${labelOf(detail.token1Symbol, detail.token1)}`
                : `${labelOf(detail.token1Symbol, detail.token1)} per ${labelOf(detail.token0Symbol, detail.token0)}`}
            </span>
          </span>
        )}
        <code className="plx-header__addr" title={detail.address}>{detail.address}</code>
        {detail.poolId && <code className="plx-header__addr" title="Balancer pool id">{shortAddr(detail.poolId, 10, 6)}</code>}
      </div>
      <MaKpiGrid>
        {kpis.map((kpi) => <MaKpi key={kpi.label} label={kpi.label} value={kpi.value} />)}
        {!cl && assets.map((asset, index) => (
          <MaKpi
            key={asset}
            label={`Reserve · ${labelOf(symbols[index], asset)}`}
            value={amountKpi(reserveRawFor(asset, index), decimalsOf(index), asset)}
          />
        ))}
        {cl && (
          <>
            <MaKpi
              label={`Reserve · ${labelOf(detail.token0Symbol, detail.token0)}`}
              value={amountKpi(detail.reserve0Raw, detail.token0Decimals, detail.token0, detail.reserve0Units)}
            />
            <MaKpi
              label={`Reserve · ${labelOf(detail.token1Symbol, detail.token1)}`}
              value={amountKpi(detail.reserve1Raw, detail.token1Decimals, detail.token1, detail.reserve1Units)}
            />
          </>
        )}
      </MaKpiGrid>
    </div>
  );
}
