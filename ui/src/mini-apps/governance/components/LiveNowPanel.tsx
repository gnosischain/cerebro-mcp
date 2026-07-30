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
// "Moving toward a GIP" excludes three things, all deliberately, and states a
// count for each rather than quietly dropping it:
//   - a GIP that already reached a Snapshot vote is PAST a vote;
//   - a phase-1 topic is at the IDEA stage, upstream of a vote rather than
//     moving toward one (phase-2 is the pre-vote signalling stage, and that is
//     what this panel's title actually claims);
//   - a phase-2 topic untouched for the idle window is dormant, not moving.
// The window is 45 days, taken from the measured distribution rather than
// picked: of 157 open phase-1/2 topics the median has been idle 1,265 days, and
// recency clusters hard -- 3 within 30 days, 5 within 45, then nothing new until
// 104. A 180-day window listed threads idle four months and read as noise.

/** Mirrors GIP_PIPELINE_IDLE_DAYS in governance_explorer.py. Only used for
 * copy: the filtering itself is done in SQL, so a drift here misstates the
 * window but cannot change which rows appear. */
const IDLE_WINDOW_DAYS = 45;

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
  const ideas = finite(pipelineRows[0]?.ideas_hidden) ?? 0;

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
            No phase-2 topic has been touched in the last {IDLE_WINDOW_DAYS} days.
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
          <strong>phase-2</strong> is the community&apos;s pre-vote signalling stage — the forum&apos;s
          own tag, not an inference — and reaching it is what &ldquo;moving toward a GIP&rdquo;
          means. A row without a GIP number has none in its title yet. Topics whose GIP already
          reached a Snapshot vote are excluded: those are past a vote, not moving toward one.
          Nothing here is silently dropped — every exclusion is counted below.
          {ideas > 0 && (
            <> <strong>{ideas}</strong> active <strong>phase-1</strong>{" "}
            {ideas === 1 ? "topic is" : "topics are"} not listed: that is the idea stage, which is
            upstream of a vote rather than moving toward one.</>
          )}
          {dormant > 0 && (
            <> A further <strong>{dormant}</strong> pending{" "}
            {dormant === 1 ? "topic has" : "topics have"} had no activity in{" "}
            {IDLE_WINDOW_DAYS} days and {dormant === 1 ? "is" : "are"} not shown — dormant, not
            missing. The Forum tab lists them.</>
          )}
        </p>
      </section>
    </div>
  );
}
