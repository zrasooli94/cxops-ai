import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import {
  c12ReviewerAdminPath,
  c12ReviewerBackendPath,
} from "./server-allowlist.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const frontend = join(HERE, "..", "..", "..");
const route = readFileSync(
  join(frontend, "src", "app", "api", "claim-review", "[...path]", "route.ts"),
  "utf8",
);

describe("C12 closed allowlist for development-only authenticated BFF", () => {
  it("allows exactly the five C11 operations", () => {
    assert.equal(c12ReviewerBackendPath(
      ["synthetic-batches"], "POST",
    ), "/evaluations/claim-reviews/synthetic-batches");
    assert.equal(c12ReviewerBackendPath(
      ["batches", "1", "assignments"], "POST",
    ), "/evaluations/claim-reviews/batches/1/assignments");
    assert.equal(c12ReviewerBackendPath(
      ["batches", "12", "my-queue"], "GET",
    ), "/evaluations/claim-reviews/batches/12/my-queue");
    assert.equal(c12ReviewerBackendPath(
      ["batches", "12", "reviews", "aster-direct-contradiction:c1"], "POST",
    ), "/evaluations/claim-reviews/batches/12/reviews/aster-direct-contradiction:c1");
    assert.equal(c12ReviewerBackendPath(
      ["batches", "12", "adjudications", "aster-direct-contradiction:c1"], "POST",
    ), "/evaluations/claim-reviews/batches/12/adjudications/aster-direct-contradiction:c1");
  });

  it("refuses all unrelated paths, query segments, methods and path escapes", () => {
    for (const [path, method] of [
      [["synthetic-batches"], "GET"],
      [["synthetic-batches"], "PUT"],
      [["synthetic-batches", "1"], "POST"],
      [["batches", "0", "my-queue"], "GET"],
      [["batches", "-1", "my-queue"], "GET"],
      [["batches", "1.5", "my-queue"], "GET"],
      [["batches", "2147483648", "my-queue"], "GET"],
      [["batches", "1", "my-queue"], "POST"],
      [["batches", "1", "assignments"], "GET"],
      [["batches", "1", "reviews", "../other"], "POST"],
      [["batches", "1", "reviews", "a:b%2fadmin"], "POST"],
      [["batches", "1", "adjudications", "a:b", "other"], "POST"],
      [["batches", "1", "delete"], "POST"],
      [["customers", "1"], "GET"],
      [["zendesk", "secret"], "GET"],
      [["batches", "1", "release-decision", "a:b"], "POST"],
    ] as const) {
      assert.equal(c12ReviewerBackendPath(path, method), null);
    }
  });

  it("admin path classification cannot be widened by client labels", () => {
    assert.equal(c12ReviewerAdminPath(["synthetic-batches"]), true);
    assert.equal(c12ReviewerAdminPath(["batches", "4", "assignments"]), true);
    assert.equal(c12ReviewerAdminPath(["batches", "4", "my-queue"]), false);
    assert.equal(c12ReviewerAdminPath(["batches", "4", "reviews", "a:b"]), false);
  });
});

describe("C12 dedicated reviewer proxy has its own server-side boundaries", () => {
  it("checks runtime flag and current session before backend requests", () => {
    assert.match(route, /c12SyntheticStaffPreviewEnabled\(\)/);
    assert.match(route, /getActiveOrganizationId\(\)/);
    assert.match(route, /getMyAuthorizationReadOnly\(selectedOrganizationId\)/);
    assert.match(route, /mayUseClaimReview\(authz, selectedOrganizationId\)/);
    assert.match(route, /authz\.capabilities\.includes\("member\.manage"\)/);
    assert.match(route, /classifySessionState\(session\) !== "valid"/);
    assert.match(route, /c12ReviewerBackendPath\(path, request\.method\)/);
  });
  it("never forwards caller's tenant selector, bearer header or arbitrary route", () => {
    assert.match(route, /"X-CXOps-Organization-ID": String\(selectedOrganizationId\)/);
    assert.match(route, /authorization,/);
    assert.doesNotMatch(route, /request\.headers\.get\("authorization"\)/);
    assert.doesNotMatch(route, /request\.headers\.get\("x-cxops-organization-id"\)/i);
    assert.doesNotMatch(route, /request\.nextUrl\.searchParams\.forEach/);
    assert.match(route, /!backendPath \|\| request\.nextUrl\.search\) return refuse\(404, "route"\)/);
    assert.match(route, /origin !== request\.nextUrl\.origin/);
    assert.match(route, /text\.length > 4096/);
    assert.doesNotMatch(route, /localStorage|IndexedDB|eval\(/);
  });
});
