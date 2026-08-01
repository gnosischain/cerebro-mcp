import { useEffect, useState } from "react";
import { AsyncButton } from "../../shared/AsyncButton";
import { MaIdentity } from "../../shared/MiniAppChrome";
import { AskCerebroButton } from "../components/AskCerebroButton";
import { DatasetPanel } from "../components/DatasetPanel";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { GipBadge } from "../components/GipBadge";
import { MarkdownBody } from "../components/MarkdownBody";
import { SafeHtml } from "../components/SafeHtml";
import { SignalingNote } from "../components/SignalingNote";
import { ChartCard } from "../../../components/ChartCard";
import { ChoiceBars } from "../components/ChoiceBars";
import { topicLikesOption } from "../model/chartOptions";
import { pickPostBody, preprocessDiscourseRaw } from "../model/discourse";
import { groupPollOptions } from "../model/polls";
import { datasetError } from "../../shared/datasetError";
import { rowsToObjects } from "../../shared/rowDataset";
import { parseLinks } from "../model/parseRows";
import { dataset, firstRow, fmtNum, KpiRow, pickNumber, pickString, type GovViewContext } from "../sections/common";

const FORUM_BASE_URL = "https://forum.gnosis.io";

interface PostView {
  post_number: number;
  username: string;
  user_id: string;
  created_at: string;
  reply_to: number | null;
  like_count: number | null;
  reads: number | null;
  raw_markdown: string;
  cooked_html: string;
  plain_text: string;
}

function toPostView(row: Record<string, unknown>): PostView {
  return {
    post_number: pickNumber(row, ["post_number"]) ?? 0,
    username: pickString(row, ["username", "author"]),
    user_id: pickString(row, ["user_id"]),
    created_at: pickString(row, ["created_at"]),
    reply_to: pickNumber(row, ["reply_to_post_number"]),
    like_count: pickNumber(row, ["like_count"]),
    reads: pickNumber(row, ["reads"]),
    raw_markdown: pickString(row, ["raw_markdown", "raw"]),
    cooked_html: pickString(row, ["cooked_html", "cooked"]),
    plain_text: pickString(row, ["plain_text"]),
  };
}

function PostBody({ post, openLink }: { post: PostView; openLink: (url: string) => void }) {
  const picked = pickPostBody(post);
  if (picked.mode === "markdown") {
    return <MarkdownBody body={preprocessDiscourseRaw(picked.body)} openLink={openLink} />;
  }
  if (picked.mode === "html") {
    return <SafeHtml html={picked.body} openLink={openLink} />;
  }
  return <div className="gov-post-body"><p>{picked.body || "(empty post)"}</p></div>;
}

export function TopicDetail({ ctx }: { ctx: GovViewContext }) {
  const entity = ctx.state.selected_entity;
  const detail = firstRow(ctx, "topic_detail");
  const postsDescriptor = ctx.descriptors.topic_posts;

  // topic_posts is a LARGE dataset (server pagination, post_number ASC) —
  // page locally from the descriptor preview instead of full hydration.
  const [rows, setRows] = useState<unknown[][]>(postsDescriptor?.preview_rows ?? []);
  const [nextToken, setNextToken] = useState<string>(postsDescriptor?.page_token ?? "");
  useEffect(() => {
    setRows(postsDescriptor?.preview_rows ?? []);
    setNextToken(postsDescriptor?.page_token ?? "");
  }, [postsDescriptor]);

  const columns = (postsDescriptor?.columns ?? []).map((column) => column.name);
  const posts = rowsToObjects({ columns, rows })
    .map(toPostView)
    .sort((a, b) => a.post_number - b.post_number);

  const topicId = entity?.identifier ?? pickString(detail, ["id"]);
  const title = pickString(detail, ["title"]) || entity?.label || `Topic ${topicId}`;

  // The always-visible brief above the collapsed thread: GIP / title / author
  // (the opener's) / status (the GIP phase tag when one exists, else the
  // topic state) / type (first non-phase tag, else the category) / created.
  const openerUsername = posts.find((post) => post.post_number === 1)?.username ?? "";
  const tagsRaw = detail?.tags;
  const topicTags = Array.isArray(tagsRaw)
    ? tagsRaw.map((tag) => String(tag)).filter(Boolean)
    : String(tagsRaw ?? "").split(",").map((tag) => tag.trim()).filter(Boolean);
  const phaseTag = topicTags.find((tag) => tag.toLowerCase().startsWith("phase"));
  const typeTag = topicTags.find((tag) => !tag.toLowerCase().startsWith("phase"));
  const createdLong = (() => {
    const iso = pickString(detail, ["created_at"]);
    if (!iso) return "";
    const parsed = new Date(iso);
    return Number.isFinite(parsed.getTime())
      ? parsed.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })
      : iso.slice(0, 10);
  })();
  const slug = pickString(detail, ["slug"]);
  const externalUrl =
    pickString(detail, ["external_url", "url"])
    || (topicId ? `${FORUM_BASE_URL}/t/${slug ? `${slug}/` : ""}${topicId}` : "");
  const status = pickString(detail, ["status"]);
  const links = parseLinks(dataset(ctx, "topic_proposal_links"));
  // A failed links lookup must stay distinguishable from "no linked vote" —
  // the identity row carries a muted note instead of silently showing nothing.
  const linksFailed = links.length === 0 && Boolean(datasetError(ctx.descriptors.topic_proposal_links));
  const pollViews = groupPollOptions(dataset(ctx, "topic_polls"));
  const likesRows = rowsToObjects(dataset(ctx, "topic_likes_activity"));
  const likesSpec = topicLikesOption(likesRows);

  // The thread is collapsed by default so the topic's METRICS (KPIs, polls,
  // likes timeline) lead the page; a "Post #N" jump expands it first.
  const [postsOpen, setPostsOpen] = useState(false);
  const scrollToPost = (postNumber: number) => {
    setPostsOpen(true);
    requestAnimationFrame(() => {
      document.getElementById(`gov-post-${postNumber}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  return (
    <div className="gov-entity">
      <MaIdentity
        label={`FORUM TOPIC · forum.gnosis.io${status ? ` · ${status}` : ""}`}
        value={title}
        onCopy={() => void navigator.clipboard?.writeText(externalUrl || String(topicId))}
        rightSlot={externalUrl || links.length > 0 || linksFailed ? (
          <span className="gov-identity-actions">
            {linksFailed && <span className="gov-caption">Snapshot link lookup failed</span>}
            {links.length > 0 && (
              // The linked Snapshot vote lives up here beside the forum link —
              // the primary (discussion-declared) link is the click target;
              // any further GIP-number matches ride the (+N) and the tooltip.
              <button
                type="button"
                className="gov-external"
                title={links.map((link) => link.linked_title || link.linked_id).join("\n")}
                onClick={() => ctx.onEntity("proposal", links[0].linked_id)}
              >
                Snapshot vote{links.length > 1 ? ` (+${links.length - 1})` : ""}
              </button>
            )}
            {externalUrl && (
              <button type="button" className="gov-external" onClick={() => ctx.openLink(externalUrl)}>
                Open on forum
              </button>
            )}
          </span>
        ) : undefined}
      />

      <DatasetPanel title="Topic" descriptor={ctx.descriptors.topic_detail} groupLoaded emptyLabel="Topic not found.">
        <KpiRow
          items={[
            { label: "Category", value: pickString(detail, ["category_name", "category"]) || "—" },
            { label: "Posts", value: fmtNum(pickNumber(detail, ["posts_count", "post_count"])) },
            { label: "Replies", value: fmtNum(pickNumber(detail, ["reply_count"])) },
            { label: "Participants", value: fmtNum(pickNumber(detail, ["participant_count"])) },
            { label: "Views", value: fmtNum(pickNumber(detail, ["views"])) },
            { label: "Likes", value: fmtNum(pickNumber(detail, ["like_count"])) },
            { label: "Last activity", value: pickString(detail, ["last_posted_at"]).slice(0, 10) || "—" },
          ]}
        />
        {pickNumber(detail, ["gip_number"]) !== null && (
          <p className="gov-caption">Referenced GIP: <GipBadge gip={pickNumber(detail, ["gip_number"])} /></p>
        )}
      </DatasetPanel>

      {/* Rendered only when the topic HAS polls — most topics carry none, and
          a permanent empty panel would be noise. A failed topic_polls load
          still surfaces through the entity bundle's load warnings. */}
      {pollViews.length > 0 && (
        <DatasetPanel title="Polls" descriptor={ctx.descriptors.topic_polls} groupLoaded emptyLabel="No polls in this topic.">
          <div className="gov-polls">
            {pollViews.map((poll) => (
              <div key={poll.pollId} className="gov-poll">
                <div className="gov-post__head">
                  <span>{poll.pollName || "poll"}</span>
                  {poll.postNumber !== null && (
                    <button
                      type="button"
                      className="gov-post__reply"
                      title="Jump to the poll-bearing post"
                      onClick={() => scrollToPost(poll.postNumber as number)}
                    >
                      Post #{poll.postNumber}
                    </button>
                  )}
                  <span className={`gov-state-chip gov-state-chip--${poll.status === "open" ? "active" : poll.status}`}>
                    {poll.status || "—"}
                  </span>
                  <span>· {fmtNum(poll.voters)} voters</span>
                  {poll.closeAt && <span>· closes {poll.closeAt.slice(0, 10)}</span>}
                </div>
                {poll.resultsHidden ? (
                  <p className="gov-caption">Results hidden by this poll's settings (visible on vote or close).</p>
                ) : !poll.hasVotes ? (
                  <p className="gov-caption">No votes yet.</p>
                ) : (
                  <ChoiceBars
                    entries={poll.entries}
                    quorum={null}
                    scoresTotal={poll.voters}
                    unitLabel="votes"
                    rankedNote={poll.pollType === "multiple"
                      ? "Multiple-choice poll — shares are of total selections and can exceed the voter count."
                      : undefined}
                  />
                )}
              </div>
            ))}
          </div>
        </DatasetPanel>
      )}

      <DatasetPanel
        title="Likes over time"
        descriptor={ctx.descriptors.topic_likes_activity}
        groupLoaded
        emptyLabel="No attributed likes recorded for this topic (Discourse exposes who-liked for only part of the counter-tracked likes)."
      >
        {likesRows.length > 0 && (
          <>
            <ChartCard
              chartId="gov-topic-likes"
              hideId
              sql={ctx.descriptors.topic_likes_activity?.sql}
              sourceModel="governance_db"
              spec={likesSpec}
            />
            <p className="gov-caption">
              Attributed likes on this topic over time (daily buckets for short-lived topics,
              weekly otherwise), stacked by which post received them — top posts named with
              their author, the rest counted as Other. Post numbers match the thread below.
            </p>
          </>
        )}
      </DatasetPanel>

      <DatasetPanel
        title="Posts"
        descriptor={postsDescriptor}
        groupLoaded
        emptyLabel="No posts."
        collapsible
        open={postsOpen}
        onToggle={setPostsOpen}
        collapsedLabel={`Show thread${(() => {
          const count = pickNumber(detail, ["posts_count", "post_count"]);
          return count !== null ? ` (${fmtNum(count)} posts)` : "";
        })()}`}
        collapsedContent={
          <dl className="gov-topic-brief">
            {pickNumber(detail, ["gip_number"]) !== null && (
              <><dt>GIP</dt><dd>#{pickNumber(detail, ["gip_number"])}</dd></>
            )}
            <dt>Title</dt><dd>{title}</dd>
            {openerUsername && <><dt>Author</dt><dd>{openerUsername}</dd></>}
            <dt>Status</dt>
            <dd>{phaseTag ? phaseTag.replace(/-/g, " ") : status || "—"}</dd>
            {(typeTag || pickString(detail, ["category_name", "category"])) && (
              <><dt>Type</dt><dd>{typeTag ?? pickString(detail, ["category_name", "category"])}</dd></>
            )}
            {createdLong && <><dt>Created</dt><dd>{createdLong}</dd></>}
          </dl>
        }
      >
        <div className="gov-posts">
          {posts.map((post) => (
            <article
              key={post.post_number}
              id={`gov-post-${post.post_number}`}
              className={`gov-post${post.post_number === 1 ? " is-opener" : ""}`}
            >
              <div className="gov-post__head">
                <span>#{post.post_number}</span>
                <button
                  type="button"
                  className="gov-post__author"
                  disabled={!post.user_id}
                  title={post.user_id ? "Open contributor profile" : "Contributor id unavailable"}
                  onClick={() => {
                    if (post.user_id) ctx.onEntity("forum_user", post.user_id);
                  }}
                >
                  {post.username || "unknown"}
                </button>
                {post.post_number === 1 && <span className="gov-state-chip gov-state-chip--active">opener</span>}
                {post.reply_to !== null && (
                  <button type="button" className="gov-post__reply" onClick={() => scrollToPost(post.reply_to as number)}>
                    in reply to #{post.reply_to}
                  </button>
                )}
                <span>{post.created_at.slice(0, 16).replace("T", " ")}</span>
                <span>· {fmtNum(post.like_count)} likes · {fmtNum(post.reads)} reads</span>
              </div>
              <PostBody post={post} openLink={ctx.openLink} />
            </article>
          ))}
        </div>
        {nextToken && (
          <div className="gov-actions">
            <AsyncButton
              variant="secondary"
              loadingLabel="Loading posts"
              onClick={async () => {
                const page = await ctx.fetchRows(ctx.viewId, "topic_posts", nextToken);
                if (!page) return;
                setRows((current) => [...current, ...(page.rows ?? [])]);
                setNextToken(page.next_page_token ?? "");
              }}
            >
              Load more posts
            </AsyncButton>
          </div>
        )}
      </DatasetPanel>

      <div className="gov-actions">
        <ExportCsvButton
          viewId={ctx.viewId}
          datasetKey="topic_posts"
          descriptor={postsDescriptor}
          fetchRows={ctx.fetchRows}
          scope={`topic_${topicId}`}
          label="Export posts CSV"
          excludeColumns={["raw_markdown", "raw", "cooked_html", "cooked"]}
        />
        <AskCerebroButton state={ctx.state} aggregates={ctx.aggregates} sendMessage={ctx.sendMessage} />
      </div>
      <SignalingNote />
    </div>
  );
}
