import { useMemo, useState } from "react";
import type { ProfileCard } from "./types";

interface Props {
  catalog: ProfileCard[];
  onSeed: (profileId: string | null, nodeId: string) => void;
}

const SECTOR_LABEL: Record<string, string> = {
  Circles: "Circles",
  circles: "Circles",
  gpay: "GPay",
  safe: "Safe",
  transfers: "Transfers",
  pools: "Pools",
  yields: "Yields",
  consensus: "Staking",
  GBCDeposit: "Staking",
  bridges: "Bridges",
  crawlers_data: "Labels",
  shared: "Shared",
};

const SECTOR_COLOR: Record<string, string> = {
  Circles: "#60a5fa",
  GPay: "#fbbf24",
  Safe: "#a78bfa",
  Transfers: "#6ee7b7",
  Pools: "#c084fc",
  Yields: "#f472b6",
  Staking: "#f97316",
  Bridges: "#facc15",
  Labels: "#94a3b8",
  Shared: "#94a3b8",
};

function sectorOf(module: string): string {
  return SECTOR_LABEL[module] ?? module ?? "Other";
}

/**
 * Compact start page:
 *   1. Primary action (address seed) at the top — no scrolling needed.
 *   2. Filter input, then profiles as dense one-line rows (sector chip |
 *      status dot | name | kind arrow | description).
 *   3. Sector chip is color-coded so the eye can scan vertically by sector.
 *
 * All of the above fits inside a single 100vh panel without outer scroll;
 * the profile list scrolls internally when there are more than a screen's
 * worth of rows.
 */
export function CatalogScreen({ catalog, onSeed }: Props) {
  const [seedInput, setSeedInput] = useState("");
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "approved" | "candidate">("all");

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return catalog
      .filter((profile) => {
        if (statusFilter !== "all" && profile.semantic_status !== statusFilter) return false;
        if (!needle) return true;
        const hay = [profile.profile, profile.module, profile.description, ...profile.question_synonyms]
          .join(" ")
          .toLowerCase();
        return hay.includes(needle);
      })
      .sort((a, b) => {
        const sa = sectorOf(a.module);
        const sb = sectorOf(b.module);
        return sa === sb ? a.profile.localeCompare(b.profile) : sa.localeCompare(sb);
      });
  }, [catalog, filter, statusFilter]);

  const sectorCount = useMemo(
    () => new Set(filtered.map((p) => sectorOf(p.module))).size,
    [filtered],
  );

  const submitSeed = () => {
    const trimmed = seedInput.trim();
    if (!trimmed) return;
    onSeed(null, trimmed);
  };

  const isAddress = /^0x[a-fA-F0-9]{40}$/.test(seedInput.trim());

  return (
    <section className="ge-catalog">
      <header className="ge-catalog-head">
        <h2>Graph Explorer</h2>
        <span className="ge-catalog-count">
          {filtered.length} profiles · {sectorCount} sectors
        </span>
      </header>

      <div className="ge-catalog-seed">
        <span className="ge-catalog-seed-label">Start from an address</span>
        <div className="ge-catalog-seed-row">
          <input
            type="text"
            value={seedInput}
            onChange={(e) => setSeedInput(e.target.value)}
            placeholder="0x… (EVM address, ENS, or any avatar)"
            onKeyDown={(e) => {
              if (e.key === "Enter") submitSeed();
            }}
            autoFocus
          />
          <button
            type="button"
            className="ge-btn primary"
            onClick={submitSeed}
            disabled={!seedInput.trim()}
          >
            Explore →
          </button>
        </div>
        <p className="ge-catalog-seed-hint">
          {seedInput.trim()
            ? isAddress
              ? "Auto-detects which graph profiles apply for this address."
              : "Looks like a non-address seed — will try as-is."
            : "Or pick a profile from the list below."}
        </p>
      </div>

      <div className="ge-catalog-filter">
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search profiles by name, sector, or synonym…"
        />
        <div className="ge-segment" role="tablist" aria-label="Status filter">
          <button
            type="button"
            className={statusFilter === "all" ? "active" : ""}
            onClick={() => setStatusFilter("all")}
          >
            All
          </button>
          <button
            type="button"
            className={"appr " + (statusFilter === "approved" ? "active" : "")}
            onClick={() => setStatusFilter("approved")}
            title="Approved only"
          >
            <span className="ge-dot ge-dot-approved" />
          </button>
          <button
            type="button"
            className={"cand " + (statusFilter === "candidate" ? "active" : "")}
            onClick={() => setStatusFilter("candidate")}
            title="Candidate only"
          >
            <span className="ge-dot ge-dot-candidate" />
          </button>
        </div>
      </div>

      <ul className="ge-catalog-list">
        {filtered.map((profile) => {
          const sector = sectorOf(profile.module);
          const color = SECTOR_COLOR[sector] ?? "#94a3b8";
          return (
            <li key={profile.profile}>
              <button
                type="button"
                className="ge-catalog-row"
                onClick={() => onSeed(profile.profile, "")}
                title={profile.description || profile.profile}
              >
                <span
                  className="ge-catalog-sector"
                  style={{ color, borderColor: `${color}33` }}
                  title={sector}
                >
                  {sector}
                </span>
                <span
                  className={`ge-dot ${profile.semantic_status === "approved" ? "ge-dot-approved" : "ge-dot-candidate"}`}
                  aria-hidden
                />
                <span className="ge-catalog-name">{profile.profile}</span>
                <span className="ge-catalog-kind">
                  {profile.source_kind} → {profile.target_kind}
                  {profile.time_aware ? " · t" : ""}
                </span>
                <span className="ge-catalog-go" aria-hidden>
                  →
                </span>
              </button>
            </li>
          );
        })}
        {!filtered.length ? (
          <li className="ge-catalog-empty">No profiles match — clear the filter or relax the status toggle.</li>
        ) : null}
      </ul>
    </section>
  );
}
