"""Generate solverRegistry.ts from Dune spellbook cow_protocol solver models.

Parses the `known_solver_metadata (address, environment, name)` VALUES tuples
from each per-chain model and emits a TypeScript map keyed by chain id. Only
'prod'-environment entries are kept (barn/staging solvers are not shown in the
explorer). Addresses are lowercased.
"""
import re
import sys
from pathlib import Path

CHAIN_IDS = {
    "eth": 1,
    "gnosis": 100,
    "arbitrum": 42161,
    "base": 8453,
    "bnb": 56,
    "polygon": 137,
    "avalanche_c": 43114,
    "linea": 59144,
    "ink": 57073,
    "plasma": 9745,
}
TUPLE_RE = re.compile(
    r"\(\s*(0x[0-9a-fA-F]{40})\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)"
)

def parse(path: Path) -> list[tuple[str, str, str]]:
    return TUPLE_RE.findall(path.read_text())

def main() -> None:
    root = Path(sys.argv[1])
    out: dict[int, dict[str, str]] = {}
    total = 0
    for stem, chain_id in CHAIN_IDS.items():
        path = root / f"solvers_{stem}.sql"
        if not path.exists():
            print(f"warn: missing {path}", file=sys.stderr)
            continue
        entries: dict[str, str] = {}
        for address, environment, name in parse(path):
            if environment != "prod":
                continue
            entries[address.lower()] = name
        out[chain_id] = dict(sorted(entries.items()))
        total += len(entries)
        print(f"chain {chain_id}: {len(entries)} prod solvers", file=sys.stderr)
    print(f"total: {total}", file=sys.stderr)

    lines = [
        "// GENERATED from Dune spellbook cow_protocol *_solvers models —",
        "// https://github.com/duneanalytics/spellbook (dbt_subprojects/hourly_spellbook/",
        "// models/_project/cow_protocol/<chain>/cow_protocol_<chain>_solvers.sql).",
        "// Regenerate with scripts/dev/gen_cow_solver_registry.py. Only 'prod'",
        "// environment entries are included; addresses are lowercase.",
        "",
        "export const SOLVER_NAMES: Record<number, Record<string, string>> = {",
    ]
    for chain_id in sorted(out):
        lines.append(f"  {chain_id}: {{")
        for address, name in out[chain_id].items():
            lines.append(f'    "{address}": "{name}",')
        lines.append("  },")
    lines.append("};")
    lines.append("")
    lines.append("export function solverName(chainId: number, address: string): string {")
    lines.append("  const value = (address || \"\").toLowerCase();")
    lines.append("  return SOLVER_NAMES[chainId]?.[value]")
    lines.append("    ?? SOLVER_NAMES[1]?.[value]")
    lines.append("    ?? \"\";")
    lines.append("}")
    lines.append("")
    Path(sys.argv[2]).write_text("\n".join(lines))

if __name__ == "__main__":
    main()
