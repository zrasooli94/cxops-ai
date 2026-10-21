import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import { reviewApi, ReviewApiError, REVIEW_API } from "./api.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..", "..", "..");
const page = readFileSync(join(ROOT, "frontend/src/app/(control-center)/evaluations/claim-review/page.tsx"), "utf8");
const workspace = readFileSync(join(ROOT, "frontend/src/app/(control-center)/evaluations/claim-review/review-workspace.tsx"), "utf8");
const preview = readFileSync(join(ROOT, "frontend/src/app/(control-center)/evaluations/claim-review/synthetic-api-preview.tsx"), "utf8");
const backend = readFileSync(join(ROOT, "app/api/routes/claim_review.py"), "utf8");
const bff = readFileSync(join(ROOT, "frontend/src/app/api/backend/[...path]/route.ts"), "utf8");

function spy(status = 200, body: unknown = {}) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const fake = (async (url: RequestInfo | URL, init: RequestInit) => {
    calls.push({ url: String(url), init });
    return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
  }) as typeof fetch;
  return { calls, fake };
}

describe("C12 same-origin scoped API adapter", () => {
  it("never calls the backend directly or selects tenant or actor", async () => {
    const mock = spy(201, { id: 1, dataset_id: "c6-fictional-faq-20261021", item_count: 12 });
    await reviewApi.createBatch(mock.fake);
    assert.equal(mock.calls[0].url, REVIEW_API + "/synthetic-batches");
    assert.equal(mock.calls[0].init.method, "POST");
    assert.equal(mock.calls[0].init.body, undefined);
    assert.deepEqual(mock.calls[0].init.headers, { "content-type": "application/json" });
    assert.equal(mock.calls[0].init.cache, "no-store");
    assert.match(bff, /getAuthHeader\(\)/);
    assert.match(bff, /getActiveOrganizationId\(\)/);
    assert.match(backend, /claim_review_synthetic_api_enabled/);
    assert.match(backend, /config\.environment != "development"/);
  });

  it("assigns exactly two subjects without sending actor or tenant fields", async () => {
    const mock = spy(201, { batch_id: 13, assigned_slots: 2 });
    await reviewApi.assign(13, " c11-r-a ", "c11-r-b", mock.fake);
    assert.equal(mock.calls[0].url, REVIEW_API + "/batches/13/assignments");
    assert.deepEqual(JSON.parse(String(mock.calls[0].init.body)), {
      reviewer_one_subject: "c11-r-a", reviewer_two_subject: "c11-r-b",
    });
    assert.throws(() => reviewApi.assign(13, "a", "a", mock.fake), /Distinct/);
    assert.throws(() => reviewApi.assign(-1, "a", "b", mock.fake), /Invalid batch/);
  });

  it("queues only the requesting user's own result; no actor selector", async () => {
    const mock = spy(200, { batch_id: 13, total: 0, items: [] });
    await reviewApi.queue(13, mock.fake);
    assert.equal(mock.calls[0].url, REVIEW_API + "/batches/13/my-queue");
    assert.equal(mock.calls[0].init.method, "GET");
    assert.equal(mock.calls[0].init.body, undefined);
  });

  it("sends only an allowed label and bounded evidence fields", async () => {
    const mock = spy(201, { id: 2, label: "contradicted", event_kind: "review" });
    await reviewApi.submit(13, "aster-direct-contradiction:c1",
      "contradicted", "Aster bicycle repairs are not free.", "", mock.fake);
    assert.equal(mock.calls[0].url,
      REVIEW_API + "/batches/13/reviews/aster-direct-contradiction:c1");
    assert.deepEqual(JSON.parse(String(mock.calls[0].init.body)), {
      label: "contradicted", evidence_excerpt: "Aster bicycle repairs are not free.",
      rationale: null,
    });
    assert.throws(() => reviewApi.submit(13, "../../other", "supported", "e", "", mock.fake), /Invalid claim/);
    assert.equal(mock.calls.length, 1);
  });

  it("maps HTTP errors to bounded messages without exposing reviewer payload", async () => {
    for (const code of [401, 403, 404, 409, 422, 503]) {
      const fake = spy(code, { detail: "internal PRIVATE REVIEWER NAME" });
      await assert.rejects(reviewApi.queue(9, fake.fake), (error: unknown) => {
        assert.ok(error instanceof ReviewApiError);
        assert.equal(error.status, code);
        assert.doesNotMatch(error.message, /PRIVATE|REVIEWER NAME/);
        return true;
      });
    }
  });
});

describe("C12 source boundaries and dev flag", () => {
  it("server hard-disables in production and passes flag only after tenant RBAC", () => {
    assert.match(page, /process\.env\.NODE_ENV !== "production"/);
    assert.match(page, /CXOPS_C12_SYNTHETIC_REVIEW_PREVIEW === "true"/);
    assert.match(page, /mayUseClaimReview\(authorization, activeOrganizationId\)/);
    assert.ok(page.indexOf("mayUseClaimReview") < page.indexOf("previewEnabled ="));
    assert.match(page, /previewEnabled=\{previewEnabled\}/);
    assert.match(workspace, /\{previewEnabled && \(/);
    assert.match(workspace, /SyntheticApiPreview organizationId=\{authorizedOrganizationId\}/);
  });

  it("client never passes user-chosen org/actor headers and checks stale scope", () => {
    assert.match(preview, /auth\.organizationId === organizationId/);
    assert.match(preview, /scope\.current !== orgAtStart/);
    assert.match(preview, /entry\?\.fingerprint/);
    assert.match(preview, /reviewCore\.validateWorksheet\(/);
    assert.match(preview, /reviewApi\.queue/);
    assert.match(preview, /reviewApi\.submit/);
    const executablePreview = preview.replace(/\/\*[\s\S]*?\*\//g, "");
    assert.doesNotMatch(executablePreview, /localStorage|indexedDB|Authorization:|Bearer |X-CXOps-Organization-ID|releaseDecision|release-decision/);
  });
});
