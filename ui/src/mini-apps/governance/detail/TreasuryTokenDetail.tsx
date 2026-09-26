import { useMemo, useState } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { shortAddr } from "../../../utils/format";
import { MaKpi, MaKpiGrid } from "../../shared/MiniAppChrome";
import { SegmentedControl } from "../../shared/SegmentedControl";
import { DatasetPanel } from "../components/DatasetPanel";
import { ChainSwitcher, type ChainOption } from "../components/treasury/ChainSwitcher";
import { GapNote, partialNotes } from "../components/treasury/GapNote";
import { TokenClassBadge } from "../components/treasury/TokenClassBadge";
import { ValueCell } from "../components/treasury/ValueCell";
import { ValueHistoryPanel } from "../components/treasury/ValueHistoryPanel";
import { WalletIdentity } from "../components/treasury/WalletIdentity";
import { bandRefFromSeriesId, priceLineOption, valueStackOption } from "../model/treasuryCharts";
import { chainName, explorerUrl, TREASURY_CHAIN_IDS } from "../model/treasuryChains";
import { spamReasonText, TOKEN_CLASS_COPY } from "../model/treasuryCopy";
import { fmtCount, fmtPrice, fmtShare, fmtStamp, fmtUnits, fmtUsd } from "../model/treasuryFormat";
import { factBuckets, historyFrame, holderSeriesFacts, stackFacts } from "../model/treasuryHistory";
import {
  address as toAddress,
  parseCoverage,
  parseHolders,
  parseHolderSeries,
  parsePriceHistory,
  parseSummary,
  parseTokenDetail,
} from "../model/treasuryRows";
import { spotQuote, spotRefused, valuationOf } from "../model/treasuryValue";
import { useDataset, type GovViewContext } from "../sections/common";
import { datasetPhase, useSpotSource } from "../sections/treasury/model";

// ONE token on ONE chain. The identity is the ADDRESS: a registry asset shows
// its reviewed symbol; anything else shows its sanitized on-chain symbol next
// to the address, because anyone can deploy a contract called "USDC".
//
// A token the treasury no longer holds has NO detail row (token_detail is
// empty when nothing is held on the as-of day), yet its holdings history and
// price history still exist — the page says "not held on <as-of>" and keeps
// showing them, rather than reading as a failed load.

const SRC = "rpc_state_indexer month-end balances (one token)";

function parseIdentifier(identifier: string): { chainId: number; token: string } {
  const sep = identifier.indexOf(":");
  const chainId = sep > 0 ? Number(identifier.slice(0, sep)) : 0;
  return { chainId: Number.isFinite(chainId) ? chainId : 0, token: toAddress(sep > 0 ? identifier.slice(sep + 1) : identifier) };
}

/** Share of the token's OWN supply. A holding cannot exceed its supply: when
 * the figure says it does, the contract's balanceOf is not trustworthy (the
 * classic spoof returns the same balance to every caller) and a percentage
 * would dress that up as a measurement. */
function supplyShareText(share: number | null): { text: string; title: string } {
  if (share === null) return { text: "—", title: "Total supply not observed for this token" };
  if (share > 1) {
    return {
      text: "> supply",
      title: "Reported balance exceeds the token's own total supply — this contract's balanceOf is not trustworthy",
    };
  }
  return { text: fmtShare(share), title: "Share of this token's own total supply" };
}

export function TreasuryTokenDetail({ ctx }: { ctx: GovViewContext }) {
  const { view } = ctx.treasury;
  const detailDs = useDataset(ctx, "treasury_token_detail");
  const holdersDs = useDataset(ctx, "treasury_token_holders");
  const seriesDs = useDataset(ctx, "treasury_token_holder_series");
  const pricesDs = useDataset(ctx, "treasury_token_price_history");
  const monthsDs = useDataset(ctx, "treasury_token_months");
  // The section summary, when still in the view: the as-of day to name when
  // the token is not held (the detail row that would carry it is absent).
  const summaryDs = useDataset(ctx, "treasury_summary");
  const spot = useSpotSource(ctx);
  const [measure, setMeasure] = useState<"usd" | "units">("usd");

  const fromId = parseIdentifier(ctx.state.selected_entity?.identifier ?? "");
  const detail = useMemo(() => parseTokenDetail(detailDs), [detailDs]);
  const chainId = fromId.chainId || detail?.chainId || 1;
  const token = detail?.token || fromId.token;
  const holders = useMemo(() => parseHolders(holdersDs), [holdersDs]);
  const series = useMemo(() => parseHolderSeries(seriesDs), [seriesDs]);
  const prices = useMemo(() => parsePriceHistory(pricesDs), [pricesDs]);
  const months = useMemo(() => parseCoverage(monthsDs), [monthsDs]);

  const tokenClass = detail?.tokenClass ?? "unverified";
  const trusted = Boolean(detail?.registrySymbol);
  const label = detail?.registrySymbol || detail?.symbol || "";
  const priceSymbols = [...new Set(prices.map((row) => row.priceSymbol).filter(Boolean))];
  const asOf = detail?.asOf
    || parseSummary(summaryDs).find((row) => row.chainId === chainId)?.asOf
    || months.reduce((latest, row) => (row.bucketDate > latest ? row.bucketDate : latest), "");
  const notHeld = !detail && datasetPhase(ctx, "treasury_token_detail") === "complete";
  const valuation = detail ? valuationOf(detail, spot, false) : null;
  const iconUrl = (ctx.state.icon_overlay ?? {})[String(chainId)]?.[token] ?? "";

  // Excluding Gnosis Ltd.: the holders table drops Ltd rows (counted), and the
  // headline value is re-summed over the remaining holders.
  const ltdHolders = holders.filter((holder) => holder.isLtd);
  const shownHolders = view.exLtd ? holders.filter((holder) => !holder.isLtd) : holders;
  const quote = detail && valuation?.kind === "spot" ? spotQuote(spot, chainId, token) : null;
  const holderValue = (holder: (typeof holders)[number]): number | null => {
    if (holder.valueUsd !== null) return holder.valueUsd;
    return quote !== null && holder.units !== null ? holder.units * quote : null;
  };
  const exLtdValue = view.exLtd && valuation && (valuation.kind === "hub" || valuation.kind === "spot")
    ? shownHolders.reduce<number | null>((acc, holder) => {
      const value = holderValue(holder);
      return value === null ? acc : (acc ?? 0) + value;
    }, null)
    : null;

  const facts = useMemo(
    () => holderSeriesFacts(series, { measure, exLtd: view.exLtd }),
    [series, measure, view.exLtd],
  );
  const monthsKnown = datasetPhase(ctx, "treasury_token_months") === "complete";
  const frame = useMemo(() => historyFrame({
    chains: [chainId],
    coverage: monthsKnown ? months : null,
    dataBuckets: factBuckets(facts, chainId),
    startAtData: true,
  }), [chainId, months, monthsKnown, facts]);
  const stack = useMemo(() => stackFacts(facts, frame, { prefix: "wallet" }), [facts, frame]);
  const unitLabel = label || shortAddr(token);
  const stackSpec = useMemo(
    () => (stack.bands.length > 0
      ? valueStackOption({
        buckets: stack.buckets,
        bands: stack.bands,
        gaps: stack.gaps,
        partialNotes: partialNotes(frame.issuesByChain, [chainId]),
        unit: measure === "usd" ? "usd" : { units: unitLabel },
      })
      : null),
    [stack, measure, unitLabel, frame.issuesByChain, chainId],
  );
  const priceSpec = useMemo(
    () => (prices.some((row) => row.priceUsd !== null) ? priceLineOption(prices, label || "price") : null),
    [prices, label],
  );

  const options: ChainOption[] = TREASURY_CHAIN_IDS.map((id) => {
    if (id === chainId) return { chainId: id, enabled: true, title: `${unitLabel} on ${chainName(id)}` };
    const sibling = detail?.siblings.find((entry) => entry.chainId === id);
    return {
      chainId: id,
      enabled: Boolean(sibling),
      title: sibling
        ? `The same registry asset on ${chainName(id)} (${shortAddr(sibling.token)})`
        : `No registry counterpart of this token held on ${chainName(id)}`,
    };
  });

  const collisions = detail?.symbolCollisions ?? null;
  const share = supplyShareText(detail?.supplyShare ?? null);
  const priceText = (() => {
    if (!valuation || valuation.kind === "hidden") return { value: "—", delta: "never valued" };
    if (valuation.kind === "hub") {
      return {
        value: fmtPrice(valuation.price),
        delta: `${valuation.proxy ? "hub peg proxy" : "dbt price hub"} · ${valuation.priceDate || "date unknown"}`,
      };
    }
    if (valuation.kind === "spot") return { value: fmtPrice(valuation.price), delta: `CoinGecko spot · ${fmtStamp(valuation.priceDate) || "capture time unknown"}` };
    if (valuation.kind === "refused") return { value: "—", delta: "spot quote refused as implausible" };
    if (valuation.kind === "retired") return { value: fmtPrice(valuation.price), delta: "mirror — excluded from totals" };
    return { value: "—", delta: "no price" };
  })();

  return (
    <div className="gov-entity gov-trs-entity">
      <div className="gov-trs-identity">
        <div className="gov-trs-identity__kicker">Treasury asset · {chainName(chainId)}</div>
        <div className="gov-trs-identity__name">
          {iconUrl ? <img className="gov-trs-identity__icon" src={iconUrl} alt="" referrerPolicy="no-referrer" loading="lazy" /> : null}
          <span title={trusted ? "Symbol from the reviewed registry" : "On-chain symbol — attacker-controlled text, sanitized; the address is the identity"}>
            {label || shortAddr(token)}
          </span>
          {detail ? <TokenClassBadge tokenClass={tokenClass} spamReason={detail.spamReason} /> : null}
          {notHeld ? <span className="gov-trs-chip">not held{asOf ? ` on ${asOf}` : ""}</span> : null}
        </div>
        {!trusted && detail?.name ? <div className="gov-trs-identity__source">On-chain name: {detail.name}</div> : null}
        {!detail && priceSymbols.length > 0 ? (
          <div className="gov-trs-identity__source">Hub price series: {priceSymbols.join(", ")}</div>
        ) : null}
        <div className="gov-trs-identity__addr">
          <code>{token}</code>
          <button type="button" onClick={() => void navigator.clipboard?.writeText(token)}>Copy</button>
          {explorerUrl(chainId, "token", token) ? (
            <button type="button" onClick={() => ctx.openLink(explorerUrl(chainId, "token", token))}>Explorer ↗</button>
          ) : null}
        </div>
      </div>

      <div className="gov-trs-entitybar">
        <ChainSwitcher
          current={chainId}
          options={options}
          onSelect={(id) => {
            const sibling = detail?.siblings.find((entry) => entry.chainId === id);
            if (sibling) ctx.onEntity("treasury_token", `${sibling.chainId}:${sibling.token}`);
          }}
          ariaLabel="Asset chain"
        />
      </div>

      {tokenClass === "spam" && detail ? (
        <div className="gov-trs-banner gov-trs-banner--spam" role="note">
          <strong>Hidden by default, never valued.</strong>{" "}
          Flagged as {spamReasonText(detail.spamReason).label.toLowerCase()}: {spamReasonText(detail.spamReason).description}
        </div>
      ) : null}
      {tokenClass === "retired_mirror" ? (
        <div className="gov-trs-banner" role="note">
          <strong>Retired mirror.</strong> {TOKEN_CLASS_COPY.retired_mirror.description}
        </div>
      ) : null}

      <DatasetPanel
        title="Holding"
        descriptor={ctx.descriptors.treasury_token_detail}
        groupLoaded
        hydrationPhase={datasetPhase(ctx, "treasury_token_detail")}
        emptyLabel={`Not held on ${asOf || "the latest snapshot"}: the treasury holds none of this token today. Its history and price history below still cover the months it was held.`}
      >
        <div className="gov-trs-kpis">
          <MaKpiGrid>
            <MaKpi
              label={view.exLtd ? "Value ex-Ltd." : "Value"}
              value={view.exLtd && exLtdValue !== null ? fmtUsd(exLtdValue) : valuation?.usd !== null && valuation?.usd !== undefined ? fmtUsd(valuation.usd) : valuation?.kind === "hidden" ? "not valued" : valuation?.kind === "retired" ? "mirror" : "unpriced"}
              delta={valuation?.kind === "spot" ? "CoinGecko spot, today only" : valuation?.kind === "hub" ? "dbt price hub" : "unknown, not zero"}
            />
            <MaKpi label="Units" value={detail?.units === null || detail?.units === undefined ? "—" : fmtUnits(detail.units)} />
            <MaKpi label="Price" value={priceText.value} delta={priceText.delta} />
            <MaKpi label="Holders" value={fmtCount(detail?.walletsHolding ?? null)} delta="treasury wallets" />
            <MaKpi label="Share of supply" value={share.text} delta={share.text === "> supply" ? "balanceOf not trustworthy" : undefined} />
            {tokenClass !== "priced" ? (
              <MaKpi label="Others claiming this symbol" value={fmtCount(collisions)} delta="on this chain" />
            ) : null}
          </MaKpiGrid>
        </div>
        {tokenClass !== "priced" && (collisions ?? 0) > 0 ? (
          <p className="gov-caption">
            {fmtCount(collisions)} other held token{collisions === 1 ? "" : "s"} on {chainName(chainId)} report
            the same symbol. The symbol identifies nothing here; the address does.
          </p>
        ) : null}
        {spotRefused(spot, chainId, token) ? (
          <p className="gov-caption">
            A CoinGecko quote exists for this token but was refused as implausible for this holding, so
            it stays unpriced.
          </p>
        ) : null}
      </DatasetPanel>

      <ValueHistoryPanel
        title="Holdings over time"
        chartId={`gov-trs-token-${chainId}-${measure}`}
        descriptor={ctx.descriptors.treasury_token_holder_series}
        groupLoaded
        phase={datasetPhase(ctx, "treasury_token_holder_series")}
        error={ctx.hydrated.treasury_token_holder_series?.error}
        spec={stackSpec}
        sql={ctx.descriptors.treasury_token_holder_series?.sql}
        sourceModel={SRC}
        coverageKnown={monthsKnown || !ctx.descriptors.treasury_token_holder_series}
        isEmpty={stack.bands.length === 0}
        emptyLabel={measure === "usd" ? "No hub-priced history for this token (switch to Units)." : "No holdings history for this token."}
        meta={(
          <SegmentedControl<"usd" | "units">
            size="sm"
            ariaLabel="Measure"
            value={measure}
            options={[
              { value: "usd", label: "USD" },
              { value: "units", label: "Units" },
            ]}
            onChange={setMeasure}
          />
        )}
        onSeriesClick={(id) => {
          const ref = bandRefFromSeriesId(id);
          if (ref?.kind === "wallet") ctx.onEntity("treasury_wallet", `${chainId}:${ref.wallet}`);
        }}
      >
        <GapNote issues={frame.issuesByChain} chains={[chainId]} />
        <p className="gov-caption">
          Month-end balances by wallet{measure === "usd" ? ", valued with the dbt price hub on each month-end" : ""}.
          {view.exLtd && ltdHolders.length > 0 ? " Gnosis Ltd. is excluded." : ""} Click a band to open the wallet.
        </p>
      </ValueHistoryPanel>

      <DatasetPanel
        title="Price history"
        descriptor={ctx.descriptors.treasury_token_price_history}
        groupLoaded
        hydrationPhase={datasetPhase(ctx, "treasury_token_price_history")}
        emptyLabel={trusted
          ? "No dbt price hub series for this token — it is valued at CoinGecko spot today only, when a plausible quote exists."
          : "No price history: this token is not in the reviewed price registry."}
      >
        {priceSpec ? (
          <ChartCard chartId={`gov-trs-price-${chainId}`} hideId spec={priceSpec} sql={ctx.descriptors.treasury_token_price_history?.sql} sourceModel="dbt price hub (daily USD)" />
        ) : (
          <div className="gov-empty">No price points for this token.</div>
        )}
      </DatasetPanel>

      <DatasetPanel
        title="Wallets holding it"
        descriptor={ctx.descriptors.treasury_token_holders}
        groupLoaded
        hydrationPhase={datasetPhase(ctx, "treasury_token_holders")}
        emptyLabel="No wallet holds a non-zero balance at the latest snapshot."
      >
        <div className="gov-trs-tablewrap">
          <table className="gov-trs-table gov-trs-table--holders">
            <thead>
              <tr>
                <th scope="col">Wallet</th>
                <th scope="col" className="gov-trs-num">Units</th>
                <th scope="col" className="gov-trs-num">Value</th>
                <th scope="col" className="gov-trs-num">Share of treasury position</th>
                <th scope="col" className="gov-trs-col-chev"><span className="gov-trs-sr">Open</span></th>
              </tr>
            </thead>
            <tbody>
              {shownHolders.map((holder) => {
                const open = () => ctx.onEntity("treasury_wallet", `${chainId}:${holder.wallet}`);
                const value = holderValue(holder);
                return (
                  <tr key={holder.wallet} className="gov-trs-row" onClick={open}>
                    <td>
                      <button
                        type="button"
                        className="gov-trs-asset"
                        onClick={(event) => {
                          event.stopPropagation();
                          open();
                        }}
                      >
                        <WalletIdentity address={holder.wallet} label={holder.label} labelSource={holder.labelSource} isLtd={holder.isLtd} />
                      </button>
                    </td>
                    <td className="gov-trs-num gov-trs-mono">
                      {holder.units === null ? (
                        <span className="gov-trs-value--none" title={`Decimals not observed. Raw: ${holder.balanceRaw}`}>raw</span>
                      ) : fmtUnits(holder.units)}
                    </td>
                    <td className="gov-trs-num">
                      <ValueCell
                        kind={valuation?.kind === "hub" || valuation?.kind === "spot" ? (value === null ? "unpriced" : valuation.kind) : valuation?.kind ?? "unpriced"}
                        usd={value}
                        spotAt={spot?.at ?? ""}
                        proxy={valuation?.proxy ?? false}
                      />
                    </td>
                    <td className="gov-trs-num gov-trs-mono">{fmtShare(holder.treasuryShare)}</td>
                    <td className="gov-trs-col-chev" aria-hidden="true">›</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="gov-caption">
          Share is of the <strong>treasury&apos;s own</strong> position in this token, not of its supply.
          {view.exLtd && ltdHolders.length > 0
            ? ` ${fmtCount(ltdHolders.length)} Gnosis Ltd. wallet${ltdHolders.length === 1 ? " is" : "s are"} hidden by the exclusion.`
            : ""}
        </p>
      </DatasetPanel>
    </div>
  );
}
