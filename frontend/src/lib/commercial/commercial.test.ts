import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  interpretLeadResponse,
  normalizeWebsite,
  toLeadPayload,
  validateLeadForm,
  type LeadFormInput,
} from "./leads.ts";
import {
  WIDGET_KEY_PLACEHOLDER,
  buildInstallSnippet,
  suggestOrigins,
  validateAllowedOrigin,
} from "./install.ts";
import { invitationLink, parseInvitationHash, roleLabel } from "./team.ts";
import {
  firstOpenStep,
  lifecycleInfo,
  progressPercent,
  usageShare,
  type WizardStep,
} from "./onboarding.ts";
import { mergeFrameAncestors, parseFrameAncestors } from "../widget/frame-ancestors.ts";

const validLead: LeadFormInput = {
  companyName: "Acme Auto",
  contactName: "Sam Rivera",
  email: "sam@acme.example",
  website: "acme.example",
  industry: "automotive",
  inquiry: "",
  consent: true,
  companyFax: "",
};

describe("lead form validation", () => {
  it("accepts a complete request", () => {
    assert.deepEqual(validateLeadForm(validLead), { ok: true, errors: {} });
  });

  it("requires consent, a known industry and a plausible email", () => {
    const result = validateLeadForm({
      ...validLead,
      consent: false,
      industry: "casino",
      email: "sam@",
    });
    assert.equal(result.ok, false);
    assert.ok(result.errors.consent);
    assert.ok(result.errors.industry);
    assert.ok(result.errors.email);
  });

  it("rejects blank names and over-long inquiries", () => {
    const result = validateLeadForm({
      ...validLead,
      companyName: "   ",
      contactName: "",
      inquiry: "x".repeat(2001),
    });
    assert.ok(result.errors.companyName && result.errors.contactName && result.errors.inquiry);
  });

  it("normalizes websites to an origin and rejects non-web schemes", () => {
    assert.equal(normalizeWebsite("acme.example/contact"), "https://acme.example");
    assert.equal(normalizeWebsite("http://shop.acme.example"), "http://shop.acme.example");
    assert.equal(normalizeWebsite("ftp://acme.example"), null);
    assert.equal(normalizeWebsite("localhost"), null);
    assert.equal(normalizeWebsite(""), "");
  });

  it("builds the snake_case payload, keeping the honeypot", () => {
    const payload = toLeadPayload({ ...validLead, companyFax: "bot" });
    assert.equal(payload.company_name, "Acme Auto");
    assert.equal(payload.website, "https://acme.example");
    assert.equal(payload.inquiry, null);
    assert.equal(payload.company_fax, "bot");
    assert.equal(payload.consent, true);
  });

  it("maps backend responses to visitor outcomes", async () => {
    const ok = await interpretLeadResponse(
      new Response(JSON.stringify({ status: "received", message: "Thanks" }), { status: 202 }),
    );
    assert.deepEqual(ok, { kind: "received", message: "Thanks" });
    assert.equal((await interpretLeadResponse(new Response("", { status: 503 }))).kind, "closed");
    assert.equal(
      (await interpretLeadResponse(new Response("", { status: 429 }))).kind,
      "rate_limited",
    );
    assert.equal((await interpretLeadResponse(new Response("", { status: 422 }))).kind, "invalid");
    const mapped = await interpretLeadResponse(
      new Response(
        JSON.stringify({ detail: [{ loc: ["body", "email"], msg: "special-use domain" }] }),
        { status: 422 },
      ),
    );
    assert.equal(mapped.kind, "invalid");
    assert.ok(mapped.kind === "invalid" && mapped.fieldErrors.email);
    assert.equal((await interpretLeadResponse(new Response("", { status: 500 }))).kind, "error");
  });
});

describe("installation", () => {
  it("builds the loader snippet with a real key or a placeholder", () => {
    const key = "pk_live_" + "a1".repeat(24);
    assert.equal(
      buildInstallSnippet("https://app.cxops.example/", key),
      `<script src="https://app.cxops.example/embed/loader.js" data-widget-key="${key}" async></script>`,
    );
    assert.ok(buildInstallSnippet("https://x.example", null).includes(WIDGET_KEY_PLACEHOLDER));
    // Never interpolate something that is not a key (no markup injection).
    assert.ok(
      buildInstallSnippet("https://x.example", '"><script>alert(1)</script>').includes(
        WIDGET_KEY_PLACEHOLDER,
      ),
    );
  });

  it("validates exact origins like the backend", () => {
    assert.equal(validateAllowedOrigin("https://www.acme.example"), null);
    assert.equal(validateAllowedOrigin("http://localhost:4173"), null);
    assert.ok(validateAllowedOrigin("https://*.acme.example"));
    assert.ok(validateAllowedOrigin("https://acme.example/"));
    assert.ok(validateAllowedOrigin("https://acme.example/contact"));
    assert.ok(validateAllowedOrigin("http://acme.example"));
    assert.ok(validateAllowedOrigin("acme.example"));
    assert.ok(validateAllowedOrigin("https://user:pw@acme.example"));
  });

  it("suggests apex and www origins", () => {
    assert.deepEqual(suggestOrigins("https://www.acme.example"), [
      "https://acme.example",
      "https://www.acme.example",
    ]);
    assert.deepEqual(suggestOrigins(null), []);
  });
});

describe("invitations", () => {
  it("keeps the token in the fragment and parses it back", () => {
    const token = "inv_" + "Z".repeat(43);
    const link = invitationLink("https://app.example/", token);
    assert.equal(link, `https://app.example/invite#token=${token}`);
    assert.equal(parseInvitationHash(new URL(link).hash), token);
    assert.equal(parseInvitationHash("#token=short"), null);
    assert.equal(parseInvitationHash("#token=pk_live_" + "a".repeat(40)), null);
    assert.equal(parseInvitationHash(""), null);
  });

  it("labels roles", () => {
    assert.equal(roleLabel("agent"), "Support agent");
    assert.equal(roleLabel("custom"), "custom");
  });
});

describe("setup wizard presentation", () => {
  const steps: WizardStep[] = [
    { key: "business", title: "Business", required: true, complete: true },
    { key: "workspace", title: "Team", required: false, complete: true },
    { key: "domain", title: "Domain", required: true, complete: false },
    { key: "knowledge", title: "Knowledge", required: false, complete: false },
    { key: "review", title: "Review", required: true, complete: false },
  ];

  it("opens the first incomplete required step", () => {
    assert.equal(firstOpenStep(steps), "domain");
    const required = steps.map((s) => (s.required && s.key !== "review" ? { ...s, complete: true } : s));
    assert.equal(firstOpenStep(required), "knowledge");
  });

  it("computes progress and clamps usage bars", () => {
    assert.equal(progressPercent(steps), 40);
    assert.equal(usageShare(50, 200), 25);
    assert.equal(usageShare(500, 200), 100);
    assert.equal(usageShare(1, 0), 100);
    assert.equal(usageShare(0, 0), 0);
  });

  it("labels lifecycle states and fails closed for unknown ones", () => {
    assert.equal(lifecycleInfo("active").tone, "ok");
    assert.equal(lifecycleInfo("pending_review").label, "Awaiting approval");
    assert.equal(lifecycleInfo("bogus").tone, "bad");
  });
});

describe("widget frame-ancestors", () => {
  it("accepts only exact https origins", () => {
    const parsed = parseFrameAncestors(
      "https://shop.example, https://www.shop.example https://*.evil.example http://plain.example https://shop.example/ nope https://shop.example",
    );
    assert.deepEqual(parsed.origins, ["https://shop.example", "https://www.shop.example"]);
    assert.deepEqual(parsed.rejected, [
      "https://*.evil.example",
      "http://plain.example",
      "https://shop.example/",
      "nope",
    ]);
    assert.deepEqual(parseFrameAncestors(undefined), { origins: [], rejected: [] });
  });

  it("accepts loopback http only when explicitly allowed (development)", () => {
    assert.deepEqual(parseFrameAncestors("http://localhost:4173").origins, []);
    assert.deepEqual(
      parseFrameAncestors("http://localhost:4173 http://evil.example", { allowLoopbackHttp: true }),
      { origins: ["http://localhost:4173"], rejected: ["http://evil.example"] },
    );
  });

  it("merges without duplicates", () => {
    assert.deepEqual(mergeFrameAncestors(["'self'", "https://a.example"], ["https://a.example", "https://b.example"]), [
      "'self'",
      "https://a.example",
      "https://b.example",
    ]);
  });
});
