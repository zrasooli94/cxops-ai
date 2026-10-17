import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

function source(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

describe("Phase 1Y Server Action security wiring", () => {
  it("checks request origin before ticket identity or network work", () => {
    const action = source("../../app/staging/operations/actions.ts");
    const origin = action.indexOf("stagingServerActionOriginAllowed(");
    const execution = action.indexOf("runSyntheticTicketAction({");
    assert.ok(origin >= 0 && execution > origin);
    assert.match(action, /readIdentity: allowed => stagingIdentity/);
    assert.match(action, /fetcher: fetch/);
  });

  it("checks request origin and clears a rejected post-OTP session", () => {
    const action = source("../../app/staging/agent-signin/actions.ts");
    const origin = action.indexOf("stagingServerActionOriginAllowed(");
    const execution = action.indexOf("verifyAgentSignInOtp(");
    assert.ok(origin >= 0 && execution > origin);
    assert.match(action, /allowed => stagingIdentity\(auth, allowed\)/);
    assert.match(action, /\(\) => auth\.signOut\(\)/);
  });
});
