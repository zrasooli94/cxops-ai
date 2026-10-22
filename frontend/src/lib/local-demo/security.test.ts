import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { isAllowedLocalDemoPath, isAuthorizedLocalDemoRequest } from "./security.ts";

const base = { nodeEnv: "development", hostname: "127.0.0.1", method: "GET", origin: null, requestOrigin: "http://127.0.0.1:3000" };

describe("local demo bridge boundary", () => {
  it("accepts loopback development reads", () => assert.equal(isAuthorizedLocalDemoRequest(base), true));
  it("rejects production even on loopback", () => assert.equal(isAuthorizedLocalDemoRequest({ ...base, nodeEnv: "production" }), false));
  it("rejects non-loopback hosts", () => assert.equal(isAuthorizedLocalDemoRequest({ ...base, hostname: "demo.example.com" }), false));
  it("requires same-origin browser mutations", () => {
    assert.equal(isAuthorizedLocalDemoRequest({ ...base, method: "POST" }), false);
    assert.equal(isAuthorizedLocalDemoRequest({ ...base, method: "POST", fetchSite: "same-origin" }), true);
    assert.equal(isAuthorizedLocalDemoRequest({ ...base, method: "POST", localOrigin: base.requestOrigin }), true);
    assert.equal(isAuthorizedLocalDemoRequest({ ...base, method: "POST", localOrigin: "http://127.0.0.1:4000" }), false);
    assert.equal(isAuthorizedLocalDemoRequest({ ...base, method: "POST", origin: "https://evil.example" }), false);
    assert.equal(isAuthorizedLocalDemoRequest({ ...base, method: "POST", origin: "http://127.0.0.1:4000" }), false);
    assert.equal(isAuthorizedLocalDemoRequest({ ...base, method: "POST", origin: base.requestOrigin }), true);
    assert.equal(isAuthorizedLocalDemoRequest({ ...base, method: "POST", origin: "http://localhost:3000" }), true);
  });
  it("allows only the owner-studio route set", () => {
    assert.equal(isAllowedLocalDemoPath("staff/tenant-config/offline-faqs"), true);
    assert.equal(isAllowedLocalDemoPath("staff/tenant-config/offline-evidence"), true);
    assert.equal(isAllowedLocalDemoPath("staff/tenant-config/offline-documents"), true);
    assert.equal(isAllowedLocalDemoPath("staff/tenant-config/offline-documents/42"), true);
    assert.equal(isAllowedLocalDemoPath("staff/tenant-config/offline-documents/not-an-id"), false);
    assert.equal(isAllowedLocalDemoPath("staff/public-chat/sessions/42/assign"), true);
    assert.equal(isAllowedLocalDemoPath("operator/platform"), false);
    assert.equal(isAllowedLocalDemoPath("staff/public-chat/sessions/42/delete"), false);
  });
});
