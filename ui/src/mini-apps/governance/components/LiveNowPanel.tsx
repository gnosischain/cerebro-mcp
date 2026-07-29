import { finite, rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import { GipBadge } from "./GipBadge";
import { fmtNum } from "../sections/common";

// "What is live now" — the two things a governance participant can still act on.
//
// Deliberately NOT date-scoped, and neither is the SQL behind it: the toolbar
// range answers "what happened in this window", while this panel answers "what
// needs attention today". A 90-day filter would hide a vote that opened 100
// days ago and is still open.
//
// The empty state is load-bearing. Every one of the 253 Snapshot proposals is
// currently closed, so "Open votes" renders empty essentially always — and an
// empty region with no words reads as "still loading" or "something broke".
// It says nothing is open, which is the actual finding.
//
// "Moving toward a GIP" excludes two things, both deliberately: a GIP that
// already reached a Snapshot vote is PAST a vote, not moving toward one; and a
// pending topic untouched for six months is dormant, not pending. The dormant
// count is stated rather than hidden — 88 of them exist, and a list that
// quietly dropped them would imply a pipeline that is not there.

/** Idle-days as a short human phrase. Only rendered past a week — "idle 1d" on
 * a thread posted to yesterday is noise. */
function idleFor(days: number | null): string {
  if (days === null || days < 7) return "";
  if (days < 60) return `idle ${days}d`;
  return `idle ${Math.floor(days / 30)}mo`;
}

/** Rough time-to-close. Hours are what the query returns; days are friendlier
 * past a couple of days, and neither is rendered below zero — a negative
 * "ends in" would mean the row should not be here at all. */
function endsIn(hours: number | null): string {
  if (hours === null) return "";
  if (hours <= 0) return "closing now";
  if (hours < 48) return `ends in ${hours}h`;
  return `ends in ${Math.floor(hours / 24)}d`;
}

export interface LiveNowPanelProps {
  votes?: RowDataset;
  pipeline?: RowDataset;
  onProposal: (id: string) => void;
  onTopic: (id: string) => void;
}

export function LiveNowPanel({ votes, pipeline, onProposal, onTopic }: LiveNowPanelProps) {
  const voteRows = rowsToObjects(votes);
  const pipelineRows = rowsToObjects(pipeline);
  // Constant column, so any row carries it; 0 when the list itself is empty.
  const dormant = finite(pipelineRows[0]?.dormant_hidden) ?? 0;

  return (
    <div className="gov-livenow">
      <section className="gov-livenow__col">
        <h3 className="gov-livenow__head">
          Open votes
          {voteRows.length > 0 && <span className="gov-livenow__count">{voteRows.length}</span>}
        </h3>
        {voteRows.length === 0 ? (
          <p className="gov-livenow__empty">
            No Snapshot vote is open right now — every indexed proposal has closed. This is a
            statement about the space, not a loading state.
          </p>
        ) : (
          <ul className="gov-livenow__list">
            {voteRows.map((row) => {
              const id = String(row.proposal_id ?? "");
              const gip = finite(row.gip);
              return (
                <li key={id} className="gov-livenow__row">
                  <button
                    type="button"
                    className="gov-livenow__main"
                    title={String(row.title ?? "")}
                    onClick={() => onProposal(id)}
                  >
                    {gip !== null && <GipBadge gip={gip} />}
                    <span className="gov-livenow__title">{String(row.title ?? "")}</span>
                  </button>
                  <span className="gov-livenow__meta">
                    <span className="gov-livenow__clock">{endsIn(finite(row.hours_left))}</span>
                    <span>{fmtNum(finite(row.votes_count))} votes</span>
                    {/* met/missed/unspecified — never pass/fail: quorum is a
                        threshold on participation, not a verdict on the idea. */}
                    <span className={`gov-quorum gov-quorum--${String(row.quorum_status ?? "")}`}>
                      quorum {String(row.quorum_status ?? "—")}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="gov-livenow__col">
        <h3 className="gov-livenow__head">
          Moving toward a GIP
          {pipelineRows.length > 0 && (
            <span className="gov-livenow__count">{pipelineRows.length}</span>
          )}
        </h3>
        {pipelineRows.length === 0 ? (
          <p className="gov-livenow__empty">
            No forum topic is tagged phase-1 or phase-2 right now.
          </p>
        ) : (
          <ul className="gov-livenow__list">
            {pipelineRows.map((row) => {
              const id = String(row.topic_id ?? "");
              const gip = finite(row.gip);
              const phase = String(row.phase ?? "");
              const idle = idleFor(finite(row.days_idle));
              return (
                <li key={`${phase}-${id}`} className="gov-livenow__row">
                  <button
                    type="button"
                    className="gov-livenow__main"
                    title={String(row.title ?? "")}
                    onClick={() => onTopic(id)}
                  >
                    <span className={`gov-phase gov-phase--${phase}`}>{phase}</span>
                    {gip !== null && <GipBadge gip={gip} />}
                    <span className="gov-livenow__title">{String(row.title ?? "")}</span>
                  </button>
                  <span className="gov-livenow__meta">
                    <span>{fmtNum(finite(row.posts_count))} posts</span>
                    <span>{String(row.last_posted_at ?? "").slice(0, 10)}</span>
                    {idle && <span className="gov-livenow__idle">{idle}</span>}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        <p className="gov-caption">
          <strong>phase-2</strong> is the community&apos;s pre-vote signalling stage and{" "}
          <strong>phase-1</strong> the idea stage — these are the forum&apos;s own tags, not an
          inference. A row without a GIP number has none in its title yet. Topics whose GIP
          already reached a Snapshot vote are excluded: those are past a vote, not moving toward
          one.
          {dormant > 0 && (
            <> A further <strong>{dormant}</strong> pending {dormant === 1 ? "topic has" : "topics have"}{" "}
            had no activity in six months and {dormant === 1 ? "is" : "are"} not shown — dormant,
            not missing. The Forum tab lists them.</>
          )}
        </p>
      </section>
    </div>
  );
}
