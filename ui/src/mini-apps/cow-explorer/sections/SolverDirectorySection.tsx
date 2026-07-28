// Solver-directory facet (client-side view over the `solvers` server
// section; group `directory`). One aggregated row per solver identity with
// expandable per-chain address rows — see SolverDirectoryTable for the
// aggregation/activity contract. All filtering is CLIENT-side (the dataset
// is all-time by design; there is nothing to refetch).

import { useMemo, useState } from "react";
import { rowsToObjects } from "../../shared/rowDataset";
import { ChainBadge } from "../../shared/ChainBadge";
import { KpiTile } from "../components/KpiTile";
import {
  aggregateDirectory,
  filterDirectory,
  SolverDirectoryTable,
  type DirectoryFilters,
} from "../components/SolverDirectoryTable";
import {
  ChartSection,
  CoverageInfo,
  GroupGate,
  formatNumber,
  toDataset,
  type SectionProps,
} from "./SectionViews";

export function SolverDirectorySection(props: SectionProps) {
  const [search, setSearch] = useState("");
  const [chains, setChains] = useState<number[]>([]);
  const [env, setEnv] = useState<DirectoryFilters["env"]>("all");
  const [activeOnly, setActiveOnly] = useState(false);

  const directoryHydrated = props.hydrated.solver_directory;
  const rows = useMemo(() => rowsToObjects(toDataset(directoryHydrated)), [directoryHydrated]);
  const groups = useMemo(() => aggregateDirectory(rows), [rows]);
  const filtered = useMemo(
    () => filterDirectory(groups, { search, chains, env, activeOnly }),
    [groups, search, chains, env, activeOnly],
  );

  const stats = useMemo(() => {
    const addresses = new Set(rows.map((row) => String(row.solver ?? "").toLowerCase()).filter(Boolean));
    const chainIds = [...new Set(rows.map((row) => Number(row.chain_id)).filter((id) => Number.isFinite(id)))]
      .sort((a, b) => a - b);
    return {
      addresses: addresses.size,
      identities: groups.length,
      active: groups.filter((group) => group.active).length,
      chainIds,
      prod: groups.filter((group) => group.envs.includes("prod")).length,
      barn: groups.filter((group) => group.envs.includes("barn")).length,
      unknown: groups.filter((group) => group.envs.length === 0).length,
    };
  }, [rows, groups]);

  const toggleChain = (chainId: number) => {
    setChains((current) => current.includes(chainId)
      ? current.filter((id) => id !== chainId)
      : [...current, chainId]);
  };

  return (
    <GroupGate props={props} group="directory">
      <div className="cow-kpi-head">
        <div className="cow-kpi-tiles">
          <KpiTile label="Solver addresses" value={formatNumber(stats.addresses)} delta={`${formatNumber(stats.identities)} identities`} />
          <KpiTile label="Active (7d of chain anchor)" value={formatNumber(stats.active)} />
          <KpiTile label="Networks covered" value={formatNumber(stats.chainIds.length)} />
          <KpiTile label="Prod / barn" value={`${formatNumber(stats.prod)} / ${formatNumber(stats.barn)}`} note={stats.unknown > 0 ? `${formatNumber(stats.unknown)} unregistered` : undefined} />
        </div>
        <div className="cow-kpi-head__meta">
          <CoverageInfo descriptor={props.descriptors.solver_directory} label="Directory methodology" />
        </div>
      </div>
      <ChartSection datasetKey="solver_directory" title="Solver directory (observed presence, all time)" props={props} metaLabel="About this data">
        <div className="cow-dir-filters">
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search name or address"
            aria-label="Search solvers"
          />
          <div className="cow-dir-filters__chains" role="group" aria-label="Filter by network">
            {stats.chainIds.map((chainId) => (
              <button
                key={chainId}
                type="button"
                className={chains.includes(chainId) ? "is-active" : ""}
                aria-pressed={chains.includes(chainId)}
                onClick={() => toggleChain(chainId)}
              >
                <ChainBadge chainId={chainId} showName={false} />
              </button>
            ))}
            {chains.length > 0 && (
              <button type="button" className="cow-dir-filters__clear" onClick={() => setChains([])}>
                clear
              </button>
            )}
          </div>
          <select
            value={env}
            onChange={(event) => setEnv(event.target.value as DirectoryFilters["env"])}
            aria-label="Environment filter"
          >
            <option value="all">All envs</option>
            <option value="prod">prod</option>
            <option value="barn">barn</option>
            <option value="unknown">unregistered</option>
          </select>
          <label className="cow-dir-filters__active">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(event) => setActiveOnly(event.target.checked)}
            />
            Active only
          </label>
          <span className="cow-dir-filters__count">
            {formatNumber(filtered.length)} / {formatNumber(groups.length)} solvers
          </span>
        </div>
        <SolverDirectoryTable groups={filtered} onEntity={props.onEntity} />
      </ChartSection>
    </GroupGate>
  );
}
