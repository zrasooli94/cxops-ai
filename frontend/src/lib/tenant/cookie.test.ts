import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { parseActiveOrganizationId } from "./cookie-helpers.ts";

describe("active organization cookie parsing", () => {
  it("parses a positive integer id", () => {
    assert.equal(parseActiveOrganizationId("42"), 42);
  });

  it("returns null for undefined", () => {
    assert.equal(parseActiveOrganizationId(undefined), null);
  });

  it("returns null for empty string", () => {
    assert.equal(parseActiveOrganizationId(""), null);
  });

  it("returns null for non-numeric value", () => {
    assert.equal(parseActiveOrganizationId("not-a-number"), null);
  });

  it("returns null for floating-point value", () => {
    assert.equal(parseActiveOrganizationId("3.14"), null);
  });

  it("returns null for zero", () => {
    assert.equal(parseActiveOrganizationId("0"), null);
  });

  it("returns null for negative id", () => {
    assert.equal(parseActiveOrganizationId("-1"), null);
  });

  it("returns null for whitespace-only value", () => {
    assert.equal(parseActiveOrganizationId("   "), null);
  });
});
