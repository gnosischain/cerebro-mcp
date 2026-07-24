"""Generate solverRegistry.ts from Dune spellbook cow_protocol solver models.

Parses the `known_solver_metadata (address, environment, name)` VALUES tuples
from each per-chain model and emits a TypeScript map keyed by chain id.

v3: BOTH 'prod' and 'barn' entries are emitted (SOLVER_REGISTRY with
{name, env}); the legacy prod-only SOLVER_NAMES map and solverName() helper
are kept for back-compat. Spellbook rows whose environment is neither 'prod'
nor 'barn' (e.g. 'service', 'test') are folded into 'barn' — they are staging
infrastructure, never production competition identities. Addresses lowercase.

Usage: python scripts/dev/gen_cow_solver_registry.py <staging_dir> <out_ts>
where <staging_dir> holds the per-chain models named solvers_<stem>.sql
(copied/renamed from the spellbook checkout or fetched from upstream raw).
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
    out: dict[int, dict[str, tuple[str, str]]] = {}
    total_prod = 0
    total_barn = 0
    for stem, chain_id in CHAIN_IDS.items():
        path = root / f"solvers_{stem}.sql"
        if not path.exists():
            print(f"warn: missing {path}", file=sys.stderr)
            continue
        entries: dict[str, tuple[str, str]] = {}
        for address, environment, name in parse(path):
            env = "prod" if environment == "prod" else "barn"
            key = address.lower()
            # A prod label wins if the same address appears in both envs.
            if key in entries and entries[key][1] == "prod":
                continue
            entries[key] = (name, env)
        out[chain_id] = dict(sorted(entries.items()))
        prod = sum(1 for _, env in entries.values() if env == "prod")
        total_prod += prod
        total_barn += len(entries) - prod
        print(
            f"chain {chain_id}: {prod} prod / {len(entries) - prod} barn solvers",
            file=sys.stderr,
        )
    print(f"total: {total_prod} prod / {total_barn} barn", file=sys.stderr)

    lines = [
        "// GENERATED from Dune spellbook cow_protocol *_solvers models —",
        "// https://github.com/duneanalytics/spellbook (dbt_subprojects/hourly_spellbook/",
        "// models/_project/cow_protocol/<chain>/cow_protocol_<chain>_solvers.sql).",
        "// Regenerate with scripts/dev/gen_cow_solver_registry.py. Both 'prod' and",
        "// 'barn' environments are included; addresses are lowercase.",
        "",
        'export type SolverEnv = "prod" | "barn";',
        "",
        "export interface SolverInfo {",
        "  name: string;",
        "  env: SolverEnv;",
        "}",
        "",
        "export const SOLVER_REGISTRY: Record<number, Record<string, SolverInfo>> = {",
    ]
    for chain_id in sorted(out):
        lines.append(f"  {chain_id}: {{")
        for address, (name, env) in out[chain_id].items():
            lines.append(
                f'    "{address}": {{ name: "{name}", env: "{env}" }},'
            )
        lines.append("  },")
    lines.append("};")
    lines.append("")
    lines.append("// Legacy prod-only view (kept for existing call sites).")
    lines.append("export const SOLVER_NAMES: Record<number, Record<string, string>> = {")
    for chain_id in sorted(out):
        lines.append(f"  {chain_id}: {{")
        for address, (name, env) in out[chain_id].items():
            if env == "prod":
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
    lines.append("export function solverInfo(chainId: number, address: string): SolverInfo | null {")
    lines.append("  const value = (address || \"\").toLowerCase();")
    lines.append("  return SOLVER_REGISTRY[chainId]?.[value]")
    lines.append("    ?? SOLVER_REGISTRY[1]?.[value]")
    lines.append("    ?? null;")
    lines.append("}")
    lines.append("")
    Path(sys.argv[2]).write_text("\n".join(lines))

if __name__ == "__main__":
    main()
