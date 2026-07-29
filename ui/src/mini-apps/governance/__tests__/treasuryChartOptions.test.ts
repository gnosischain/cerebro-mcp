// Treasury chart builders. The assertions here are the frozen conventions plus
// the two treasury-specific correctness rules: a unit axis must never be
// labelled as a currency (this plane has no historical prices), and anything a
// builder drops must come back out so the caller can name it.

import type { EChartsOption } from "echarts";
import { describe, expect, it } from "vitest";

import {
  LTD_SERIES_COLOR,
  breadthOption,
  compositionTreemapOption,
  concentrationBarOption,
  constantPriceStackOption,
  timeSeriesLineOption,
  walletStackOption,
} from "../model/chartOptions";
import { fmtUsdCompact, stackedSeriesOption, treemapOption } from "../../shared/chartOptions";

interface SeriesLike {
  name?: string;
  type?: string;
  data?: unknown;
  itemStyle?: { color?: string };
  label?: { show?: boolean; formatter?: () => string };
}

interface AxisLike {
  name?: string;
  type?: string;
  show?: boolean;
}

interface LooseSpec {
  dataZoom?: unknown;
  series?: SeriesLike[];
  xAxis?: AxisLike;
  yAxis?: AxisLike | AxisLike[];
  legend?: { show?: boolean };
  _cerebro_height?: string;
}

const spec = (option: EChartsOption): LooseSpec => option as unknown as LooseSpec;

const series = (option: EChartsOption): SeriesLike[] => spec(option).series ?? [];

const names = (option: EChartsOption): string[] => series(option).map((entry) => String(entry.name));

function zoomTypes(option: EChartsOption): string[] {
  const zoom = spec(option).dataZoom;
  if (!zoom) return [];
  const list = Array.isArray(zoom) ? zoom : [zoom];
  return list.map((entry) => String((entry as { type?: string }).type));
}

function yAxisNames(option: EChartsOption): string[] {
  const axis = spec(option).yAxis;
  if (!axis) return [];
  const list = Array.isArray(axis) ? axis : [axis];
  return list.map((entry) => String(entry.name ?? ""));
}

/** Monthly buckets, in the YYYY-MM-01 shape the history datasets use. */
const BUCKETS = ["2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"];

const points = (values: Array<number | null>) =>
  BUCKETS.map((bucket, index) => ({ bucket, units: values[index] ?? null }));

/** Address-shaped keys: the builders key on identity, never on the symbol. */
const addr = (n: number) => `0x${String(n).padStart(2, "0").repeat(20)}`;

const CHAIN_ROWS = BUCKETS.map((bucket, index) => ({
  bucket,
  gno_units: 1000 + index * 10,
  gno_units_ex_ltd: 700 + index * 5,
  tokens_held: 200 + index,
  tokens_named: 180 + index,
  positions: 400 + index * 3,
}));

describe("frozen conventions", () => {
  const built: Array<[string, EChartsOption]> = [
    ["timeSeriesLineOption", timeSeriesLineOption(CHAIN_ROWS, {
      xField: "bucket",
      series: [{ field: "gno_units", label: "GNO" }],
      unitLabel: "GNO",
    })],
    ["breadthOption", breadthOption(CHAIN_ROWS, {
      xField: "bucket",
      namedField: "tokens_named",
      heldField: "tokens_held",
      positionsField: "positions",
    })],
    ["constantPriceStackOption", constantPriceStackOption([
      { token: addr(1), label: "GNO", price: 120, points: points([10, 11, 12, 13]) },
    ])],
    ["walletStackOption", walletStackOption(
      [{ wallet: addr(9), label: "DAO", points: points([5, 6, 7, 8]) }],
      { unitLabel: "GNO" },
    )],
    ["compositionTreemapOption", compositionTreemapOption([
      { token: addr(1), label: "GNO", usd: 84_470_000 },
    ])],
    ["concentrationBarOption", concentrationBarOption({
      leadLabel: "GNO",
      leadUsd: 84_470_000,
      restUsd: 20_428_402,
    })],
  ];

  it("never emits a slider dataZoom", () => {
    for (const [label, option] of built) {
      const types = zoomTypes(option);
      expect(types, `${label} emitted a non-inside dataZoom`).not.toContain("slider");
      for (const type of types) expect(type, label).toBe("inside");
    }
  });

  it("only the constant-price revaluation names a currency on its axis", () => {
    for (const [label, option] of built) {
      const currency = yAxisNames(option).filter((name) => /usd|\$/i.test(name));
      if (label === "constantPriceStackOption") {
        // ...and it discloses the revaluation in the axis name itself.
        expect(currency).toEqual(["USD at constant price"]);
      } else {
        expect(currency, `${label} labelled a unit axis as currency`).toEqual([]);
      }
    }
  });

  it("uses only tree-shaken-registered series types", () => {
    const registered = new Set(["line", "bar", "pie", "scatter", "heatmap", "treemap", "sankey", "graph"]);
    for (const [label, option] of built) {
      for (const entry of series(option)) {
        expect(registered.has(String(entry.type)), `${label} used ${entry.type}`).toBe(true);
      }
    }
  });
});

describe("timeSeriesLineOption", () => {
  it("names the y axis with the unit it was given", () => {
    const option = timeSeriesLineOption(CHAIN_ROWS, {
      xField: "bucket",
      series: [{ field: "gno_units", label: "GNO" }, { field: "gno_units_ex_ltd", label: "GNO ex-Ltd", dashed: true }],
      unitLabel: "GNO",
    });
    expect(yAxisNames(option)).toEqual(["GNO"]);
    expect(names(option)).toEqual(["GNO", "GNO ex-Ltd"]);
  });

  it("rejects a currency unit label — this plane has no historical prices", () => {
    for (const unitLabel of ["USD", "Value (USD)", "$ value", "usd", "EUR"]) {
      expect(
        () => timeSeriesLineOption(CHAIN_ROWS, { xField: "bucket", series: [], unitLabel }),
        unitLabel,
      ).toThrow(/no historical prices/);
    }
  });

  it("accepts a token symbol that merely contains 'usd'", () => {
    expect(() =>
      timeSeriesLineOption(CHAIN_ROWS, { xField: "bucket", series: [], unitLabel: "USDC" })).not.toThrow();
  });

  it("keeps a missing snapshot null instead of plunging the line to zero", () => {
    const option = timeSeriesLineOption(
      [{ bucket: "2026-03-01", gno_units: 1000 }, { bucket: "2026-04-01", gno_units: null }],
      { xField: "bucket", series: [{ field: "gno_units", label: "GNO" }], unitLabel: "GNO" },
    );
    expect(series(option)[0].data).toEqual([1000, null]);
  });
});

describe("breadthOption", () => {
  it("derives the unnamed band and puts positions on a second axis", () => {
    const option = breadthOption(CHAIN_ROWS, {
      xField: "bucket",
      namedField: "tokens_named",
      heldField: "tokens_held",
      positionsField: "positions",
    });
    expect(names(option)).toEqual(["Named", "Unnamed", "Positions"]);
    expect(series(option)[1].data).toEqual([20, 20, 20, 20]);
    expect(yAxisNames(option)).toEqual(["tokens", "positions"]);
  });

  it("leaves the derived band null when either input is missing", () => {
    const option = breadthOption(
      [{ bucket: "2026-03-01", tokens_named: null, tokens_held: 200 }],
      { xField: "bucket", namedField: "tokens_named", heldField: "tokens_held" },
    );
    // Not 200: an unknown named count makes the difference unknowable, and
    // Number(null) would have claimed every token was unnamed.
    expect(series(option)[1].data).toEqual([null]);
  });
});

describe("constantPriceStackOption", () => {
  const priced = (n: number, label: string, price: number, units: number) => ({
    token: addr(n),
    label,
    price,
    points: points([units, units, units, units]),
  });

  it("excludes unpriced tokens and reports them instead of valuing them at zero", () => {
    const option = constantPriceStackOption([
      priced(1, "GNO", 120, 700),
      { token: addr(2), label: "USDC", price: null, points: points([1e6, 1e6, 1e6, 1e6]) },
      { token: addr(3), label: "USDC", points: points([5e5, 5e5, 5e5, 5e5]) },
    ]);
    expect(option._cerebro_excluded).toEqual(["USDC", "USDC"]);
    expect(names(option)).toEqual(["GNO"]);
  });

  it("caps the band count at maxSeries, folding the tail into a counted residual", () => {
    const many = Array.from({ length: 9 }, (_, index) => priced(index + 1, `T${index}`, 10, 100 - index));
    const option = constantPriceStackOption(many);
    expect(series(option)).toHaveLength(5);
    // The residual names how much identity it swallowed.
    expect(names(option)[4]).toBe("Other (+5)");
  });

  it("honours an explicit maxSeries", () => {
    const many = Array.from({ length: 9 }, (_, index) => priced(index + 1, `T${index}`, 10, 100 - index));
    expect(series(constantPriceStackOption(many, { maxSeries: 3 }))).toHaveLength(3);
  });

  it("keys on the address so two tokens claiming one symbol stay separate", () => {
    const option = constantPriceStackOption([
      { token: addr(2), label: "USDC", price: 1, points: points([100, 100, 100, 100]) },
      { token: addr(3), label: "USDC", price: 1, points: points([50, 50, 50, 50]) },
    ]);
    expect(series(option)).toHaveLength(2);
    // Colliding labels get the address appended — the same disambiguation
    // TokenIdentity applies when a symbol is not unique in view.
    for (const name of names(option)) expect(name).toMatch(/^USDC 0x/);
  });

  it("takes prices from the overlay map keyed by lowercase address", () => {
    const option = constantPriceStackOption(
      [{ token: addr(1).toUpperCase(), label: "GNO", points: points([2, 2, 2, 2]) }],
      { prices: { [addr(1)]: 120 } },
    );
    expect(option._cerebro_excluded).toEqual([]);
    expect(series(option)[0].data).toEqual([240, 240, 240, 240]);
  });

  it("orders buckets chronologically even when a token appears late", () => {
    const option = constantPriceStackOption([
      { token: addr(1), label: "GNO", price: 1, points: [{ bucket: "2026-05-01", units: 1 }] },
      { token: addr(2), label: "COW", price: 1, points: [{ bucket: "2026-03-01", units: 1 }] },
    ]);
    expect(spec(option).xAxis).toMatchObject({ data: ["2026-03-01", "2026-05-01"] });
  });
});

describe("walletStackOption", () => {
  const wallet = (n: number, units: number, isLtd = false) => ({
    wallet: addr(n),
    isLtd,
    points: points([units, units, units, units]),
  });

  it("gives Ltd. a fixed hue and a suffix so the split reads without the legend", () => {
    const option = walletStackOption(
      [wallet(1, 700), wallet(2, 300, true)],
      { unitLabel: "GNO" },
    );
    const ltd = series(option).find((entry) => String(entry.name).endsWith("(Ltd.)"));
    expect(ltd, "no Ltd. series").toBeDefined();
    expect(ltd?.itemStyle?.color).toBe(LTD_SERIES_COLOR);
    expect(LTD_SERIES_COLOR).toBe("#F5B14C");
  });

  it("sorts the server-folded 'other' tail last however heavy it is", () => {
    const option = walletStackOption(
      [
        { wallet: "other", points: points([9_000, 9_000, 9_000, 9_000]) },
        wallet(1, 700),
        wallet(2, 300, true),
      ],
      { unitLabel: "GNO" },
    );
    const plotted = names(option);
    expect(plotted[plotted.length - 1]).toBe("Other");
    expect(plotted).toHaveLength(3);
  });

  it("folds overflow into the existing tail without exceeding maxSeries", () => {
    const wallets = Array.from({ length: 8 }, (_, index) => wallet(index + 1, 100 - index));
    const option = walletStackOption(
      [...wallets, { wallet: "other", points: points([1, 1, 1, 1]) }],
      { unitLabel: "GNO" },
    );
    expect(series(option)).toHaveLength(5);
    expect(names(option)[4]).toBe("Other (+4)");
  });

  it("names the y axis with the token unit, never a currency", () => {
    // A stablecoin's own unit stack legitimately reads "USDC" — the rule is
    // about asserting dollar VALUE, not about the letters in a ticker.
    const option = walletStackOption([wallet(1, 5)], { unitLabel: "USDC" });
    expect(yAxisNames(option)).toEqual(["USDC"]);
  });

  it("falls back to a unit-free axis name when the symbol is unusable", () => {
    const option = walletStackOption([wallet(1, 5)], { unitLabel: "‮​" });
    expect(yAxisNames(option)).toEqual(["units"]);
  });
});

describe("compositionTreemapOption", () => {
  it("carries the token address as each node id for drill-down", () => {
    const option = compositionTreemapOption([
      { token: addr(1).toUpperCase(), label: "GNO", usd: 84_470_000 },
      { token: addr(4), label: "COW", usd: 6_920_000 },
    ]);
    const nodes = series(option)[0].data as Array<{ id?: string; name: string; value: number }>;
    expect(nodes.map((node) => node.id)).toEqual([addr(1), addr(4)]);
    expect(spec(option)._cerebro_height).toBe("380px");
  });

  it("reports holdings it cannot draw rather than dropping them silently", () => {
    const option = compositionTreemapOption([
      { token: addr(1), label: "GNO", usd: 84_470_000 },
      { token: addr(2), label: "USDC", usd: 0 },
      { token: addr(3), label: "Visit [aave-sr.xyz] and claim", usd: Number.NaN },
    ]);
    expect(option._cerebro_dropped).toHaveLength(2);
    expect(option._cerebro_dropped[0]).toBe("USDC");
    // Long phishing "names" are capped by sanitizeSymbol before display.
    expect(option._cerebro_dropped[1].length).toBeLessThanOrEqual(14);
    expect(series(option)[0].data).toHaveLength(1);
  });
});

describe("concentrationBarOption", () => {
  it("states the dominant share on the bar", () => {
    const option = concentrationBarOption({
      leadLabel: "GNO",
      leadUsd: 84_470_000,
      restUsd: 20_428_402,
    });
    const [lead, rest] = series(option);
    expect(lead.label?.formatter?.()).toBe("GNO 80.5%");
    expect(rest.label?.formatter?.()).toBe("Everything else 19.5%");
    expect(rest.label?.show).toBe(true);
    expect(spec(option).xAxis?.show).toBe(false);
  });

  it("hides a band label too thin to hold it", () => {
    const option = concentrationBarOption({ leadLabel: "GNO", leadUsd: 99, restUsd: 1 });
    const [, rest] = series(option);
    // The legend and tooltip still carry it — only the in-bar label, which
    // would spill across its neighbour, is withheld.
    expect(rest.label?.show).toBe(false);
    expect(rest.label?.formatter?.()).toBe("Everything else 1.0%");
  });

  it("renders a dash, never 0%, when there is no priced NAV", () => {
    const option = concentrationBarOption({ leadLabel: "GNO", leadUsd: 0, restUsd: 0 });
    const [lead] = series(option);
    expect(lead.label?.show).toBe(false);
    expect(lead.label?.formatter?.()).toBe("GNO —");
  });
});

describe("shared builders", () => {
  it("treemap nodes carry ids only when the caller made them clickable", () => {
    const items = [{ id: addr(1), name: "GNO", value: 10 }];
    const clickable = series(treemapOption(items, { clickable: true }))[0].data as Array<{ id?: string }>;
    const inert = series(treemapOption(items))[0].data as Array<{ id?: string }>;
    expect(clickable[0].id).toBe(addr(1));
    expect(inert[0]).not.toHaveProperty("id");
  });

  it("caps bands at five by default", () => {
    const rows = Array.from({ length: 8 }, (_, index) => ({
      bucket: "2026-03-01",
      key: `k${index}`,
      value: 100 - index,
    }));
    const option = stackedSeriesOption(rows, { xField: "bucket", valueField: "value", seriesField: "key" });
    expect(series(option)).toHaveLength(5);
    expect(zoomTypes(option)).toEqual(["inside"]);
  });

  it("normalizes each bucket to 100% in share mode", () => {
    const rows = [
      { bucket: "2026-03-01", key: "a", value: 3 },
      { bucket: "2026-03-01", key: "b", value: 1 },
    ];
    const option = stackedSeriesOption(rows, {
      xField: "bucket", valueField: "value", seriesField: "key", mode: "share",
    });
    expect(series(option).map((entry) => entry.data)).toEqual([[75], [25]]);
  });

  it("fmtUsdCompact dashes on null rather than coercing it to $0", () => {
    expect(fmtUsdCompact(null)).toBe("—");
    expect(fmtUsdCompact(undefined)).toBe("—");
    expect(fmtUsdCompact(Number.NaN)).toBe("—");
    expect(fmtUsdCompact(104_898_402)).toBe("$104.90M");
    expect(fmtUsdCompact(0)).toBe("$0.00");
  });
});

describe("constantPriceStackOption — the empty-chart regression", () => {
  // Shipped bug: the section passed no `prices` arg AND TokenSeries carried no
  // `price`, so every series fell through to null and was excluded. The chart
  // rendered empty while its caption named those very tokens as "excluded for
  // want of a price". Both routes to a price are pinned here.
  const points = [
    { bucket: "2026-06-01", units: 10 },
    { bucket: "2026-07-01", units: 12 },
  ];

  it("revalues a series whose price rides ON the series", () => {
    const option = constantPriceStackOption([
      { token: "0xaaa", label: "AAA", price: 3, points },
    ]);
    expect(option._cerebro_excluded).toEqual([]);
    expect((option.series as unknown[]).length).toBe(1);
  });

  it("revalues a series priced through the args map", () => {
    const option = constantPriceStackOption(
      [{ token: "0xAAA", label: "AAA", points }],
      { prices: { "0xaaa": 3 } },
    );
    expect(option._cerebro_excluded).toEqual([]);
    expect((option.series as unknown[]).length).toBe(1);
  });

  it("still excludes — and REPORTS — a genuinely unpriced series", () => {
    const option = constantPriceStackOption([
      { token: "0xaaa", label: "AAA", price: 3, points },
      { token: "0xbbb", label: "BBB", price: null, points },
    ]);
    // Absence is the honest outcome for an unpriced token; silence is not.
    expect(option._cerebro_excluded).toEqual(["BBB"]);
    expect((option.series as unknown[]).length).toBe(1);
  });
});
