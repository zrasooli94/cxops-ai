/* C9 true Chrome/Next SSR contract test, fictional auth only.
 *
 * No customer accounts, real provider keys, DB connections, network beyond
 * 127.0.0.1, or production side effects. Do not call this real-account UAT.
 *
 * CI runs the REAL Next production build unmodified against a fake in-memory
 * authorization service on 127.0.0.1:18767. Browser on 127.0.0.1:18768.
 */
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { chromium } from "playwright-core";

const BASE = "http://127.0.0.1:18768";
const BACKEND = "http://127.0.0.1:18767";
const TARGET = BASE + "/evaluations/claim-review";
const BLANK = process.env.C9_BLANK_SHEET;
const OUTPUT = process.env.RUNNER_TEMP;
const CHROME = process.env.CHROME_BINARY;
const C9_LABEL = "c9-source-only-browser-testing";

function fakeToken(subject) {
  const payload = Buffer.from(JSON.stringify({
    sub: subject,
    exp: Math.floor(Date.now() / 1000) + 3600,
    source: C9_LABEL,
  })).toString("base64url");
  return "c9." + payload + ".local-only-test";
}

function cookieSession(subject) {
  return JSON.stringify({
    accessToken: fakeToken(subject),
    refreshToken: "offline-c9-test-only",
    user: {
      id: "synthetic-" + subject,
      displayName: "C9 Synthetic Staff",
      email: "nobody@fixture.invalid",
    },
  });
}

async function makeContext(browser, subject, orgId, viewport = {
  width: 1280, height: 900,
}) {
  const context = await browser.newContext({
    viewport, acceptDownloads: true, serviceWorkers: "block",
  });
  // Never allow the browser to connect to a public host. Next's server
  // itself only calls the loopback fake backend configured in CI.
  await context.route("**/*", async (route) => {
    const uri = new URL(route.request().url());
    if (uri.hostname !== "127.0.0.1" ||
        (uri.port !== "18768" && uri.port !== "18767")) {
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  if (subject) {
    await context.addCookies([
      {
        name: "nhostSession", value: cookieSession(subject),
        url: BASE, sameSite: "Lax",
      },
      {
        name: "cxopsOrganization", value: String(orgId),
        url: BASE, sameSite: "Lax",
      },
    ]);
  }
  return context;
}

async function rendered(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await page.goto(TARGET, {
    waitUntil: "domcontentloaded", timeout: 45_000,
  });
  assert.ok(response, "Expected a real Next response");
  return { errors, response };
}

async function assertDenied(page) {
  await page.getByRole("heading", {
    name: /don't have permission to view this page/i,
  }).waitFor({ state: "visible", timeout: 20_000 });
  assert.equal(await page.getByText(
    "Judge the claim. Read the source. Keep the evidence.",
    { exact: true },
  ).count(), 0);
  assert.equal(await page.locator('input[type="file"]').count(), 0);
}

async function assertAllowed(page) {
  await page.getByRole("heading", {
    name: /Judge the claim. Read the source. Keep the evidence/i,
  }).waitFor({ state: "visible", timeout: 25_000 });
  assert.equal(await page.locator('input[type="file"]').count(), 1);
  assert.equal(await page.getByRole("button", { name: /Download draft/i }).count(), 0);
}

async function loadFirstClaim(page) {
  await page.getByRole("textbox", {
    name: /Reviewer identifier/i,
  }).fill("fixture-reviewer-A");
  await page.locator('input[type="file"]').setInputFiles(BLANK);
  await page.getByRole("heading", {
    name: /All Aster bicycle repairs are free/i,
  }).waitFor({ state: "visible", timeout: 20_000 });
  assert.equal(await page.getByText(/Loaded 12 fictional claims/i).count(), 1);
}

test("C9 real headless Chrome + real Next auth boundary, synthetic only", async (t) => {
  assert.equal(process.env.CI, "true");
  assert.equal(process.env.GITHUB_ACTIONS, "true");
  assert.equal(process.env.ENVIRONMENT, "development");
  assert.ok(BLANK && OUTPUT && CHROME, "CI must supply paths to purely fictional inputs");
  assert.match(BLANK, /c9-blank\.csv$/);
  assert.ok(readFileSync(BLANK, "utf8").includes("c6-fictional-faq-20261021"));

  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
    args: [
      "--no-sandbox", "--disable-dev-shm-usage",
      "--disable-extensions", "--disable-background-networking",
    ],
  });
  try {
    await t.test("no session: real protected route redirects before reviewer UI", async () => {
      const context = await makeContext(browser, null, null);
      try {
        const response = await context.request.get(TARGET, {
          maxRedirects: 0,
        });
        assert.ok([302, 303, 307, 308].includes(response.status()));
        assert.match(response.headers().location ?? "", /\/login/);
      } finally {
        await context.close();
      }
    });

    await t.test("read-only staff cannot see reviewer even by direct URL", async () => {
      const context = await makeContext(browser, "read-only", 9001);
      try {
        const page = await context.newPage();
        await rendered(page);
        await assertDenied(page);
      } finally {
        await context.close();
      }
    });

    await t.test("manage-only without read also fails closed", async () => {
      const context = await makeContext(browser, "manage-only", 9001);
      try {
        const page = await context.newPage();
        await rendered(page);
        await assertDenied(page);
      } finally {
        await context.close();
      }
    });

    await t.test("server's second authorization read blocks mid-render downgrade", async () => {
      const context = await makeContext(browser, "revoked", 9001);
      try {
        const page = await context.newPage();
        await rendered(page);
        await assertDenied(page);
      } finally {
        await context.close();
      }
    });

    await t.test("manager in A cannot see B's reviewer with B read-only role", async () => {
      const context = await makeContext(browser, "manager-a", 9002);
      try {
        const page = await context.newPage();
        await rendered(page);
        await assertDenied(page);
      } finally {
        await context.close();
      }
    });

    await t.test("wrong tenant membership refuses before review rendering", async () => {
      const context = await makeContext(browser, "no-membership", 9001);
      try {
        const response = await context.request.get(TARGET, { maxRedirects: 0 });
        assert.ok([302, 303, 307, 308].includes(response.status()));
        assert.match(response.headers().location ?? "", /\/api\/auth\/landing/);
      } finally {
        await context.close();
      }
    });

    await t.test("authorized reviewer: genuine DOM, CSV import, label, quote and download", async () => {
      const context = await makeContext(browser, "manager-a", 9001);
      try {
        const page = await context.newPage();
        await rendered(page);
        await assertAllowed(page);
        await loadFirstClaim(page);

        await page.getByRole("combobox", { name: "Claim review label" })
          .selectOption("contradicted");
        await page.locator("textarea").first()
          .fill("Aster bicycle repairs are not free.");
        await page.getByText("Judgment fields are filled; not a factual certificate.")
          .waitFor({ state: "visible", timeout: 10_000 });

        await page.screenshot({
          path: join(OUTPUT, "c9-desktop-fixture.png"),
          fullPage: true,
        });

        const saved = page.waitForEvent("download");
        await page.getByRole("button", { name: /Download draft CSV/i }).click();
        const download = await saved;
        assert.match(download.suggestedFilename(), /reviewer-A-draft\.csv$/);
        const csvPath = join(OUTPUT, "c9-reviewer-fixture-draft.csv");
        await download.saveAs(csvPath);
        const contents = readFileSync(csvPath, "utf8");
        assert.match(contents, /fixture-reviewer-A/);
        assert.match(contents, /Aster bicycle repairs are not free/);
        assert.ok(!contents.includes("production_certified"));
        // A human judgement is a text label, not a production release.
        const buffer = contents.replace(/^\ufeff/, "");
        assert.match(buffer, /"contradicted"/);
        assert.doesNotMatch(buffer, /"fixture-reviewer-B"/);
      } finally {
        await context.close();
      }
    });

    await t.test("same browser switches A -> B and cannot display stale A review", async () => {
      const context = await makeContext(browser, "manager-both", 9001, {
        width: 390, height: 844,
      });
      try {
        const page = await context.newPage();
        await rendered(page);
        await assertAllowed(page);
        await loadFirstClaim(page);

        // A real browser context tenant-cookie switch triggers a new actual
        // Next SSR auth check; no client-side role override or debug flag.
        await context.addCookies([{
          name: "cxopsOrganization", value: "9002",
          url: BASE, sameSite: "Lax",
        }]);
        await page.reload({ waitUntil: "domcontentloaded", timeout: 45_000 });
        await assertAllowed(page);
        await page.getByRole("heading", {
          name: "One reviewer, one private CSV",
        }).waitFor({ state: "visible" });
        assert.equal(await page.getByText(/Loaded 12 fictional claims/i).count(), 0);
        assert.equal(await page.getByText(
          "All Aster bicycle repairs are free.", { exact: true },
        ).count(), 0);
        await page.screenshot({
          path: join(OUTPUT, "c9-mobile-org-b-fixture.png"),
          fullPage: true,
        });
      } finally {
        await context.close();
      }
    });

    await t.test("fixture backend only saw scoped authorization GET requests", async () => {
      const response = await fetch(BACKEND + "/__c9__/audit");
      assert.equal(response.status, 200);
      const data = await response.json();
      assert.equal(data.not_production, true);
      assert.ok(data.requests.length > 12);
      for (const request of data.requests) {
        assert.equal(request.method, "GET");
        assert.ok(
          request.path === "/me/organizations" ||
          request.path === "/me/authorization",
        );
        if (request.path === "/me/authorization") {
          assert.ok(["9001", "9002"].includes(request.selected_organization));
        }
      }
      const groups = new Set(
        data.requests.filter((x) => x.path === "/me/authorization")
          .map((x) => x.subject + ":" + x.selected_organization),
      );
      assert.ok(groups.has("manager-a:9001"));
      assert.ok(groups.has("manager-both:9002"));
      assert.ok(groups.has("read-only:9001"));
      assert.ok(groups.has("manage-only:9001"));
    });
  } finally {
    await browser.close();
  }
});
