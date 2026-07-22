// @vitest-environment jsdom

import DOMPurify from "dompurify";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GOV_IMAGE_URL_ATTR, sanitizeCookedHtml, stripToText } from "../model/sanitize";

afterEach(() => vi.restoreAllMocks());

describe("sanitizeCookedHtml — rich allowlist", () => {
  it("removes script tags AND their content", () => {
    const { html } = sanitizeCookedHtml("<p>Hello <script>alert(1)</script>world</p>");
    expect(html).toContain("Hello");
    expect(html).toContain("world");
    expect(html).not.toContain("script");
    expect(html).not.toContain("alert(1)");
  });

  it("strips event-handler attributes", () => {
    const { html } = sanitizeCookedHtml('<p onclick="x()" onerror="y()">hi</p><div onload="z()">ok</div>');
    expect(html).toContain("hi");
    expect(html).not.toContain("onclick");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("onload");
  });

  it("kills javascript:, data:, vbscript:, http: and protocol-relative hrefs (https-only)", () => {
    for (const href of [
      "javascript:alert(1)",
      "data:text/html;base64,xxxx",
      "vbscript:evil",
      "http://insecure.example",
      "//protocol-relative.example",
    ]) {
      const { html } = sanitizeCookedHtml(`<a href="${href}">text</a>`);
      expect(html).toContain("text");
      expect(html).not.toContain("href");
    }
  });

  it("keeps https hrefs and forces target/rel on anchors", () => {
    const { html } = sanitizeCookedHtml('<a href="https://forum.gnosis.io/t/x/1">topic</a>');
    expect(html).toContain('href="https://forum.gnosis.io/t/x/1"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer nofollow ugc"');
  });

  it("removes forbidden tags: style/iframe/object/embed/form/input/video/audio/math/svg", () => {
    const { html } = sanitizeCookedHtml(
      "<style>p{}</style><iframe src=\"https://x\"></iframe><object></object><embed>"
      + "<form><input value=\"a\"></form><video>v</video><audio>a</audio>"
      + "<math><mi>m</mi></math><svg><circle /></svg><p>kept</p>",
    );
    expect(html).toContain("kept");
    for (const tag of ["<style", "<iframe", "<object", "<embed", "<form", "<input", "<video", "<audio", "<math", "<svg"]) {
      expect(html).not.toContain(tag);
    }
  });

  it("keeps allowlisted structure: blockquote, table, lists, code", () => {
    const input = "<blockquote><p>q</p></blockquote><table><thead><tr><th>h</th></tr></thead>"
      + "<tbody><tr><td>c</td></tr></tbody></table><ul><li>i</li></ul><pre><code>x</code></pre>";
    const { html } = sanitizeCookedHtml(input);
    for (const tag of ["<blockquote", "<table", "<thead", "<tbody", "<th", "<td", "<ul", "<li", "<pre", "<code"]) {
      expect(html).toContain(tag);
    }
  });
});

describe("image chip policy", () => {
  it("replaces <img> with a gov-image-chip span carrying the https URL and reports hadImages", () => {
    const { html, hadImages } = sanitizeCookedHtml('<p><img src="https://forum.gnosis.io/uploads/pic.png" alt="x"></p>');
    expect(hadImages).toBe(true);
    expect(html).not.toContain("<img");
    expect(html).toContain('class="gov-image-chip"');
    expect(html).toContain(`${GOV_IMAGE_URL_ATTR}="https://forum.gnosis.io/uploads/pic.png"`);
    expect(html).toContain("[image]");
  });

  it("non-https image sources get a chip with NO link attribute", () => {
    for (const src of ["http://x/pic.png", "javascript:alert(1)", "data:image/png;base64,xx", "//x/pic.png"]) {
      const { html, hadImages } = sanitizeCookedHtml(`<p><img src="${src}"></p>`);
      expect(hadImages).toBe(true);
      expect(html).toContain("gov-image-chip");
      expect(html).not.toContain(GOV_IMAGE_URL_ATTR);
    }
  });

  it("reports hadImages=false without images", () => {
    expect(sanitizeCookedHtml("<p>no pictures</p>").hadImages).toBe(false);
  });

  it("strips a non-https chip URL forged directly in the input", () => {
    const { html } = sanitizeCookedHtml(`<span class="gov-image-chip" ${GOV_IMAGE_URL_ATTR}="javascript:alert(1)">[image]</span>`);
    expect(html).not.toContain(GOV_IMAGE_URL_ATTR);
  });
});

describe("stripToText", () => {
  it("strips tags but keeps text, with whitespace normalized", () => {
    expect(stripToText("<p>Hello   <b>world</b></p>\n<p>again</p>")).toBe("Hello world again");
  });

  it("drops script content entirely", () => {
    expect(stripToText("<p>ok</p><script>alert(1)</script>")).toBe("ok");
  });

  it("returns empty for empty input", () => {
    expect(stripToText("")).toBe("");
  });
});

describe("fallback behavior", () => {
  it("falls back to stripToText when sanitize output is empty from non-empty input", () => {
    const spy = vi.spyOn(DOMPurify, "sanitize").mockImplementationOnce(() => "");
    const { html } = sanitizeCookedHtml("<p>hello</p>");
    expect(spy).toHaveBeenCalled();
    expect(html).toBe("hello");
  });

  it("falls back to stripToText when sanitize throws", () => {
    vi.spyOn(DOMPurify, "sanitize").mockImplementationOnce(() => {
      throw new Error("boom");
    });
    const { html } = sanitizeCookedHtml("<p>hi &amp; bye</p>");
    expect(html).toBe("hi &amp; bye"); // stripToText decodes, escapeHtml re-encodes
  });

  it("returns empty (no fallback) for empty/whitespace input", () => {
    expect(sanitizeCookedHtml("")).toEqual({ html: "", hadImages: false });
    expect(sanitizeCookedHtml("   \n ")).toEqual({ html: "", hadImages: false });
  });
});

describe("per-call config and hook isolation", () => {
  it("does not leak the rich config into later default sanitize calls", () => {
    sanitizeCookedHtml('<a href="https://x.example">x</a><img src="https://x.example/p.png">');
    // Default DOMPurify config still allows <img> (RICH_CONFIG was per-call).
    expect(String(DOMPurify.sanitize('<img src="x.png">'))).toContain("<img");
  });

  it("removes the anchor hook after every call", () => {
    sanitizeCookedHtml('<a href="https://x.example">x</a>');
    const later = String(DOMPurify.sanitize('<a href="https://y.example">y</a>'));
    expect(later).not.toContain('target="_blank"');
    expect(later).not.toContain("noopener");
  });

  it("removes the hook even when sanitize throws", () => {
    vi.spyOn(DOMPurify, "sanitize").mockImplementationOnce(() => {
      throw new Error("boom");
    });
    sanitizeCookedHtml("<p>x</p>");
    vi.restoreAllMocks();
    const later = String(DOMPurify.sanitize('<a href="https://y.example">y</a>'));
    expect(later).not.toContain('target="_blank"');
  });
});
