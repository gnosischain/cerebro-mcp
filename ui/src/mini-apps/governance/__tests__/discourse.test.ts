import { describe, expect, it } from "vitest";

import { pickPostBody, preprocessDiscourseRaw } from "../model/discourse";

describe("preprocessDiscourseRaw", () => {
  it("converts an attributed [quote] block to a markdown blockquote with attribution", () => {
    const raw = '[quote="bob, post:2, topic:12131"]We should fund it.[/quote]\n\nI agree.';
    const out = preprocessDiscourseRaw(raw);
    expect(out).toContain("> **bob (post 2):**");
    expect(out).toContain("> We should fund it.");
    expect(out).toContain("I agree.");
    expect(out).not.toContain("[quote");
    expect(out).not.toContain("[/quote]");
  });

  it("converts a bare [quote] (no attrs) without an attribution line", () => {
    const out = preprocessDiscourseRaw("[quote]just words[/quote]");
    expect(out).toContain("> just words");
    expect(out).not.toContain("**");
  });

  it("handles nested quotes as nested blockquote levels", () => {
    const raw = '[quote="alice"]outer [quote="bob"]inner[/quote] tail[/quote]';
    const out = preprocessDiscourseRaw(raw);
    expect(out).toContain("> **alice:**");
    // the inner quote was blockquoted first, then re-prefixed by the outer pass
    expect(out).toContain("> > inner");
    expect(out).not.toContain("[quote");
  });

  it("strips [details=...] and [spoiler] to their inner text", () => {
    expect(preprocessDiscourseRaw('[details="Summary"]hidden body[/details]')).toBe("hidden body");
    expect(preprocessDiscourseRaw("[spoiler]surprise[/spoiler]")).toBe("surprise");
  });

  it("strips unknown [tag]...[/tag] pairs to inner text", () => {
    expect(preprocessDiscourseRaw("[poll type=regular]option list[/poll]")).toBe("option list");
    expect(preprocessDiscourseRaw("[wrap=notice]note[/wrap]")).toBe("note");
  });

  it("passes plain markdown through untouched", () => {
    const markdown = [
      "# Heading",
      "",
      "Some **bold** text with a [link](https://forum.gnosis.io/t/x/1).",
      "",
      "- [ ] task item",
      "- list item",
      "",
      "| a | b |",
      "|---|---|",
      "| 1 | 2 |",
    ].join("\n");
    expect(preprocessDiscourseRaw(markdown)).toBe(markdown);
  });

  it("drops an unmatched [quote] open tag but keeps its content", () => {
    const out = preprocessDiscourseRaw("[quote]dangling content");
    expect(out).toContain("dangling content");
    expect(out).not.toContain("[quote]");
  });

  it("returns empty for empty input", () => {
    expect(preprocessDiscourseRaw("")).toBe("");
  });
});

describe("pickPostBody — frozen fallback chain raw -> cooked -> plain_text", () => {
  it("prefers raw markdown when present", () => {
    expect(pickPostBody({ raw_markdown: "# hi", cooked_html: "<p>hi</p>", plain_text: "hi" }))
      .toEqual({ mode: "markdown", body: "# hi" });
  });

  it("falls back to cooked HTML when raw is empty or whitespace", () => {
    expect(pickPostBody({ raw_markdown: "  \n ", cooked_html: "<p>hi</p>", plain_text: "hi" }))
      .toEqual({ mode: "html", body: "<p>hi</p>" });
    expect(pickPostBody({ cooked_html: "<p>hi</p>" }))
      .toEqual({ mode: "html", body: "<p>hi</p>" });
  });

  it("falls back to plain text when both raw and cooked are absent", () => {
    expect(pickPostBody({ raw_markdown: "", cooked_html: "", plain_text: "only text" }))
      .toEqual({ mode: "text", body: "only text" });
    expect(pickPostBody({})).toEqual({ mode: "text", body: "" });
  });
});
