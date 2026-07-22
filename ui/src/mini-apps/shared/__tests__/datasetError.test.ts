import { describe, expect, it } from "vitest";

import { datasetError } from "../datasetError";

describe("datasetError (stub-descriptor failure contract)", () => {
  it("prefers the explicit error message", () => {
    expect(datasetError({ provenance: { coverage: { error: "boom", warning_codes: ["query_failed"] } } })).toBe("boom");
  });

  it("falls back to a generic message on the query_failed code", () => {
    expect(datasetError({ provenance: { coverage: { warning_codes: ["query_failed"] } } })).toBe("Query failed.");
  });

  it("returns empty for clean or missing coverage", () => {
    expect(datasetError({ provenance: { coverage: { warning_codes: [] } } })).toBe("");
    expect(datasetError({ provenance: {} })).toBe("");
    expect(datasetError(undefined)).toBe("");
  });
});
