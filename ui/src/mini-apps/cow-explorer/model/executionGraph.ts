import type { ExecutionGraphEdge, ExecutionGraphModel, ExecutionGraphNode, ExecutionNodeKind } from "../types";
import { rowsToObjects, type RowDataset } from "./parseRows";

function short(value: unknown, left = 6, right = 4): string {
  const text = String(value ?? "");
  return text.length > left + right + 1 ? `${text.slice(0, left)}…${text.slice(-right)}` : text;
}

function amount(row: Record<string, unknown>, side: "sell" | "buy"): string {
  const normalized = row[`${side}_amount`];
  if (normalized !== null && normalized !== undefined && normalized !== "") return Number(normalized).toLocaleString(undefined, { maximumSignificantDigits: 7 });
  const raw = String(row[`${side}_amount_raw`] ?? "");
  return raw ? `${short(raw, 8, 4)} raw` : "";
}

export function buildTransactionExecutionGraph(datasets: Record<string, RowDataset | undefined>, fallbackTx = ""): ExecutionGraphModel {
  const detail = rowsToObjects(datasets.transaction_detail)[0] ?? {};
  const trades = rowsToObjects(datasets.transaction_trades).slice(0, 40);
  const allInteractions = rowsToObjects(datasets.transaction_interactions);
  const interactions = allInteractions.slice(0, 12);
  const competitions = rowsToObjects(datasets.transaction_competition);
  const tx = String(detail.tx_hash ?? fallbackTx);
  if (!tx && trades.length === 0 && interactions.length === 0) return { nodes: [], edges: [] };

  const nodes = new Map<string, ExecutionGraphNode>();
  const edges = new Map<string, ExecutionGraphEdge>();
  const addNode = (id: string, kind: ExecutionNodeKind, label: string, x: number, y: number, extra: Partial<ExecutionGraphNode> = {}) => {
    if (!nodes.has(id)) nodes.set(id, { id, kind, label, x, y, evidenceSource: "cow_db", ...extra });
  };
  const addEdge = (source: string, target: string, relation: string, evidenceSource: string, scope: "direct" | "auction_scoped" = "direct", label = relation) => {
    const id = `${source}|${target}|${relation}`;
    if (!edges.has(id)) edges.set(id, { id, source, target, relation, label, evidenceSource, scope });
  };

  const txId = `transaction:${tx || "selected"}`;
  const transactionY = trades.length ? 100 + (Math.min(trades.length, 12) - 1) * 65 : 180;
  addNode(txId, "transaction", "Settlement", 700, transactionY, { subtitle: short(tx || "transaction"), entityType: "transaction", identifier: tx, evidenceSource: "settlements_canonical" });

  const executor = String(detail.settlement_executor ?? "");
  if (executor) {
    const id = `settlement_executor:${executor}`;
    addNode(id, "actor", "Settlement executor", 1060, 40, { subtitle: short(executor), entityType: "solver", identifier: executor, role: "settlement_executor", evidenceSource: "settlements_canonical" });
    addEdge(txId, id, "Settlement event", "settlements_canonical");
  }

  trades.forEach((trade, index) => {
    const order = String(trade.order_uid ?? `fill-${index}`);
    const fillId = `fill:${trade.log_index ?? index}:${order}`;
    const orderId = `order:${order}`;
    const sell = String(trade.sell_token ?? "unknown-sell-token");
    const buy = String(trade.buy_token ?? "unknown-buy-token");
    const sellId = `token:${sell}`;
    const buyId = `token:${buy}`;
    const y = 100 + index * 130;
    addNode(orderId, "order", "Order", 20, y, { subtitle: short(order), entityType: "order", identifier: order, evidenceSource: "trades_canonical" });
    addNode(sellId, "token", String(trade.sell_symbol || short(sell)), 180, y, { entityType: "token", identifier: sell, evidenceSource: "trades_canonical" });
    addNode(fillId, "fill", `Fill #${trade.log_index ?? index}`, 360, y, { subtitle: short(order), evidenceSource: "trades_canonical" });
    addNode(buyId, "token", String(trade.buy_symbol || short(buy)), 520, y, { entityType: "token", identifier: buy, evidenceSource: "trades_canonical" });
    addEdge(orderId, fillId, "settled fill", "trades_canonical");
    addEdge(sellId, fillId, "sold", "trades_canonical", "direct", `sold ${amount(trade, "sell")}`.trim());
    addEdge(fillId, buyId, "bought", "trades_canonical", "direct", `bought ${amount(trade, "buy")}`.trim());
    addEdge(fillId, txId, "included in", "trades_canonical");
  });

  interactions.forEach((interaction, index) => {
    const target = String(interaction.target ?? "unknown-target");
    const logIndex = interaction.log_index ?? index;
    const id = `interaction:${logIndex}:${target}`;
    addNode(id, "interaction", `Event #${logIndex}`, 760 + (index % 3) * 170, transactionY + 190 + Math.floor(index / 3) * 125, { entityType: "address", identifier: target, subtitle: `Interaction · ${short(target)} ${String(interaction.selector ?? "")}`.trim(), evidenceSource: "interactions_canonical" });
    addEdge(txId, id, `Interaction event #${logIndex}`, "interactions_canonical");
  });
  if (allInteractions.length > interactions.length) {
    const id = "interaction:collapsed";
    addNode(id, "interaction", `+${allInteractions.length - interactions.length} events`, 760, transactionY + 190 + Math.ceil(interactions.length / 3) * 125, { evidenceSource: "interactions_canonical" });
    addEdge(txId, id, "additional indexed interactions", "interactions_canonical");
  }

  competitions.forEach((competition, index) => {
    const auction = String(competition.auction_id ?? "");
    if (!auction) return;
    const auctionId = `auction:${auction}`;
    addNode(auctionId, "auction", `Auction ${auction}`, 880, 40 + index * 100, { entityType: "auction", identifier: auction, evidenceSource: "competition_transactions" });
    addEdge(txId, auctionId, "competition mapping", "competition_transactions");
    const winner = String(competition.competition_winner ?? "");
    if (winner) {
      const winnerId = `competition_winner:${winner}`;
      addNode(winnerId, "actor", "Competition winner", 1080, 260 + index * 100, { subtitle: short(winner), entityType: "solver", identifier: winner, role: "competition_winner", evidenceSource: "solver_competitions" });
      addEdge(auctionId, winnerId, "winner evidence", "solver_competitions", "auction_scoped");
    }
    const solver = String(competition.winning_solution_solver ?? "");
    if (solver) {
      const solverId = `competition_solver:${solver}`;
      addNode(solverId, "actor", "Competition solver", 1080, 380 + index * 100, { subtitle: short(solver), entityType: "solver", identifier: solver, role: "competition_solver", evidenceSource: "competition_solutions" });
      addEdge(auctionId, solverId, "winning solution", "competition_solutions", "auction_scoped");
    }
  });

  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}
