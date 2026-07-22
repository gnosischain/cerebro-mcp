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
import { pickPostBody, preprocessDiscourseRaw } from "../model/discourse";
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
  const slug = pickString(detail, ["slug"]);
  const externalUrl =
    pickString(detail, ["external_url", "url"])
    || (topicId ? `${FORUM_BASE_URL}/t/${slug ? `${slug}/` : ""}${topicId}` : "");
  const status = pickString(detail, ["status"]);
  const links = parseLinks(dataset(ctx, "topic_proposal_links"));

  const scrollToPost = (postNumber: number) => {
    document.getElementById(`gov-post-${postNumber}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="gov-entity">
      <MaIdentity
        label={`FORUM TOPIC · forum.gnosis.io${status ? ` · ${status}` : ""}`}
        value={title}
        onCopy={() => void navigator.clipboard?.writeText(externalUrl || String(topicId))}
        rightSlot={externalUrl ? (
          <button type="button" className="gov-external" onClick={() => ctx.openLink(externalUrl)}>
            Open on forum
          </button>
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

      <DatasetPanel title="Posts" descriptor={postsDescriptor} groupLoaded emptyLabel="No posts.">
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

      <DatasetPanel
        title="Linked Snapshot proposals"
        descriptor={ctx.descriptors.topic_proposal_links}
        groupLoaded
        emptyLabel="No linked proposal found (no proposal declares this topic and no exact GIP-number match)."
      >
        <div className="gov-links">
          {/* topic_proposal_links rows are ALL Snapshot proposals. */}
          {links.map((link, index) => (
            <div key={index} className="gov-links__row">
              <span className={`gov-link-tier gov-link-tier--${link.link_source}`}>{link.link_source}</span>
              <button
                type="button"
                title={link.linked_title}
                onClick={() => ctx.onEntity("proposal", link.linked_id)}
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
