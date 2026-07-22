// DOMPurify sanitization for Discourse cooked HTML (the fallback render path
// when a post has no raw markdown). Frozen policy:
//
//   - strict tag/attr allowlists, https-only URLs (javascript:, data:,
//     http:, protocol-relative all die on ALLOWED_URI_REGEXP);
//   - NO <img> — images become a `span.gov-image-chip` placeholder carrying
//     the https source URL in `data-gov-image-url` (routed through host
//     openLink by the chip component; non-https sources get no link);
//   - anchors are forced to target="_blank" rel="noopener noreferrer
//     nofollow ugc" via a per-call afterSanitizeAttributes hook (added and
//     removed around each sanitize so no config/hook state leaks);
//   - config objects are passed per call — never DOMPurify.setConfig;
//   - empty output from non-empty input, or a sanitizer throw, falls back to
//     stripToText so a post never silently vanishes.

import DOMPurify, { type Config } from "dompurify";

/** Chip attribute produced ONLY by our own image pre-pass. It must be in
 * ALLOWED_ATTR to survive sanitization (ALLOW_DATA_ATTR stays false so every
 * other data-* attribute is stripped); the per-call hook re-validates it as
 * https so attacker-authored copies cannot smuggle another scheme. */
export const GOV_IMAGE_URL_ATTR = "data-gov-image-url";

const HTTPS_ONLY = /^https:\/\//i;

export const RICH_CONFIG: Config = {
  ALLOWED_TAGS: [
    "a", "p", "br", "blockquote", "aside", "code", "pre",
    "em", "strong", "b", "i", "s",
    "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td",
    "hr", "span", "div", "small", "sup", "sub",
  ],
  ALLOWED_ATTR: ["href", "title", "alt", "class", "start", GOV_IMAGE_URL_ATTR],
  ALLOWED_URI_REGEXP: HTTPS_ONLY,
  FORBID_TAGS: [
    "style", "script", "iframe", "object", "embed",
    "form", "input", "video", "audio", "math", "svg",
  ],
  ALLOW_DATA_ATTR: false,
};

const STRIP_CONFIG: Config = { ALLOWED_TAGS: [], KEEP_CONTENT: true };

/** Strict last-resort path: strip every tag, keep text content, collapse
 * whitespace. Used for sanitize-failure fallback, excerpts, and CSV. */
export function stripToText(html: string): string {
  if (!html) return "";
  let sanitized: string;
  try {
    sanitized = String(DOMPurify.sanitize(html, STRIP_CONFIG));
  } catch {
    sanitized = "";
  }
  let text = sanitized;
  if (typeof document !== "undefined") {
    // Decode residual entities via an inert template element.
    const template = document.createElement("template");
    template.innerHTML = sanitized;
    text = template.content.textContent ?? "";
  }
  return text.replace(/\s+/g, " ").trim();
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Pre-sanitize pass: replace every <img> with the chip placeholder inside
 * an inert <template> (nothing executes during template parsing). */
function replaceImages(html: string): { html: string; hadImages: boolean } {
  if (typeof document === "undefined") return { html, hadImages: false };
  const template = document.createElement("template");
  template.innerHTML = html;
  const images = Array.from(template.content.querySelectorAll("img"));
  for (const img of images) {
    const chip = document.createElement("span");
    chip.className = "gov-image-chip";
    const src = img.getAttribute("src") ?? "";
    if (HTTPS_ONLY.test(src)) chip.setAttribute(GOV_IMAGE_URL_ATTR, src);
    chip.textContent = "[image] — open on forum";
    img.replaceWith(chip);
  }
  return { html: template.innerHTML, hadImages: images.length > 0 };
}

export interface SanitizedHtml {
  html: string;
  hadImages: boolean;
}

export function sanitizeCookedHtml(html: string): SanitizedHtml {
  const input = html ?? "";
  if (input.trim() === "") return { html: "", hadImages: false };
  const pre = replaceImages(input);

  const hook = (node: Element) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer nofollow ugc");
    }
    if (node.hasAttribute(GOV_IMAGE_URL_ATTR)) {
      const url = node.getAttribute(GOV_IMAGE_URL_ATTR) ?? "";
      if (!HTTPS_ONLY.test(url)) node.removeAttribute(GOV_IMAGE_URL_ATTR);
    }
  };

  let output: string | null = null;
  DOMPurify.addHook("afterSanitizeAttributes", hook);
  try {
    output = String(DOMPurify.sanitize(pre.html, RICH_CONFIG));
  } catch {
    output = null; // sanitizer threw — fall back below, after hook removal
  } finally {
    // Pop the hook added above — add/remove is strictly per call, so config
    // and hook state never leak into other DOMPurify users.
    DOMPurify.removeHook("afterSanitizeAttributes");
  }

  if (output === null || output.trim() === "") {
    // Sanitize threw, or produced empty output from non-empty input: fall
    // back to the strict text path so the post never silently vanishes.
    return { html: escapeHtml(stripToText(input)), hadImages: pre.hadImages };
  }
  return { html: output, hadImages: pre.hadImages };
}
