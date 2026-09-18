import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  POST_SIGN_IN_DESTINATION,
  postSignInNavigation,
  postSignInSuccess,
  type PostSignInResult,
} from "./sign-in-result.ts";

describe("postSignInSuccess", () => {
  it("1. successful sign-in returns ok=true with the landing destination", () => {
    const result = postSignInSuccess();
    assert.equal(result.ok, true);
    assert.equal(result.destination, "/api/auth/landing");
    assert.equal(result.destination, POST_SIGN_IN_DESTINATION);
  });

  it("2. the success payload exposes no session or identity data", () => {
    const keys = new Set(Object.keys(postSignInSuccess()));
    assert.deepEqual([...keys].sort(), ["destination", "ok"]);
  });

  it("2b. sign-in.ts performs NO Next redirect on success", () => {
    const signInSource = readFileSync(
      fileURLToPath(new URL("./sign-in.ts", import.meta.url)),
      "utf8",
    );
    assert.ok(
      !signInSource.includes("redirect("),
      "success path must not call redirect()",
    );
    assert.ok(!signInSource.includes('"next/navigation"'));
    assert.ok(!signInSource.includes("from 'next/navigation'"));
  });

  it("3. sign-in success result is the only shape that declares a destination", () => {
    const result: PostSignInResult = postSignInSuccess();
    assert.equal(result.ok, true);
    if (result.ok) {
      assert.equal(result.destination, POST_SIGN_IN_DESTINATION);
    }
    assert.equal(postSignInNavigation(result).shouldNavigate, true);
  });
});

describe("postSignInNavigation", () => {
  it("4. navigates exactly once to the landing destination on success", () => {
    const navigation = postSignInNavigation({
      ok: true,
      destination: "/api/auth/landing",
    });
    assert.equal(navigation.shouldNavigate, true);
    if (navigation.shouldNavigate) {
      assert.equal(navigation.destination, "/api/auth/landing");
      assert.equal(navigation.destination, POST_SIGN_IN_DESTINATION);
    }

    const again = postSignInNavigation({ ok: true, destination: "/api/auth/landing" });
    assert.equal(again.shouldNavigate, true);
    if (again.shouldNavigate) {
      assert.equal(again.destination, POST_SIGN_IN_DESTINATION);
    }
  });

  it("5. never navigates to a user-controlled or non-trusted destination", () => {
    const attempts: unknown[] = [
      { ok: true, destination: "https://evil.example" },
      { ok: true, destination: "//evil.example" },
      { ok: true, destination: "/dashboard" },
      { ok: true, destination: "/select-organization" },
      { ok: true, destination: "javascript:alert(1)" },
      { ok: true, destination: "" },
      { ok: true, destination: null },
      { ok: true, destination: undefined },
      { ok: false, error: "Invalid email or password." },
      { ok: false },
      null,
      undefined,
      "ok",
    ];
    for (const attempt of attempts) {
      assert.equal(
        postSignInNavigation(attempt).shouldNavigate,
        false,
        `expected no navigation for ${JSON.stringify(attempt)}`,
      );
    }
  });

  it("6. failure state (invalid credentials) does not navigate", () => {
    const navigation = postSignInNavigation({
      ok: false,
      error: "Invalid email or password.",
    });
    assert.equal(navigation.shouldNavigate, false);
  });

  it("7. LoginForm uses a hard full-document navigation, not Next router", () => {
    const formSource = readFileSync(
      fileURLToPath(new URL("../../app/login/LoginForm.tsx", import.meta.url)),
      "utf8",
    );
    assert.ok(formSource.includes("window.location.replace("), "must call window.location.replace");
    assert.ok(formSource.includes("useEffect"), "navigation must be an effect");
    assert.ok(formSource.includes("navigatedRef"), "must guard against duplicate navigation");
    assert.ok(!formSource.includes("useRouter"), "must not use next/navigation router");
    assert.ok(!formSource.includes("router.push"), "must not push client-side routes");
    assert.ok(!formSource.includes('next/navigation'), "must not import next/navigation");
  });

  it("8. pending/navigating state disables the submit control", () => {
    const formSource = readFileSync(
      fileURLToPath(new URL("../../app/login/LoginForm.tsx", import.meta.url)),
      "utf8",
    );
    assert.ok(formSource.includes("isPending || navigating"), "submit must be disabled while pending or navigating");
    assert.ok(!formSource.includes("disabled={isPending}\n"), "must not disable only on pending");
  });
});

describe("POST_SIGN_IN_DESTINATION", () => {
  it("is a static trusted same-origin path", () => {
    assert.equal(typeof POST_SIGN_IN_DESTINATION, "string");
    assert.ok(POST_SIGN_IN_DESTINATION.startsWith("/"));
    assert.ok(!POST_SIGN_IN_DESTINATION.startsWith("//"));
    assert.ok(!POST_SIGN_IN_DESTINATION.includes("://"));
    assert.ok(!POST_SIGN_IN_DESTINATION.includes("\\"));
  });

  it("sign-in result destination always equals the static constant", () => {
    const result = postSignInSuccess();
    assert.equal(result.destination, POST_SIGN_IN_DESTINATION);
  });
});