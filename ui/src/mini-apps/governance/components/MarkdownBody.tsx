import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const HTTPS_ONLY = /^https:\/\//i;

// Markdown render path — used for BOTH proposal bodies (Snapshot markdown)
// and forum raw markdown (after preprocessDiscourseRaw). Policy:
//   - skipHtml: raw HTML embedded in markdown is never rendered;
//   - urlTransform allows https URLs only (everything else becomes "#");
//   - anchors route through the host `openLink` capability (default
//     navigation is always prevented);
//   - images render as an "[image] — open on forum" chip, never inline.

export function MarkdownBody({ body, openLink, className }: {
  body: string;
  openLink: (url: string) => void;
  className?: string;
}) {
  return (
    <div className={`gov-post-body gov-post-body--markdown${className ? ` ${className}` : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={(url) => (HTTPS_ONLY.test(url) ? url : "#")}
        components={{
          a: ({ href, children }) => (
            <a
              className="gov-link"
              href={href ?? "#"}
              onClick={(event) => {
                event.preventDefault();
                if (href && HTTPS_ONLY.test(href)) openLink(href);
              }}
            >
              {children}
            </a>
          ),
          img: ({ src }) => {
            const url = typeof src === "string" && HTTPS_ONLY.test(src) ? src : "";
            return (
              <button
                type="button"
                className="gov-image-chip"
                disabled={!url}
                onClick={() => {
                  if (url) openLink(url);
                }}
              >
                [image] — open on forum
              </button>
            );
          },
        }}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}
