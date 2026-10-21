/* C12 real Chromium + Next -> BFF -> actual FastAPI -> disposable Postgres.
 * Fictional staff JWTs and claims only. Loopback browser requests only.
 */
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { chromium } from "playwright-core";

const WEB = "http://127.0.0.1:18768";
const API = WEB + "/api/backend/evaluations/claim-reviews";
const CSV = process.env.C12_BLANK_SHEET;
const ORGS = JSON.parse(readFileSync(process.env.C12_ORGS_JSON_PATH, "utf8"));
const CHROME = process.env.CHROME_BINARY;
const OUT = process.env.RUNNER_TEMP;
const SECRET = process.env.AUTH_JWT_SECRET;
const CLAIM = "aster-direct-contradiction:c1";
const QUOTE = "Aster bicycle repairs are not free.";

if (
  process.env.CI !== "true" || process.env.GITHUB_ACTIONS !== "true"
  || process.env.ENVIRONMENT !== "development"
  || process.env.CLAIM_REVIEW_SYNTHETIC_API_ENABLED !== "true"
  || process.env.CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED !== "true"
  || SECRET !== "c".repeat(32) || !CSV || !CHROME || !OUT || !ORGS.synthetic
) throw Error("C12 browser requires exact disposable fictional CI");

function token(sub) {
  const now = Math.floor(Date.now() / 1000);
  const head = Buffer.from(JSON.stringify({
    alg: "HS256", typ: "JWT",
  })).toString("base64url");
  const body = Buffer.from(JSON.stringify({
    sub, email: sub + "@fixture.invalid",
    iss: "c12-synthetic-test-issuer",
    aud: "c12-synthetic-test-audience",
    iat: now, exp: now + 1800,
  })).toString("base64url");
  const input = head + "." + body;
  return input + "." + createHmac("sha256", SECRET).update(input).digest("base64url");
}

async function contextFor(browser, sub, org, viewport = { width: 1280, height: 900 }) {
  const ctx = await browser.newContext({
    viewport, acceptDownloads: true, serviceWorkers: "block",
  });
  await ctx.route("**/*", async (route) => {
    const u = new URL(route.request().url());
    if (u.hostname !== "127.0.0.1" || !["18767", "18768"].includes(u.port)) {
      await route.abort("blockedbyclient");
    } else {
      await route.continue();
    }
  });
  if (sub) {
    const session = JSON.stringify({
      accessToken: token(sub),
      refreshToken: "c12-disposable-fixture-only",
      user: {
        id: sub, displayName: "C12 Fictional Staff",
        email: sub + "@fixture.invalid",
      },
    });
    await ctx.addCookies([
      { name: "nhostSession", value: session, url: WEB, sameSite: "Lax" },
      { name: "cxopsOrganization", value: String(org), url: WEB, sameSite: "Lax" },
    ]);
  }
  return ctx;
}

async function visible(page) {
  await page.goto(WEB + "/evaluations/claim-review", {
    waitUntil: "domcontentloaded", timeout: 45_000,
  });
  await page.getByRole("heading", {
    name: "Connect your local review to the C11 audit",
  }).waitFor({ state: "visible", timeout: 25_000 });
}

async function importClaim(page, slot, id) {
  if (slot === 2) await page.getByLabel("Reviewer assignment").selectOption("2");
  await page.getByPlaceholder("Your private reviewer ID").fill(id);
  await page.locator('input[type="file"]').setInputFiles(CSV);
  await page.getByRole("heading", {
    name: "All Aster bicycle repairs are free.",
  }).waitFor({ state: "visible", timeout: 20_000 });
}

async function fillDecision(page, label, content) {
  await page.getByRole("combobox", { name: "Claim review label" })
    .selectOption(label);
  await page.getByPlaceholder(
    label === "contradicted" ? "Quote at least 8 exact characters from the cited passage."
      : "Explain missing or ambiguous evidence.",
  ).fill(content);
  await page.getByText(
    "Judgment fields are filled; not a factual certificate.",
  ).waitFor({ state: "visible", timeout: 12_000 });
}
async function openQueue(page, batch) {
  await page.getByLabel("Synthetic claim batch ID").fill(String(batch));
  await page.getByRole("button", { name: /Open \/ refresh my queue/ }).click();
  await page.getByText(/my judgments recorded for batch/).waitFor({
    state: "visible", timeout: 20_000,
  });
}
async function marker(page, n, batch) {
  await page.getByText(
    String(n) + " / 12 my judgments recorded for batch " + batch + ".",
  ).waitFor({ state: "visible", timeout: 12_000 });
}

test("real C12 browser-to-backend synthetic reviewer flow", async (t) => {
  assert.ok(readFileSync(CSV, "utf8").includes("c6-fictional-faq-20261021"));
  const browser = await chromium.launch({
    executablePath: CHROME, headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage",
      "--disable-extensions", "--disable-background-networking"],
  });
  let batchId;
  try {
    await t.test("unauthed and viewer never gain reviewer API access", async () => {
      const missing = await contextFor(browser, null, null);
      try {
        const res = await missing.request.post(API + "/synthetic-batches");
        assert.equal(res.status(), 401);
      } finally { await missing.close(); }
      const viewer = await contextFor(browser, "c12-viewer", ORGS.org_a);
      try {
        const page = await viewer.newPage();
        await page.goto(WEB + "/evaluations/claim-review", {
          waitUntil: "domcontentloaded",
        });
        assert.equal(await page.getByRole("heading", {
          name: "Connect your local review to the C11 audit",
        }).count(), 0);
        const denied = await viewer.request.post(API + "/synthetic-batches");
        assert.equal(denied.status(), 403);
      } finally { await viewer.close(); }
    });

    await t.test("owner uses real Next UI to create batch + assign real DB reviewers", async () => {
      const ctx = await contextFor(browser, "c12-owner", ORGS.org_a);
      try {
        const page = await ctx.newPage();
        await visible(page);
        await page.getByRole("button", { name: /Create fictional batch/ }).click();
        await page.getByText("Fictional review batch created.", {
          exact: false,
        }).waitFor({ state: "visible", timeout: 20_000 });
        batchId = Number(await page.getByLabel("Synthetic claim batch ID").inputValue());
        assert.ok(Number.isSafeInteger(batchId) && batchId > 0);
        await page.getByLabel("Reviewer A subject ID").fill("c12-r-a");
        await page.getByLabel("Reviewer B subject ID").fill("c12-r-b");
        await page.getByRole("button", { name: /Assign two reviewers/ }).click();
        await page.getByText("Two immutable reviewer slots assigned", {
          exact: false,
        }).waitFor({ state: "visible", timeout: 20_000 });
        const repeated = await ctx.request.post(
          API + "/batches/" + batchId + "/assignments",
          { data: { reviewer_one_subject: "c12-r-a",
            reviewer_two_subject: "c12-r-b" } },
        );
        assert.equal(repeated.status(), 409);
        await page.screenshot({
          path: join(OUT, "c12-owner-fixture.png"), fullPage: true,
        });
      } finally { await ctx.close(); }
    });

    await t.test("A sees exact hash + submits one immutable claim", async () => {
      const ctx = await contextFor(browser, "c12-r-a", ORGS.org_a);
      try {
        const page = await ctx.newPage();
        await visible(page);
        await openQueue(page, batchId);
        await marker(page, 0, batchId);
        await importClaim(page, 1, "c12-reviewer-A-local");
        await fillDecision(page, "contradicted", QUOTE);
        await page.getByText(
          "The backend source fingerprint matches your locally imported C6 claim.",
        ).waitFor({ state: "visible", timeout: 12_000 });
        await page.getByRole("button", {
          name: "Submit my judgment",
        }).click();
        await page.getByText("Your single immutable judgment was recorded", {
          exact: false,
        }).waitFor({ state: "visible", timeout: 20_000 });
        await page.getByRole("button", {
          name: /Open \/ refresh my queue/,
        }).click();
        await marker(page, 1, batchId);
        const forged = await ctx.request.post(
          API + "/batches/" + batchId + "/reviews/" + CLAIM,
          { data: {
            label: "contradicted", evidence_excerpt: QUOTE,
            actor_subject: "c12-owner",
          } },
        );
        assert.equal(forged.status(), 422);
      } finally { await ctx.close(); }
    });

    await t.test("B cannot see A's completion and submits a differing label", async () => {
      const ctx = await contextFor(browser, "c12-r-b", ORGS.org_a);
      try {
        const page = await ctx.newPage();
        await visible(page);
        await openQueue(page, batchId);
        await marker(page, 0, batchId);
        assert.equal(await page.getByRole("button", {
          name: /Create fictional batch/,
        }).count(), 0);
        await importClaim(page, 2, "c12-reviewer-B-local");
        await fillDecision(
          page, "unsupported",
          "No cited passage confirms that repairs are free.",
        );
        await page.getByRole("button", {
          name: "Submit my judgment",
        }).click();
        await page.getByText(
          "Your single immutable judgment was recorded", { exact: false },
        ).waitFor({ state: "visible", timeout: 20_000 });
      } finally { await ctx.close(); }
    });

    await t.test("third authorized member adjudicates actual disputed database events", async () => {
      const ctx = await contextFor(browser, "c12-third", ORGS.org_a);
      try {
        const page = await ctx.newPage();
        await visible(page);
        await page.getByLabel("Synthetic claim batch ID").fill(String(batchId));
        await importClaim(page, 1, "c12-adjudicator-local");
        await fillDecision(page, "contradicted", QUOTE);
        await page.getByRole("button", {
          name: "Adjudicate disputed claim",
        }).click();
        await page.getByText("Third-person adjudication recorded", {
          exact: false,
        }).waitFor({ state: "visible", timeout: 20_000 });
        await page.screenshot({
          path: join(OUT, "c12-adjudicator-fixture.png"), fullPage: true,
        });
      } finally { await ctx.close(); }
    });

    await t.test("fully authorized Org B cannot read Org A's audit batch", async () => {
      const ctx = await contextFor(browser, "c12-both", ORGS.org_b);
      try {
        const res = await ctx.request.get(
          API + "/batches/" + batchId + "/my-queue",
        );
        assert.equal(res.status(), 404);
      } finally { await ctx.close(); }
    });

    await t.test("tenant cookie switch resets local claim/batch in actual browser", async () => {
      const ctx = await contextFor(browser, "c12-both", ORGS.org_a, {
        width: 390, height: 844,
      });
      try {
        const page = await ctx.newPage();
        await visible(page);
        await page.getByLabel("Synthetic claim batch ID").fill(String(batchId));
        await importClaim(page, 1, "c12-local-review");
        await ctx.addCookies([{
          name: "cxopsOrganization", value: String(ORGS.org_b),
          url: WEB, sameSite: "Lax",
        }]);
        await page.reload({ waitUntil: "domcontentloaded", timeout: 45_000 });
        await page.getByRole("heading", {
          name: "Connect your local review to the C11 audit",
        }).waitFor({ state: "visible", timeout: 25_000 });
        assert.equal(await page.getByRole("heading", {
          name: "All Aster bicycle repairs are free.",
        }).count(), 0);
        assert.equal(await page.getByLabel("Synthetic claim batch ID").inputValue(), "");
        assert.equal(await page.getByRole("button", {
          name: "Submit my judgment",
        }).isDisabled(), true);
        await page.screenshot({
          path: join(OUT, "c12-mobile-org-b-fixture.png"), fullPage: true,
        });
      } finally { await ctx.close(); }
    });
  } finally {
    await browser.close();
  }
});
