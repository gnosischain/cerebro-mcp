// Discourse raw-markdown preprocessing. Discourse "raw" is markdown plus a
// few BBCode-style constructs react-markdown does not understand:
//
//   [quote="user, post:3, topic:123"] ... [/quote]  (attrs optional, nests)
//   [details="Summary"] ... [/details]
//   [spoiler] ... [/spoiler]
//   [poll ...] ... [/poll] and other unknown [tag]...[/tag] pairs
//
// Quotes become markdown blockquotes with an attribution line; every other
// recognized-or-unknown BBCode pair is stripped to its inner text. Plain
// markdown passes through untouched. Pure functions, fixture-tested.

/** Human attribution from a quote attr like `user, post:3, topic:123`. */
function parseQuoteAttribution(attr: string | undefined): string {
  if (!attr) return "";
  const cleaned = attr.replace(/^["']|["']$/g, "").trim();
  if (!cleaned) return "";
  const segments = cleaned.split(",").map((s) => s.trim()).filter(Boolean);
  if (segments.length === 0) return "";
  const user = segments[0];
  const post = segments.find((s) => /^post:\s*\d+$/i.test(s));
  const postNumber = post ? post.split(":")[1].trim() : "";
  return postNumber ? `${user} (post ${postNumber})` : user;
}

function toBlockquote(inner: string, attribution: string): string {
  const lines = inner.split("\n");
  const quoted = [
    ...(attribution ? [`> **${attribution}:**`] : []),
    ...lines.map((line) => (line.length > 0 ? `> ${line}` : ">")),
  ].join("\n");
  return `\n\n${quoted}\n\n`;
}

/** Convert every (possibly nested) [quote]...[/quote] block to a markdown
 * blockquote. Inner content is converted first, so nested quotes become
 * nested blockquote levels. */
function convertQuotes(text: string): string {
  const openRe = /\[quote(?:=([^\]]*))?\]/i;
  let result = "";
  let rest = text;
  for (let guard = 0; guard < 500; guard += 1) {
    const open = openRe.exec(rest);
    if (!open) return result + rest;
    result += rest.slice(0, open.index);
    const afterOpen = open.index + open[0].length;
    // Find the matching close, counting nested opens.
    const tokenRe = /\[quote(?:=[^\]]*)?\]|\[\/quote\]/gi;
    tokenRe.lastIndex = afterOpen;
    let depth = 1;
    let closeStart = -1;
    let closeEnd = -1;
    let token: RegExpExecArray | null;
    while ((token = tokenRe.exec(rest)) !== null) {
      if (token[0].toLowerCase() === "[/quote]") {
        depth -= 1;
        if (depth === 0) {
          closeStart = token.index;
          closeEnd = token.index + token[0].length;
          break;
        }
      } else {
        depth += 1;
      }
    }
    if (closeStart < 0) {
      // Unmatched open tag — drop the tag itself, keep the content.
      rest = rest.slice(afterOpen);
      continue;
    }
    const inner = convertQuotes(rest.slice(afterOpen, closeStart)).trim();
    result += toBlockquote(inner, parseQuoteAttribution(open[1]));
    rest = rest.slice(closeEnd);
  }
  return result + rest;
}

/** Strip remaining BBCode-style [tag]...[/tag] pairs (details, spoiler,
 * poll, anything unknown) to their inner text. Innermost-first via a
 * non-greedy match repeated until stable; unpaired brackets (markdown links,
 * task-list checkboxes) never match because a literal [/tag] is required. */
function stripBBCodePairs(text: string): string {
  const pairRe = /\[([a-zA-Z][a-zA-Z0-9_-]*)(?:[=\s][^\]]*)?\]([\s\S]*?)\[\/\1\]/g;
  let out = text;
  for (let guard = 0; guard < 50; guard += 1) {
    const next = out.replace(pairRe, (_full, _tag, inner: string) => inner.trim());
    if (next === out) return out;
    out = next;
  }
  return out;
}

export function preprocessDiscourseRaw(raw: string): string {
  if (!raw) return "";
  return stripBBCodePairs(convertQuotes(raw));
}

export interface PostBodySource {
  raw_markdown?: string | null;
  cooked_html?: string | null;
  plain_text?: string | null;
}

export interface PickedPostBody {
  mode: "markdown" | "html" | "text";
  body: string;
}

/** Frozen body fallback chain: raw markdown -> sanitized cooked HTML ->
 * plain text. Whitespace-only bodies count as absent. */
export function pickPostBody(post: PostBodySource): PickedPostBody {
  const raw = post.raw_markdown ?? "";
  if (raw.trim().length > 0) return { mode: "markdown", body: raw };
  const cooked = post.cooked_html ?? "";
  if (cooked.trim().length > 0) return { mode: "html", body: cooked };
  return { mode: "text", body: post.plain_text ?? "" };
}
