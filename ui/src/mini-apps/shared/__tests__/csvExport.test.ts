// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import { buildCsv, csvCell, downloadCsv } from "../csvExport";

afterEach(() => vi.restoreAllMocks());

describe("csvCell — RFC 4180 quoting", () => {
  it("quotes cells containing comma, quote, CR or LF and doubles embedded quotes", () => {
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell("line1\nline2")).toBe('"line1\nline2"');
    expect(csvCell("plain")).toBe("plain");
  });

  it("emits numbers as plain untouched values — negative numbers are not formulas", () => {
    expect(csvCell(42)).toBe("42");
    expect(csvCell(-7.5)).toBe("-7.5");
    expect(csvCell(0)).toBe("0");
  });

  it("neutralizes formula-leading strings (= + - @ TAB CR) with a quote prefix AND forced quoting", () => {
    expect(csvCell("=SUM(A1:A9)")).toBe("\"'=SUM(A1:A9)\"");
    expect(csvCell("+1234")).toBe("\"'+1234\"");
    expect(csvCell("-cmd")).toBe("\"'-cmd\"");
    expect(csvCell("@handle")).toBe("\"'@handle\"");
    expect(csvCell("\tx")).toBe("\"'\tx\"");
    expect(csvCell("\rx")).toBe("\"'\rx\"");
  });

  it("renders null/undefined as empty", () => {
    expect(csvCell(null)).toBe("");
    expect(csvCell(undefined)).toBe("");
  });

  it("force-quotes when asked, even for benign values", () => {
    expect(csvCell("2026-07-22T00:00:00Z", true)).toBe('"2026-07-22T00:00:00Z"');
    expect(csvCell(42, true)).toBe('"42"');
  });
});

describe("buildCsv", () => {
  it("joins with CRLF and ends with a trailing CRLF", () => {
    const csv = buildCsv(["a", "b"], [[1, 2], [3, 4]]);
    expect(csv).toBe("a,b\r\n1,2\r\n3,4\r\n");
  });

  it("force-quotes DateTime and String-typed columns so spreadsheets keep text", () => {
    const csv = buildCsv(
      ["voter", "vp", "created_at"],
      [[`0x${"aa".repeat(20)}`, 1200.5, "2026-05-03T10:00:00Z"]],
      ["String", "Float64", "DateTime"],
    );
    const [, row] = csv.split("\r\n");
    expect(row).toBe(`"0x${"aa".repeat(20)}",1200.5,"2026-05-03T10:00:00Z"`);
  });

  it("neutralizes a formula-shaped title inside a typed column", () => {
    const csv = buildCsv(["title"], [["=HYPERLINK(\"https://evil\")"]], ["String"]);
    expect(csv).toContain("'=HYPERLINK");
    expect(csv.split("\r\n")[1].startsWith("\"'=")).toBe(true);
  });

  it("round-trips UTF-8 content", () => {
    const csv = buildCsv(["name"], [["Gnosis DAO — Schnaps 🦉"]]);
    expect(csv).toContain("Gnosis DAO — Schnaps 🦉");
  });
});

describe("downloadCsv", () => {
  it("prepends the UTF-8 BOM and triggers an anchor download", async () => {
    let capturedBlob: Blob | null = null;
    const createObjectURL = vi.fn((blob: Blob) => {
      capturedBlob = blob;
      return "blob:fake";
    });
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", Object.assign(Object.create(URL), { createObjectURL, revokeObjectURL }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    downloadCsv("governance_test.csv", "a,b\r\n1,2\r\n");

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake");
    const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(capturedBlob!);
    });
    const bytes = new Uint8Array(buffer);
    // UTF-8 BOM bytes, then the untouched CSV body.
    expect([bytes[0], bytes[1], bytes[2]]).toEqual([0xef, 0xbb, 0xbf]);
    expect(new TextDecoder().decode(bytes.slice(3))).toBe("a,b\r\n1,2\r\n");
    vi.unstubAllGlobals();
  });
});
