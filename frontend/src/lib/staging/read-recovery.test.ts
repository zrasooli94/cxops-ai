import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import {
  stagingReadIssue, stagingRetryState,
  STAGING_RETRY_DELAYS_MS, STAGING_RETRY_WINDOW_MS,
} from "./read-recovery.ts";

describe("Phase 1Y read-only Free-tier API recovery", () => {
  it("treats cold-start gateway errors and timeouts as transient", () => {
    for (const code of [502, 503, 504]) assert.equal(stagingReadIssue(code), "warming");
    for (const name of ["TimeoutError", "AbortError", "TypeError"]) {
      assert.equal(stagingReadIssue(null, { name }), "warming");
    }
  });
  it("never hides identity or authorization denials behind automatic retries", () => {
    for (const code of [401, 403]) assert.equal(stagingReadIssue(code), "denied");
    for (const code of [200, 400, 404, 409, 429, 500]) {
      assert.equal(stagingReadIssue(code), "unavailable");
    }
    assert.equal(stagingReadIssue(null, new Error("unexpected")), "unavailable");
    assert.equal(stagingReadIssue(null), "unavailable");
  });
  it("bounds automatic read-only retries within the current browsing window", () => {
    const now = 2000000;
    assert.equal(STAGING_RETRY_DELAYS_MS.length, 3);
    assert.deepEqual(stagingRetryState(null, now), { attempts: 0, startedAt: now });
    assert.deepEqual(stagingRetryState({ attempts: 2, startedAt: now - 30000 }, now),
      { attempts: 2, startedAt: now - 30000 });
    assert.equal(stagingRetryState({ attempts: 50, startedAt: now - 10 }, now).attempts, 3);
    for (const invalid of [
      { attempts: -1, startedAt: now },
      { attempts: "2", startedAt: now },
      { attempts: 1.5, startedAt: now },
      { attempts: 1, startedAt: now + 1 },
      { attempts: 2, startedAt: now - STAGING_RETRY_WINDOW_MS - 1 },
      {},
    ]) assert.deepEqual(stagingRetryState(invalid, now), { attempts: 0, startedAt: now });
  });
});

describe("Phase 1Y recovery component is actually mounted", () => {
  it("renders the automatic read-retry controller in the authenticated staging home", () => {
    // Regression: original patch imported recovery but never rendered it.
    const home = readFileSync(new URL("../../app/staging/page.tsx", import.meta.url), "utf8");
    const render = "{backendIssue && <StagingReadRecovery issue={backendIssue} />}";
    assert.ok(home.includes(render), "StagingReadRecovery must be mounted, not just imported");
    const welcome = home.indexOf("Welcome, {identity.user.name}");
    const recovered = home.indexOf(render);
    const workspaces = home.indexOf("Test workspaces", recovered);
    assert.ok(welcome >= 0 && recovered > welcome && workspaces > recovered);
    assert.ok(home.includes('const response = await fetch(STAGING_BACKEND_URL + "/me/organizations"'));
    assert.ok(home.includes("backendIssue = stagingReadIssue(response.status)"));
  });
  it("keeps the same bounded recovery available to the Control Center", () => {
    const operations = readFileSync(
      new URL("../../app/staging/operations/page.tsx", import.meta.url), "utf8",
    );
    assert.ok(operations.includes("<StagingReadRecovery issue={issue} />"));
    assert.ok(operations.includes("waitingForBackend && <StagingReadRecovery"));
  });
});
