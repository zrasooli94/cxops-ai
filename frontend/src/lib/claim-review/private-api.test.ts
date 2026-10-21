import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import {
  C12ReviewApiError,
  C12_REVIEWER_PATH,
  adjudicateSyntheticClaim,
  assignSyntheticReviewers,
  createSyntheticBatch,
  fetchOwnClaimQueue,
  ownQueueMatchesLocalClaim,
  submitOwnClaimReview,
} from "./private-api.ts";
import { c12SyntheticStaffPreviewEnabled } from "./preview.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..", "..", "..");
const routePage = readFileSync(join(
  ROOT, "src", "app", "(control-center)", "evaluations",
  "claim-review", "page.tsx",
), "utf8");
const staffUi = readFileSync(join(
  ROOT, "src", "app", "(control-center)", "evaluations",
  "claim-review", "review-workspace.tsx",
), "utf8");
const panel = readFileSync(join(
  ROOT, "src", "app", "(control-center)", "evaluations",
  "claim-review", "synthetic-api-panel.tsx",
), "utf8");
const HEAD = { fingerprint: "a".repeat(64), review_key: "aster-direct-contradiction:c1" };
const queue = { batch_id: 17, total: 1, items: [{ ...HEAD, completed: false }] };

interface CapturedRequest {
  url: string;
  options?: RequestInit;
}
function fakeResponse(
  value: unknown,
  status = 200,
): { fetcher: typeof fetch; calls: CapturedRequest[] } {
  const calls: CapturedRequest[] = [];
  const fetcher = (async (url: URL | RequestInfo, options?: RequestInit) => {
    calls.push({ url: String(url), options });
    return new Response(JSON.stringify(value), {
      status, headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return { fetcher, calls };
}
function payload(req: CapturedRequest) {
  return req.options?.body
    ? JSON.parse(req.options.body as string) as Record<string, unknown>
    : null;
}

describe("C12 double feature gate never activates by default or in production", () => {
  it("requires both explicit preview=true and ENVIRONMENT development", () => {
    assert.equal(c12SyntheticStaffPreviewEnabled({}), false);
    assert.equal(c12SyntheticStaffPreviewEnabled({
      ENVIRONMENT: "production", CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED: "true",
    }), false);
    assert.equal(c12SyntheticStaffPreviewEnabled({
      ENVIRONMENT: "staging", CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED: "true",
    }), false);
    assert.equal(c12SyntheticStaffPreviewEnabled({
      ENVIRONMENT: "development", CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED: "false",
    }), false);
    assert.equal(c12SyntheticStaffPreviewEnabled({
      ENVIRONMENT: "development", CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED: "true",
    }), true);
    assert.equal(c12SyntheticStaffPreviewEnabled({
      ENVIRONMENT: "development", CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED: "TRUE",
    }), false);
  });
  it("server rather than browser owns preview permission", () => {
    assert.match(routePage, /getMyAuthorizationReadOnly\(activeOrganizationId\)/);
    assert.match(routePage, /mayUseClaimReview\(authorization, activeOrganizationId\)/);
    assert.match(routePage, /syntheticApiPreviewEnabled=\{c12SyntheticStaffPreviewEnabled\(\)\}/);
    assert.match(staffUi, /syntheticApiPreviewEnabled = false/);
    assert.match(staffUi, /\{syntheticApiPreviewEnabled && \(/);
    assert.match(staffUi, /<SyntheticApiPanel/);
    assert.doesNotMatch(staffUi, /localStorage|indexedDB|sessionStorage|dangerouslySetInnerHTML/);
    assert.doesNotMatch(panel, /X-CXOps-Organization-ID|Authorization:|localStorage|indexedDB|dangerouslySetInnerHTML/);
    assert.match(panel, /tenantRef\.current !== requestOrganizationId/);
    assert.match(panel, /key=\{organizationId\}/); // key on component parent
  });
});

describe("C12 closed private API contract over existing authenticated BFF", () => {
  it("batch POST carries no body, tenant, subject or raw source", async () => {
    const stub = fakeResponse({
      id: 17, dataset_id: "c6-fictional-faq-20261021",
      item_count: 12, created_at: "2026-10-21T00:00:00Z",
    });
    const result = await createSyntheticBatch(stub.fetcher);
    assert.equal(result.id, 17);
    assert.equal(stub.calls.length, 1);
    assert.equal(stub.calls[0].url, C12_REVIEWER_PATH + "/synthetic-batches");
    assert.equal(stub.calls[0].options?.method, "POST");
    assert.equal(payload(stub.calls[0]), null);
    assert.equal(stub.calls[0].options?.credentials, "same-origin");
    assert.equal(stub.calls[0].options?.cache, "no-store");
  });

  it("assignments carry exactly two staff subjects, no actor/tenant", async () => {
    const stub = fakeResponse({ batch_id: 17, assigned_slots: 2 });
    await assignSyntheticReviewers(17, "fictional-A", "fictional-B", stub.fetcher);
    assert.deepEqual(payload(stub.calls[0]), {
      reviewer_one_subject: "fictional-A", reviewer_two_subject: "fictional-B",
    });
    assert.deepEqual(Object.keys(payload(stub.calls[0]) ?? {}).sort(), [
      "reviewer_one_subject", "reviewer_two_subject",
    ]);
    assert.throws(() => assignSyntheticReviewers(0, "A", "B", stub.fetcher));
    assert.throws(() => assignSyntheticReviewers(17, "A", "A", stub.fetcher));
    assert.throws(() => assignSyntheticReviewers(17, "A\n", "B", stub.fetcher));
    assert.equal(stub.calls.length, 1, "invalid inputs never reach backend");
  });

  it("own queue exposes only claim hashes and completion, never other votes", async () => {
    const stub = fakeResponse(queue);
    const got = await fetchOwnClaimQueue(17, stub.fetcher);
    assert.deepEqual(got, queue);
    assert.equal(stub.calls[0].options?.method, "GET");
    assert.equal(payload(stub.calls[0]), null);
    assert.equal(stub.calls[0].url, C12_REVIEWER_PATH + "/batches/17/my-queue");
    assert.ok(ownQueueMatchesLocalClaim(got, HEAD.review_key, HEAD.fingerprint));
    assert.equal(ownQueueMatchesLocalClaim(got, HEAD.review_key, "b".repeat(64)), false);
    assert.equal(ownQueueMatchesLocalClaim(got, "other:c1", HEAD.fingerprint), false);
    assert.equal(ownQueueMatchesLocalClaim(null, HEAD.review_key, HEAD.fingerprint), false);
    const completed = { ...got, items: [{ ...HEAD, completed: true }] };
    assert.equal(ownQueueMatchesLocalClaim(completed, HEAD.review_key, HEAD.fingerprint), false);
  });

  it("rejects malicious/invalid queue payloads before rendering", async () => {
    for (const bad of [
      { ...queue, batch_id: 18 },
      { ...queue, total: 12 },
      { ...queue, items: [{ ...HEAD, fingerprint: "not-a-sha256", completed: false }] },
      { ...queue, items: [{ ...HEAD, completed: "false" }] },
    ]) {
      const stub = fakeResponse(bad);
      await assert.rejects(() => fetchOwnClaimQueue(17, stub.fetcher), /contract/);
    }
  });

  it("validated review and adjudication POST only label + exact evidence", async () => {
    const review = fakeResponse({ id: 9, batch_id: 17, review_key: HEAD.review_key,
      event_kind: "review", label: "contradicted", created_at: "2026-10-21T00:00:00Z" }, 201);
    await submitOwnClaimReview(17, HEAD.review_key, {
      label: "contradicted",
      evidence_excerpt: "Aster bicycle repairs are not free.",
      rationale: "not included",
    }, review.fetcher);
    assert.equal(review.calls[0].url, C12_REVIEWER_PATH
      + "/batches/17/reviews/" + HEAD.review_key);
    assert.deepEqual(payload(review.calls[0]), {
      label: "contradicted", evidence_excerpt: "Aster bicycle repairs are not free.",
    });
    const adj = fakeResponse({ id: 10, event_kind: "adjudication" }, 201);
    await adjudicateSyntheticClaim(17, HEAD.review_key, {
      label: "unsupported", rationale: "No cited passage supports the pricing claim.",
      evidence_excerpt: "unused",
    }, adj.fetcher);
    assert.deepEqual(payload(adj.calls[0]), {
      label: "unsupported", rationale: "No cited passage supports the pricing claim.",
    });
    assert.match(adj.calls[0].url, /adjudications/);
  });

  it("rejects forged route segments and arbitrary review labels", async () => {
    const stub = fakeResponse({});
    await assert.rejects(() => submitOwnClaimReview(17, "../other", {
      label: "supported", evidence_excerpt: "valid passage",
    }, stub.fetcher), /valid fictional claim/);
    await assert.rejects(() => submitOwnClaimReview(17, HEAD.review_key, {
      label: "approved" as "supported",
    }, stub.fetcher), /Unknown review label/);
    await assert.rejects(() => adjudicateSyntheticClaim(17, HEAD.review_key, {
      label: "supported", evidence_excerpt: "a".repeat(1001),
    }, stub.fetcher), /1,000/);
    assert.equal(stub.calls.length, 0);
  });

  it("never echoes a raw SQL/reviewer/backend denial reason", async () => {
    for (const status of [401, 403, 404, 409, 422, 500]) {
      const stub = fakeResponse({ detail: "secret reviewer quote SQL tenant 99" }, status);
      const err = await createSyntheticBatch(stub.fetcher).then(
        () => null, (e: unknown) => e as C12ReviewApiError,
      );
      assert.ok(err instanceof C12ReviewApiError);
      assert.equal(err.status, status);
      assert.doesNotMatch(err.message, /SQL|secret reviewer|tenant 99/);
    }
  });
});
