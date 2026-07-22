// @vitest-environment jsdom

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarkdownBody } from "../components/MarkdownBody";

function render(body: string): string {
  return renderToStaticMarkup(<MarkdownBody body={body} openLink={() => {}} />);
}

describe("MarkdownBody — untrusted markdown policy", () => {
  it("does NOT render raw HTML embedded in the body (skipHtml)", () => {
    // skipHtml drops the HTML *nodes* — nothing renders as an element, so
    // nothing can execute. Text between tags stays as inert plain text.
    const html = render("Hello <script>alert(1)</script> <b>bold?</b> world");
    expect(html).toContain("Hello");
    expect(html).toContain("world");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<b>");
    expect(html).not.toContain("&lt;script"); // dropped, not escaped-through
  });

  it("neutralizes javascript: links via urlTransform (href becomes #)", () => {
    const html = render("[click me](javascript:alert(1))");
    expect(html).toContain("click me");
    expect(html).not.toContain("javascript:");
    expect(html).toContain('href="#"');
  });

  it("neutralizes data: and http: links too (https-only)", () => {
    for (const url of ["data:text/html;base64,xxxx", "http://insecure.example/x"]) {
      const html = render(`[link](${url})`);
      expect(html).not.toContain(url);
      expect(html).toContain('href="#"');
    }
  });

  it("renders https links through the anchor override", () => {
    const html = render("[topic](https://forum.gnosis.io/t/x/12131)");
    expect(html).toContain('href="https://forum.gnosis.io/t/x/12131"');
    expect(html).toContain('class="gov-link"');
    expect(html).toContain("topic");
  });

  it("renders a GFM table", () => {
    const html = render("| a | b |\n|---|---|\n| 1 | 2 |");
    expect(html).toContain("<table");
    expect(html).toContain("<th");
    expect(html).toContain("<td");
  });

  it("renders a markdown image as an [image] chip button, never an <img>", () => {
    const html = render("![screenshot](https://forum.gnosis.io/uploads/pic.png)");
    expect(html).not.toContain("<img");
    expect(html).toContain("gov-image-chip");
    expect(html).toContain("[image]");
  });

  it("image chip is disabled for non-https image sources", () => {
    const html = render("![x](http://insecure.example/pic.png)");
    expect(html).not.toContain("<img");
    expect(html).toContain("gov-image-chip");
    expect(html).toContain("disabled");
    expect(html).not.toContain("http://insecure.example");
  });

  it("renders GFM autolinks as anchors through the override", () => {
    const html = render("see https://snapshot.org/#/gnosis.eth");
    expect(html).toContain('class="gov-link"');
    expect(html).toContain('href="https://snapshot.org/#/gnosis.eth"');
  });
});
