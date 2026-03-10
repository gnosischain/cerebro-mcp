import type { HtmlSection } from "../types";

/**
 * Split an HTML string by <h2> tags into tabbed sections.
 * Returns an array of { title, html } objects.
 * If there are fewer than 2 h2 sections, returns a single section with the full HTML.
 */
export function parseHtmlSections(html: string): HtmlSection[] {
  if (!html) return [];

  // Split by <h2> tags, keeping the tag content
  const parts = html.split(/(?=<h2[^>]*>)/i);

  const sections: HtmlSection[] = [];

  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) continue;

    // Extract title from <h2>...</h2>
    const h2Match = trimmed.match(/<h2[^>]*>(.*?)<\/h2>/i);
    if (h2Match) {
      // Strip any HTML tags from the title text
      const title = h2Match[1].replace(/<[^>]*>/g, "").trim();
      sections.push({ title, html: trimmed });
    } else {
      // Content before any h2 — prepend to first section or create standalone
      if (sections.length > 0) {
        sections[0].html = trimmed + sections[0].html;
      } else {
        sections.push({ title: "Overview", html: trimmed });
      }
    }
  }

  // If only one section, return it without tabs
  if (sections.length < 2) {
    return [{ title: "Report", html }];
  }

  return sections;
}
