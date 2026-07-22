import { useMemo, type MouseEvent } from "react";
import { GOV_IMAGE_URL_ATTR, sanitizeCookedHtml } from "../model/sanitize";

const HTTPS_ONLY = /^https:\/\//i;

// Fallback render path for Discourse cooked HTML (posts without raw
// markdown). The HTML is DOMPurify-sanitized by sanitizeCookedHtml (https-only
// URLs, no <img> — image chips instead); a delegated click-capture handler
// prevents default navigation and routes https hrefs (and image-chip URLs)
// through the host `openLink` capability.

export function SafeHtml({ html, openLink, className }: {
  html: string;
  openLink: (url: string) => void;
  className?: string;
}) {
  const sanitized = useMemo(() => sanitizeCookedHtml(html), [html]);

  const onClickCapture = (event: MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement | null;
    if (!target) return;
    const chip = target.closest(`[${GOV_IMAGE_URL_ATTR}]`);
    if (chip) {
      event.preventDefault();
      const url = chip.getAttribute(GOV_IMAGE_URL_ATTR) ?? "";
      if (HTTPS_ONLY.test(url)) openLink(url);
      return;
    }
    const anchor = target.closest("a");
    if (anchor) {
      // Always neutralize the native navigation; only https hrefs proceed
      // (sanitization already dropped everything else, belt-and-braces here).
      event.preventDefault();
      const href = anchor.getAttribute("href") ?? "";
      if (HTTPS_ONLY.test(href)) openLink(href);
    }
  };

  if (!sanitized.html) {
    return <div className={`gov-post-body${className ? ` ${className}` : ""}`} />;
  }
  return (
    <div
      className={`gov-post-body gov-post-body--html${className ? ` ${className}` : ""}`}
      onClickCapture={onClickCapture}
      // Sanitized upstream by sanitizeCookedHtml (DOMPurify, frozen config).
      dangerouslySetInnerHTML={{ __html: sanitized.html }}
    />
  );
}
