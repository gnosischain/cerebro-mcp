import type { FlowEdgeRow, FlowNodeRow } from "./flowLayout";

export type MoneyEventKind =
  | "transfer"
  | "mint"
  | "burn"
  | "bridge_attributed"
  | "contract_endpoint";

export type MoneyNodeRole =
  | "seed"
  | "received"
  | "sent"
  | "terminal"
  | "omitted";

export type MoneyWidthBasis = "known_usd" | "token_amount" | "categorical";

export interface MoneyNodeInstance {
  id: string;
  address: string;
  direction: "in" | "out";
  stage: number;
  hop: number;
  role: MoneyNodeRole;
  label: string;
  eventKinds: MoneyEventKind[];
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface MoneyRibbon {
  id: string;
  sourceInstanceId: string;
  targetInstanceId: string;
  sourceAddress: string;
  targetAddress: string;
  direction: "in" | "out";
  hop: number;
  eventKind: MoneyEventKind;
  tokenAddresses: string[];
  symbols: string[];
  knownUsd: number | null;
  normalizedAmount: number | null;
  transferCount: number;
  unpricedRows: number;
  widthBasis: MoneyWidthBasis;
  strokeWidth: number;
  edgeIds: string[];
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
}

export interface ExpansionConnector {
  id: string;
  fromInstanceId: string;
  toInstanceId: string;
  address: string;
  direction: "in" | "out";
  stage: number;
  kind: "analyst_expansion";
  value: null;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface SankeyHopCoverage {
  direction: "in" | "out";
  hop: number;
  shownCounterparties: number;
  loadedCounterparties: number;
  omittedCounterparties: number;
}

export interface MoneySankeyLayout {
  width: number;
  height: number;
  nodes: MoneyNodeInstance[];
  ribbons: MoneyRibbon[];
  connectors: ExpansionConnector[];
  hopCoverage: SankeyHopCoverage[];
}

export interface MoneySankeyOptions {
  maxCounterpartiesPerHop?: number;
  singleTokenMode?: boolean;
  width?: number;
}

export const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";
export const DEAD_ADDRESS = "0x000000000000000000000000000000000000dead";
export const STRUCTURAL_TERMINALS = new Set([ZERO_ADDRESS, DEAD_ADDRESS]);

const NODE_WIDTH = 10;
const NODE_HEIGHT = 18;
const ROLE_GAP = 18;
const COLUMN_MARGIN = 74;
const ROW_GAP = 34;
const TOP_MARGIN = 42;
const BOTTOM_MARGIN = 36;

function normalizedAddress(value: string): string {
  return value.trim().toLowerCase();
}

export function isStructuralTerminal(value: string): boolean {
  return STRUCTURAL_TERMINALS.has(normalizedAddress(value));
}

export function moneyEventKind(
  edge: FlowEdgeRow,
  nodeById: ReadonlyMap<string, FlowNodeRow>,
): MoneyEventKind {
  if (isStructuralTerminal(edge.source) || edge.edgeClass === "mint") return "mint";
  if (isStructuralTerminal(edge.target) || edge.edgeClass === "burn") return "burn";
  if (edge.edgeClass === "bridge" || edge.edgeClass === "bridge_attributed") {
    return "bridge_attributed";
  }
  const source = nodeById.get(edge.source);
  const target = nodeById.get(edge.target);
  if (
    source?.flags.includes("token_contract") ||
    target?.flags.includes("token_contract")
  ) {
    return "contract_endpoint";
  }
  return "transfer";
}

function edgeStages(
  sourceRank: number,
  targetRank: number,
): { direction: "in" | "out"; sourceStage: number; targetStage: number; hop: number } {
  const incoming = sourceRank < 0 || (targetRank <= 0 && sourceRank < targetRank);
  if (incoming) {
    const targetStage = Math.min(0, targetRank);
    const sourceStage = sourceRank < targetStage ? sourceRank : targetStage - 1;
    return { direction: "in", sourceStage, targetStage, hop: Math.abs(sourceStage) };
  }
  const sourceStage = Math.max(0, sourceRank);
  const targetStage = targetRank > sourceStage ? targetRank : sourceStage + 1;
  return { direction: "out", sourceStage, targetStage, hop: targetStage };
}

function roleForEndpoint(
  address: string,
  endpoint: "source" | "target",
  stage: number,
  eventKind: MoneyEventKind,
  seeds: ReadonlySet<string>,
  node: FlowNodeRow | undefined,
): MoneyNodeRole {
  if (seeds.has(address) && stage === 0) return "seed";
  if (
    isStructuralTerminal(address) ||
    node?.flags.includes("structural_terminal") ||
    node?.flags.includes("token_contract") ||
    (eventKind === "bridge_attributed" && endpoint === "target")
  ) {
    return "terminal";
  }
  return endpoint === "source" ? "sent" : "received";
}

function instanceId(
  address: string,
  direction: "in" | "out",
  stage: number,
  role: MoneyNodeRole,
): string {
  if (role === "seed") return `${address}@seed@${stage}@seed`;
  return `${address}@${direction}@${stage}@${role}`;
}

function labelForNode(address: string, node: FlowNodeRow | undefined): string {
  if (address === ZERO_ADDRESS) return "Zero address";
  if (address === DEAD_ADDRESS) return "Dead address";
  return node?.label || `${address.slice(0, 8)}…${address.slice(-6)}`;
}

interface RibbonAccumulator {
  id: string;
  sourceInstanceId: string;
  targetInstanceId: string;
  sourceAddress: string;
  targetAddress: string;
  direction: "in" | "out";
  hop: number;
  eventKind: MoneyEventKind;
  tokenAddresses: Set<string>;
  symbols: Set<string>;
  knownUsd: number;
  pricedEdges: number;
  normalizedAmount: number;
  amountEdges: number;
  transferCount: number;
  unpricedRows: number;
  edgeIds: string[];
}

function ribbonSort(a: RibbonAccumulator, b: RibbonAccumulator): number {
  const aUsd = a.pricedEdges ? a.knownUsd : Number.NEGATIVE_INFINITY;
  const bUsd = b.pricedEdges ? b.knownUsd : Number.NEGATIVE_INFINITY;
  return (
    bUsd - aUsd ||
    b.transferCount - a.transferCount ||
    a.id.localeCompare(b.id)
  );
}

/**
 * Build a segmented, deterministic Sankey-style layout.
 *
 * Each intermediary gets separate ``received`` and ``sent`` instances.  A
 * valueless expansion connector joins those instances without participating
 * in ribbon widths, so the view never asserts that inbound fungible units
 * continued into the next observed aggregate.
 */
export function buildMoneySankeyLayout(
  flowNodes: FlowNodeRow[],
  flowEdges: FlowEdgeRow[],
  seedAddresses: string[],
  options: MoneySankeyOptions = {},
): MoneySankeyLayout {
  const width = Math.max(640, options.width ?? 1040);
  const maxCounterparties = Math.max(1, options.maxCounterpartiesPerHop ?? 40);
  const nodeById = new Map(flowNodes.map((node) => [node.id, node]));
  const seeds = new Set(seedAddresses.map(normalizedAddress));
  const accumulators = new Map<string, RibbonAccumulator>();

  for (const edge of [...flowEdges].sort((a, b) => a.id.localeCompare(b.id))) {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    if (!source || !target) continue;
    const eventKind = moneyEventKind(edge, nodeById);
    const { direction, sourceStage, targetStage, hop } = edgeStages(
      source.hopRank,
      target.hopRank,
    );
    const sourceRole = roleForEndpoint(
      edge.source,
      "source",
      sourceStage,
      eventKind,
      seeds,
      source,
    );
    const targetRole = roleForEndpoint(
      edge.target,
      "target",
      targetStage,
      eventKind,
      seeds,
      target,
    );
    const sourceInstanceId = instanceId(edge.source, direction, sourceStage, sourceRole);
    const targetInstanceId = instanceId(edge.target, direction, targetStage, targetRole);
    const key = [sourceInstanceId, targetInstanceId, eventKind].join("|");
    const acc = accumulators.get(key) ?? {
      id: key,
      sourceInstanceId,
      targetInstanceId,
      sourceAddress: edge.source,
      targetAddress: edge.target,
      direction,
      hop,
      eventKind,
      tokenAddresses: new Set<string>(),
      symbols: new Set<string>(),
      knownUsd: 0,
      pricedEdges: 0,
      normalizedAmount: 0,
      amountEdges: 0,
      transferCount: 0,
      unpricedRows: 0,
      edgeIds: [],
    };
    acc.tokenAddresses.add(edge.tokenAddress);
    if (edge.symbol) acc.symbols.add(edge.symbol);
    if (edge.amountUsd != null) {
      acc.knownUsd += edge.amountUsd;
      acc.pricedEdges += 1;
    }
    if (edge.amount != null) {
      acc.normalizedAmount += edge.amount;
      acc.amountEdges += 1;
    }
    acc.transferCount += edge.transferCount;
    acc.unpricedRows += edge.unknownUsdRows;
    acc.edgeIds.push(edge.id);
    accumulators.set(key, acc);
  }

  const buckets = new Map<string, RibbonAccumulator[]>();
  for (const acc of accumulators.values()) {
    const key = `${acc.direction}:${acc.hop}`;
    const bucket = buckets.get(key) ?? [];
    bucket.push(acc);
    buckets.set(key, bucket);
  }

  const visibleAccumulators: RibbonAccumulator[] = [];
  const hopCoverage: SankeyHopCoverage[] = [];
  for (const [bucketKey, bucket] of [...buckets].sort(([a], [b]) => a.localeCompare(b))) {
    bucket.sort(ribbonSort);
    const [directionRaw, hopRaw] = bucketKey.split(":");
    const direction = directionRaw as "in" | "out";
    const hop = Number(hopRaw);
    const loaded = new Set<string>();
    const shown = new Set<string>();
    for (const acc of bucket) {
      const counterparty = direction === "out" ? acc.targetAddress : acc.sourceAddress;
      const structural = isStructuralTerminal(counterparty);
      if (!structural) loaded.add(counterparty);
      if (structural || shown.has(counterparty) || shown.size < maxCounterparties) {
        visibleAccumulators.push(acc);
        if (!structural) shown.add(counterparty);
      }
    }
    hopCoverage.push({
      direction,
      hop,
      shownCounterparties: shown.size,
      loadedCounterparties: loaded.size,
      omittedCounterparties: Math.max(0, loaded.size - shown.size),
    });
  }

  const nodeSpecs = new Map<
    string,
    Omit<MoneyNodeInstance, "x" | "y" | "width" | "height">
  >();
  for (const acc of visibleAccumulators) {
    const sourceNode = nodeById.get(acc.sourceAddress);
    const targetNode = nodeById.get(acc.targetAddress);
    const sourceParts = acc.sourceInstanceId.split("@");
    const targetParts = acc.targetInstanceId.split("@");
    const sourceRole = sourceParts[sourceParts.length - 1] as MoneyNodeRole;
    const targetRole = targetParts[targetParts.length - 1] as MoneyNodeRole;
    const sourceStage = Number(sourceParts[sourceParts.length - 2]);
    const targetStage = Number(targetParts[targetParts.length - 2]);
    const addNode = (
      id: string,
      address: string,
      role: MoneyNodeRole,
      stage: number,
      node: FlowNodeRow | undefined,
    ) => {
      const current = nodeSpecs.get(id);
      const kinds = new Set(current?.eventKinds ?? []);
      kinds.add(acc.eventKind);
      nodeSpecs.set(id, {
        id,
        address,
        direction: acc.direction,
        stage,
        hop: Math.abs(stage),
        role,
        label: labelForNode(address, node),
        eventKinds: [...kinds].sort(),
      });
    };
    addNode(acc.sourceInstanceId, acc.sourceAddress, sourceRole, sourceStage, sourceNode);
    addNode(acc.targetInstanceId, acc.targetAddress, targetRole, targetStage, targetNode);
  }

  const stages = [...new Set([...nodeSpecs.values()].map((node) => node.stage))].sort(
    (a, b) => a - b,
  );
  const addressesByStage = new Map<number, string[]>();
  for (const stage of stages) {
    const addresses = [...new Set(
      [...nodeSpecs.values()]
        .filter((node) => node.stage === stage)
        .map((node) => node.address),
    )].sort((a, b) => {
      const aSeed = seeds.has(a) ? 0 : 1;
      const bSeed = seeds.has(b) ? 0 : 1;
      return aSeed - bSeed || a.localeCompare(b);
    });
    addressesByStage.set(stage, addresses);
  }
  const maxRows = Math.max(1, ...[...addressesByStage.values()].map((rows) => rows.length));
  const height = Math.max(320, TOP_MARGIN + BOTTOM_MARGIN + (maxRows - 1) * ROW_GAP);
  const stageX = new Map<number, number>();
  stages.forEach((stage, index) => {
    const x =
      stages.length === 1
        ? width / 2
        : COLUMN_MARGIN + ((width - COLUMN_MARGIN * 2) * index) / (stages.length - 1);
    stageX.set(stage, x);
  });

  const positionedNodes: MoneyNodeInstance[] = [...nodeSpecs.values()]
    .map((spec) => {
      const rows = addressesByStage.get(spec.stage) ?? [];
      const row = Math.max(0, rows.indexOf(spec.address));
      const usedHeight = Math.max(0, (rows.length - 1) * ROW_GAP);
      const y = (height - usedHeight) / 2 + row * ROW_GAP;
      const roleOffset =
        spec.role === "received" ? -ROLE_GAP / 2 : spec.role === "sent" ? ROLE_GAP / 2 : 0;
      return {
        ...spec,
        x: (stageX.get(spec.stage) ?? width / 2) + roleOffset,
        y,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      };
    })
    .sort((a, b) => a.stage - b.stage || a.y - b.y || a.id.localeCompare(b.id));
  const positionedById = new Map(positionedNodes.map((node) => [node.id, node]));

  const provisional = visibleAccumulators.map((acc) => {
    const tokenAddresses = [...acc.tokenAddresses].sort();
    const singleTokenAmount = tokenAddresses.length === 1 && acc.amountEdges > 0;
    const widthBasis: MoneyWidthBasis =
      options.singleTokenMode && singleTokenAmount
        ? "token_amount"
        : acc.pricedEdges > 0
          ? "known_usd"
          : "categorical";
    const knownUsd = acc.pricedEdges > 0 ? acc.knownUsd : null;
    const normalizedAmount = singleTokenAmount ? acc.normalizedAmount : null;
    return {
      ...acc,
      tokenAddresses,
      symbols: [...acc.symbols].sort(),
      knownUsd,
      normalizedAmount,
      widthBasis,
      edgeIds: [...acc.edgeIds].sort(),
    };
  });
  const maxUsd = Math.max(0, ...provisional.map((r) => r.knownUsd ?? 0));
  const maxToken = Math.max(0, ...provisional.map((r) => r.normalizedAmount ?? 0));

  const ribbons: MoneyRibbon[] = provisional
    .map((ribbon) => {
      const source = positionedById.get(ribbon.sourceInstanceId);
      const target = positionedById.get(ribbon.targetInstanceId);
      if (!source || !target) return null;
      const basisValue =
        ribbon.widthBasis === "known_usd"
          ? ribbon.knownUsd ?? 0
          : ribbon.widthBasis === "token_amount"
            ? ribbon.normalizedAmount ?? 0
            : 0;
      const basisMax = ribbon.widthBasis === "known_usd" ? maxUsd : maxToken;
      const strokeWidth =
        ribbon.widthBasis === "categorical" || basisMax <= 0
          ? 3
          : 2 + (basisValue / basisMax) * 24;
      return {
        id: ribbon.id,
        sourceInstanceId: ribbon.sourceInstanceId,
        targetInstanceId: ribbon.targetInstanceId,
        sourceAddress: ribbon.sourceAddress,
        targetAddress: ribbon.targetAddress,
        direction: ribbon.direction,
        hop: ribbon.hop,
        eventKind: ribbon.eventKind,
        tokenAddresses: ribbon.tokenAddresses,
        symbols: ribbon.symbols,
        knownUsd: ribbon.knownUsd,
        normalizedAmount: ribbon.normalizedAmount,
        transferCount: ribbon.transferCount,
        unpricedRows: ribbon.unpricedRows,
        widthBasis: ribbon.widthBasis,
        strokeWidth,
        edgeIds: ribbon.edgeIds,
        sourceX: source.x + source.width / 2,
        sourceY: source.y,
        targetX: target.x - target.width / 2,
        targetY: target.y,
      };
    })
    .filter((ribbon): ribbon is MoneyRibbon => ribbon !== null)
    .sort((a, b) => a.id.localeCompare(b.id));

  const connectors: ExpansionConnector[] = [];
  const byAddressStage = new Map<string, MoneyNodeInstance[]>();
  for (const node of positionedNodes) {
    const key = `${node.address}@${node.direction}@${node.stage}`;
    const group = byAddressStage.get(key) ?? [];
    group.push(node);
    byAddressStage.set(key, group);
  }
  for (const [key, group] of byAddressStage) {
    const received = group.find((node) => node.role === "received");
    const sent = group.find((node) => node.role === "sent");
    if (!received || !sent) continue;
    connectors.push({
      id: `expand:${key}`,
      fromInstanceId: received.id,
      toInstanceId: sent.id,
      address: received.address,
      direction: received.direction,
      stage: received.stage,
      kind: "analyst_expansion",
      value: null,
      x1: received.x + received.width / 2,
      y1: received.y,
      x2: sent.x - sent.width / 2,
      y2: sent.y,
    });
  }

  return {
    width,
    height,
    nodes: positionedNodes,
    ribbons,
    connectors: connectors.sort((a, b) => a.id.localeCompare(b.id)),
    hopCoverage,
  };
}
