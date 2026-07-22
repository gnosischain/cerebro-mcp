import { MaIdentity } from "../../shared/MiniAppChrome";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { AskCerebroButton } from "../components/AskCerebroButton";
import { ChoiceBars } from "../components/ChoiceBars";
import { DatasetPanel } from "../components/DatasetPanel";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { GipBadge } from "../components/GipBadge";
import { MarkdownBody } from "../components/MarkdownBody";
import { QuorumBadge } from "../components/QuorumBadge";
import { SignalingNote } from "../components/SignalingNote";
import { VoteChoiceCell } from "../components/VoteChoiceCell";
import { pairChoices, type ChoiceEntry } from "../model/choices";
import { COLUMN_LABELS, hiddenColumnsFor } from "../model/columns";
import { rowsToObjects } from "../../shared/rowDataset";
import { parseLinks } from "../model/parseRows";
import { shortAddr } from "../../../utils/format";
import { dataset, firstRow, fmtNum, pickNumber, pickString, type GovViewContext } from "../sections/common";

const SNAPSHOT_SPACE_URL = "https://snapshot.org/#/gnosis.eth/proposal/";

/** Choice entries: prefer the enumerated `proposal_choices` dataset; fall
 * back to zipping the detail row's choices/scores JSON. */
function choiceEntries(ctx: GovViewContext, detail: Record<string, unknown> | null): ChoiceEntry[] {
  const rows = rowsToObjects(dataset(ctx, "proposal_choices"));
  if (rows.length > 0) {
    return rows.map((row, index) => ({
      index: pickNumber(row, ["choice_index", "index", "position"]) ?? index + 1,
      label: pickString(row, ["choice", "label", "name"]) || `Choice ${index + 1}`,
      score: pickNumber(row, ["score", "vp", "value"]),
    }));
  }
  if (!detail) return [];
  return pairChoices(
    detail.choices_json ?? detail.choices,
    detail.scores_json ?? detail.scores,
  ).entries;
}

export function ProposalDetail({ ctx }: { ctx: GovViewContext }) {
  const entity = ctx.state.selected_entity;
  const detail = firstRow(ctx, "proposal_detail");
  const votes = ctx.descriptors.proposal_votes;
  const voteIndex = new Map((votes?.columns ?? []).map((column, index) => [column.name, index]));
  const entries = choiceEntries(ctx, detail);
  const choiceLabels = entries.map((entry) => entry.label);
  const quorum = pickNumber(detail, ["quorum"]);
  const scoresTotal = pickNumber(detail, ["scores_total"]);
  const proposalType = pickString(detail, ["type", "proposal_type"]);
  const title = pickString(detail, ["title"]) || entity?.label || "Proposal";
  const identifier = entity?.identifier ?? pickString(detail, ["id"]);
  const snapshotUrl =
    pickString(detail, ["link", "snapshot_link", "snapshot_url"])
    || (identifier ? `${SNAPSHOT_SPACE_URL}${identifier}` : "");
  const links = parseLinks(dataset(ctx, "proposal_forum_links"));

  return (
    <div className="gov-entity">
      <MaIdentity
        label={`PROPOSAL · Snapshot off-chain signaling${proposalType ? ` · ${proposalType}` : ""}`}
        value={title}
        onCopy={() => void navigator.clipboard?.writeText(identifier)}
        rightSlot={snapshotUrl ? (
          <button type="button" className="gov-external" onClick={() => ctx.openLink(snapshotUrl)}>
            Open on Snapshot
          </button>
        ) : undefined}
      />

      <DatasetPanel title="Proposal" descriptor={ctx.descriptors.proposal_detail} groupLoaded emptyLabel="Proposal not found.">
        <dl className="gov-meta">
          {[
            { label: "Id", value: identifier, mono: true },
            { label: "Author", value: shortAddr(pickString(detail, ["author"])), mono: true },
            { label: "Snapshot block", value: fmtNum(pickNumber(detail, ["snapshot_block"])) },
            { label: "Created (UTC)", value: pickString(detail, ["created_at"]) },
            { label: "Voting starts (UTC)", value: pickString(detail, ["start_at"]) },
            { label: "Voting ends (UTC)", value: pickString(detail, ["end_at"]) },
            { label: "Type", value: proposalType || "—" },
            { label: "State", value: pickString(detail, ["state"]) || "—" },
            { label: "Scores state", value: pickString(detail, ["scores_state"]) || "—" },
            { label: "Votes", value: fmtNum(pickNumber(detail, ["votes_count"])) },
          ].map((item) => (
            <div key={item.label} className="gov-meta__item">
              <dt>{item.label}</dt>
              <dd className={item.mono ? "gov-mono" : undefined} title={item.value}>{item.value || "—"}</dd>
            </div>
          ))}
          <div className="gov-meta__item">
            <dt>GIP</dt>
            <dd><GipBadge gip={pickNumber(detail, ["gip_number"])} /></dd>
          </div>
          <div className="gov-meta__item">
            <dt>Quorum</dt>
            <dd><QuorumBadge scoresTotal={scoresTotal} quorum={quorum} /></dd>
          </div>
        </dl>
      </DatasetPanel>

      <DatasetPanel title="Proposal body" descriptor={ctx.descriptors.proposal_detail} groupLoaded emptyLabel="No body text.">
        <MarkdownBody body={pickString(detail, ["body_markdown", "body"])} openLink={ctx.openLink} />
      </DatasetPanel>

      <DatasetPanel
        title="Choices and signaling outcome"
        descriptor={ctx.descriptors.proposal_choices ?? ctx.descriptors.proposal_detail}
        groupLoaded
        emptyLabel="No choices recorded."
      >
        <ChoiceBars
          entries={entries}
          quorum={quorum}
          scoresTotal={scoresTotal}
          rankedNote={proposalType === "ranked-choice"
            ? "Ranked-choice proposal: scores reflect Snapshot's ranked tabulation; each vote lists ordered preferences."
            : undefined}
        />
      </DatasetPanel>

      <DatasetPanel
        title="Votes"
        descriptor={votes}
        groupLoaded
        emptyLabel="No votes recorded."
      >
        <PaginatedTable
          dataset={votes}
          datasetKey="proposal_votes"
          viewId={ctx.viewId}
          fetchRows={ctx.fetchRows}
          maxHeight="520px"
          hiddenColumns={hiddenColumnsFor("proposal_votes")}
          columnLabels={COLUMN_LABELS}
          sourceLabel="Snapshot off-chain signaling"
          onCellClick={(column, value) => {
            if (column !== "voter") return;
            const voter = String(value ?? "");
            if (voter) ctx.onEntity("voter", voter.toLowerCase());
          }}
          renderCell={(column, value, row) => {
            if (column === "voter") {
              return <span className="gov-mono" title={String(value ?? "")}>{shortAddr(String(value ?? ""))}</span>;
            }
            if (column === "choice_kind") {
              return (
                <VoteChoiceCell
                  kind={value}
                  index={row[voteIndex.get("choice_index") ?? -1]}
                  indexes={row[voteIndex.get("choice_indexes") ?? -1]}
                  choices={choiceLabels}
                />
              );
            }
            if (column === "created_at") {
              return <span className="gov-mono">{String(value ?? "").slice(0, 16).replace("T", " ")}</span>;
            }
            if (column === "reason") {
              const reason = String(value ?? "");
              return reason ? <span title={reason}>✎</span> : <span>—</span>;
            }
            return undefined;
          }}
        />
      </DatasetPanel>

      <DatasetPanel
        title="Linked forum discussions"
        descriptor={ctx.descriptors.proposal_forum_links}
        groupLoaded
        emptyLabel="No linked forum topic found (no author-declared discussion URL and no exact GIP-number match)."
      >
        <div className="gov-links">
          {links.map((link, index) => (
            <div key={index} className="gov-links__row">
              <span className={`gov-link-tier gov-link-tier--${link.link_source}`}>{link.link_source}</span>
              <button
                type="button"
                title={link.linked_title}
                onClick={() => {
                  if (link.linked_type === "forum_topic") ctx.onEntity("forum_topic", link.linked_id);
                  else ctx.onEntity("proposal", link.linked_id);
                }}
              >
                {link.linked_title || link.linked_id}
              </button>
            </div>
          ))}
        </div>
      </DatasetPanel>

      <div className="gov-actions">
        <ExportCsvButton
          viewId={ctx.viewId}
          datasetKey="proposal_votes"
          descriptor={votes}
          fetchRows={ctx.fetchRows}
          scope={`proposal_${identifier.slice(0, 10)}`}
          label="Export votes CSV"
        />
        <AskCerebroButton state={ctx.state} aggregates={ctx.aggregates} sendMessage={ctx.sendMessage} />
      </div>
      <SignalingNote />
    </div>
  );
}
