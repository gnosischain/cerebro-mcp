export interface TxContextRow {
  txHash: string;
  initiator: string | null;
  target: string | null;
  methodSelector: string | null;
  input: string | null;
  nonce: string | null;
  nativeValueRaw: string | null;
  gasLimit: string | null;
  gasUsed: string | null;
  effectiveGasPrice: string | null;
  feeRaw: string | null;
  status: string | null;
  blockNumber: string | null;
  transactionIndex: string | null;
  blockTimestamp: string | null;
  matchedBecause: string[];
}

function scalar(value: unknown): string | null {
  if (value == null || value === "") return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "bigint") return String(value);
  return null;
}

function list(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value !== "string" || !value.trim()) return [];
  try {
    const parsed = JSON.parse(value) as unknown;
    if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean);
  } catch {
    // Older scope rows used a comma-separated compatibility value.
  }
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export function parseTxContextRows(
  rows: unknown[][] | undefined,
  columns: string[] | undefined,
): TxContextRow[] {
  if (!rows?.length) return [];
  const names = (columns ?? []).map((name) => name.toLowerCase());
  const indexOf = (candidates: string[], fallback: number) => {
    for (const candidate of candidates) {
      const index = names.indexOf(candidate);
      if (index >= 0) return index;
    }
    return fallback;
  };
  const at = (row: unknown[], candidates: string[], fallback: number) =>
    row[indexOf(candidates, fallback)];

  return rows.flatMap((row) => {
    const txHash = scalar(at(row, ["tx_hash", "transaction_hash"], 0));
    if (!txHash) return [];
    return [{
      txHash: txHash.toLowerCase(),
      initiator: scalar(at(row, ["initiator", "from", "from_address", "tx_from"], 1)),
      target: scalar(at(row, ["target", "to", "to_address", "tx_to"], 2)),
      methodSelector: scalar(at(row, ["method_selector", "method_id"], 3)),
      input: scalar(at(row, ["input", "calldata"], 4)),
      nonce: scalar(at(row, ["nonce"], 5)),
      nativeValueRaw: scalar(at(row, ["native_value_raw", "value_raw", "value"], 6)),
      gasLimit: scalar(at(row, ["gas_limit", "gas"], 7)),
      gasUsed: scalar(at(row, ["gas_used"], 8)),
      effectiveGasPrice: scalar(at(row, ["effective_gas_price", "gas_price"], 9)),
      feeRaw: scalar(at(row, ["fee_raw", "transaction_fee_raw"], 10)),
      status: scalar(at(row, ["status", "receipt_status"], 11)),
      blockNumber: scalar(at(row, ["block_number"], 12)),
      transactionIndex: scalar(at(row, ["transaction_index"], 13)),
      blockTimestamp: scalar(at(row, ["block_timestamp", "timestamp"], 14)),
      matchedBecause: list(at(row, ["matched_because", "match_reasons"], 15)),
    }];
  });
}
