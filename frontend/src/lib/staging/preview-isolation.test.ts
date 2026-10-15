import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { previewGate } from "./preview-isolation.ts";

const SAFE_STAGING = Object.freeze({
  VERCEL_ENV: "preview",
  CXOPS_PREVIEW_AUTH_ENABLED: "true",
  CXOPS_STAGING_ISOLATION_VERIFIED: "true",
  BACKEND_API_URL: "https://cxops-ai-staging.onrender.com",
  CXOPS_STAGING_BACKEND_URL: "https://cxops-ai-staging.onrender.com",
  NEXT_PUBLIC_NHOST_SUBDOMAIN: "isolated-test-tenant",
  CXOPS_STAGING_NHOST_SUBDOMAIN: "isolated-test-tenant",
  NEXT_PUBLIC_NHOST_REGION: "ap-southeast-1",
  CXOPS_STAGING_NHOST_REGION: "ap-southeast-1",
});

describe("preview staff isolation gate", () => {
  it("does not change production or ordinary local behavior", () => {
    assert.equal(previewGate({ ...SAFE_STAGING, VERCEL_ENV: "production" }), "not-preview");
    assert.equal(previewGate({ ...SAFE_STAGING, VERCEL_ENV: undefined }), "not-preview");
  });

  it("is closed for current preview configuration, even with a shared backend", () => {
    assert.equal(previewGate({
      VERCEL_ENV: "preview",
      BACKEND_API_URL: "https://cxops-ai.onrender.com",
    }), "blocked");
  });

  it("requires each explicit, separately verified staging prerequisite", () => {
    assert.equal(previewGate(SAFE_STAGING), "ready");
    const fields = [
      "CXOPS_PREVIEW_AUTH_ENABLED",
      "CXOPS_STAGING_ISOLATION_VERIFIED",
      "BACKEND_API_URL",
      "CXOPS_STAGING_BACKEND_URL",
      "NEXT_PUBLIC_NHOST_SUBDOMAIN",
      "NEXT_PUBLIC_NHOST_REGION",
      "CXOPS_STAGING_NHOST_SUBDOMAIN",
      "CXOPS_STAGING_NHOST_REGION",
    ] as const;
    for (const field of fields) {
      assert.equal(previewGate({ ...SAFE_STAGING, [field]: undefined }), "blocked", field);
    }
  });

  it("rejects production, malformed, local and mismatched API origins", () => {
    for (const url of [
      "https://cxops-ai.onrender.com",
      "https://cxops-ai.onrender.com/",
      "https://cxops-ai.onrender.com.",
      "https://127.0.0.2",
      "https://[::1]",
      "http://cxops-ai-staging.onrender.com",
      "http://127.0.0.1:8000",
      "https://localhost",
      "https://192.168.1.15",
      "https://internal.local",
      "https://cxops-ai-staging.onrender.com/subpath",
      "https://user:password@cxops-ai-staging.onrender.com",
      "https://cxops-ai-staging.onrender.com?redirect=https://evil.test",
      "not-a-url",
    ]) {
      assert.equal(previewGate({ ...SAFE_STAGING, BACKEND_API_URL: url }), "blocked", url);
    }
    assert.equal(previewGate({
      ...SAFE_STAGING,
      BACKEND_API_URL: "https://different-staging.onrender.com",
    }), "blocked");
  });

  it("rejects a production-copy identity or incomplete staging identity mapping", () => {
    assert.equal(previewGate({
      ...SAFE_STAGING,
      NEXT_PUBLIC_NHOST_SUBDOMAIN: "copied-prod-auth",
    }), "blocked");
    assert.equal(previewGate({
      ...SAFE_STAGING,
      NEXT_PUBLIC_NHOST_REGION: "eu-central-1",
    }), "blocked");
  });

  it("accepts a correctly attested HTTPS staging origin with harmless trailing slash", () => {
    assert.equal(previewGate({
      ...SAFE_STAGING,
      BACKEND_API_URL: SAFE_STAGING.BACKEND_API_URL + "/",
    }), "ready");
  });
});
