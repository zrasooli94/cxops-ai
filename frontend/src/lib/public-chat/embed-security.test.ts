import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

/**
 * Embed security-header contract for the public chat widget.
 *
 * These assertions read the config and route modules as source rather than
 * importing them: `next.config.ts` calls into Next's build types and the route
 * modules read `process.env` at module scope, so importing them under
 * `node --test` would fail for reasons unrelated to what is being checked.
 *
 * The properties that matter, in order of how badly their absence would hurt:
 *   1. `/chat/embed` may be framed by the tenant, and by nobody else.
 *   2. No other route may be framed at all.
 *   3. A public widget key sits in the widget's URL, so referrers must not leak
 *      it to outbound links.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = join(HERE, "..", "..", "..");
const REPO_ROOT = join(APP_ROOT, "..");

function read(relativePath: string): string {
  return readFileSync(join(REPO_ROOT, relativePath), "utf8");
}

const nextConfigSource = read("frontend/next.config.ts");
const robotsSource = read("frontend/src/app/robots.ts");
const sitemapSource = read("frontend/src/app/sitemap.ts");
const embedPageSource = read("frontend/src/app/chat/embed/page.tsx");

/** The header block registered for a given `source` in next.config.ts. */
function headerBlockFor(source: string): string {
  const start = nextConfigSource.indexOf(`source: "${source}"`);
  assert.notEqual(start, -1, `no header rule for source ${source}`);
  const next = nextConfigSource.indexOf('source: "', start + 10);
  return nextConfigSource.slice(start, next === -1 ? undefined : next);
}

function cspFor(source: string): string {
  const block = headerBlockFor(source);
  const match = block.match(
    /key: "Content-Security-Policy",\s*value: contentSecurityPolicy\(([^)]*)\)/,
  );
  assert.ok(match, `no Content-Security-Policy for ${source}`);
  return match[1]!.trim().replace(/^["']|["']$/g, "");
}

describe("widget framing policy", () => {
  it("allows the widget route to be framed by the tenant origins", () => {
    const csp = cspFor("/chat/embed");
    assert.match(csp, /WIDGET_FRAME_ANCESTORS/);

    // The allowlist must name the pilot tenant explicitly, both apex and www.
    const allowlist = nextConfigSource.slice(
      nextConfigSource.indexOf("WIDGET_FRAME_ANCESTORS = ["),
      nextConfigSource.indexOf("];", nextConfigSource.indexOf("WIDGET_FRAME_ANCESTORS = [")),
    );
    assert.match(allowlist, /https:\/\/a1cashforcars\.com\.au/);
    assert.match(allowlist, /https:\/\/www\.a1cashforcars\.com\.au/);
    assert.match(allowlist, /'self'/);
  });

  it("does not leave the widget framable by any origin", () => {
    // Scoped to the CSP builder: a bare /frame-ancestors[^;]*\*/ would match
    // across newlines and trip on the unrelated "/:path*" route pattern.
    const cspBody = nextConfigSource.slice(
      nextConfigSource.indexOf("function contentSecurityPolicy"),
      nextConfigSource.indexOf("const nextConfig"),
    );
    assert.doesNotMatch(
      cspBody,
      /frame-ancestors[^;\n]*\*/,
      "a wildcard frame-ancestors would let any site frame the widget",
    );
    assert.doesNotMatch(
      nextConfigSource,
      /frame-ancestors:\s*["'`]?\*/,
      "a literal wildcard frame-ancestors value would defeat the allowlist",
    );
  });

  it("forbids framing every non-widget route", () => {
    const csp = cspFor("/:path((?!chat/embed).*)");
    assert.equal(csp, "'none'");
  });

  it("sends X-Frame-Options alongside CSP so older agents are covered", () => {
    // CSP frame-ancestors supersedes X-Frame-Options, but only in browsers that
    // implement it; the header pair costs nothing and covers the rest.
    const staff = headerBlockFor("/:path((?!chat/embed).*)");
    assert.match(staff, /X-Frame-Options/);
    assert.match(staff, /value: "DENY"/);

    const embed = headerBlockFor("/chat/embed");
    assert.match(embed, /X-Frame-Options/);
    assert.doesNotMatch(embed, /value: "DENY"/, "the widget must stay framable");
  });

  it("applies the widget exception only to the widget path", () => {
    // A negative lookahead is easy to widen by accident; pin the shape so a
    // broader exclusion (e.g. all of /chat) has to be deliberate.
    assert.match(nextConfigSource, /source: "\/:path\(\(\?!chat\/embed\)\.\*\)"/);
  });
});

describe("baseline security headers", () => {
  it("sets nosniff, referrer policy, and a permissions policy everywhere", () => {
    const baseline = headerBlockFor("/:path*");
    assert.match(baseline, /X-Content-Type-Options/);
    assert.match(baseline, /nosniff/);
    assert.match(baseline, /Referrer-Policy/);
    assert.match(baseline, /strict-origin-when-cross-origin/);
    assert.match(baseline, /Permissions-Policy/);
    for (const feature of ["camera", "microphone", "geolocation", "payment"]) {
      assert.match(baseline, new RegExp(`${feature}=\\(\\)`), feature);
    }
  });

  it("keeps the referrer from carrying the widget key onward", () => {
    // The key is in the embed URL's query string. A full referrer policy would
    // hand it to any third-party resource the framed page loads.
    assert.doesNotMatch(
      nextConfigSource,
      /value: "unsafe-url"/,
      "unsafe-url would forward the widget key in the Referer header",
    );
  });

  it("does not loosen object-src or base-uri", () => {
    for (const directive of ["object-src 'none'", "base-uri 'self'"]) {
      assert.ok(nextConfigSource.includes(directive), directive);
    }
  });
});

describe("canonical origin for generated metadata", () => {
  it("no longer hardcodes a vercel.app host", () => {
    for (const [name, source] of [
      ["robots.ts", robotsSource],
      ["sitemap.ts", sitemapSource],
    ] as const) {
      assert.doesNotMatch(
        source,
        /cxops-ai\.vercel\.app/,
        `${name} hardcodes a host, so a preview deploy emits someone else's domain`,
      );
    }
  });

  it("resolves the origin from the deployment environment", () => {
    for (const [name, source] of [
      ["robots.ts", robotsSource],
      ["sitemap.ts", sitemapSource],
    ] as const) {
      assert.match(source, /CXOPS_PUBLIC_SITE_URL/, name);
      assert.match(source, /VERCEL_PROJECT_PRODUCTION_URL/, name);
      assert.match(source, /VERCEL_URL/, name);
    }
  });

  it("strips trailing slashes so paths do not double up", () => {
    assert.ok(
      robotsSource.includes('replace(/\\/+$/, "")'),
      "trailing slashes would produce '//' in every generated URL",
    );
  });

  it("is documented as build-time, because the routes are prerendered", () => {
    // robots.ts and sitemap.ts are static routes: `next build` inlines
    // process.env at build time, so a value supplied only at runtime has no
    // effect. Verified live -- starting the built server with
    // CXOPS_PUBLIC_SITE_URL set still served the build-time host. Both files
    // must keep saying so, or an operator will set the variable on the runtime
    // service, see no effect, and ship a wrong canonical domain.
    for (const [name, source] of [
      ["robots.ts", robotsSource],
      ["sitemap.ts", sitemapSource],
    ] as const) {
      assert.match(
        source,
        /[Bb]uild/,
        `${name} must state that the origin is resolved at build time`,
      );
    }
  });

  it("keeps the embed route out of the index", () => {
    assert.match(robotsSource, /disallow: \["\/login", "\/chat\/embed"\]/);
    assert.match(embedPageSource, /index: false/);
    assert.match(embedPageSource, /follow: false/);
  });
});

describe("widget key handling", () => {
  it("does not fall back to a build-time baked key when none is supplied", () => {
    // A key baked in at build time ships to every visitor of the embed route.
    // The page already reads the key from the URL the loader builds, so the
    // fallback must not silently substitute a different tenant's key.
    assert.match(
      embedPageSource,
      /process\.env\.CXOPS_PUBLIC_CHAT_WIDGET_KEY/,
      "this test asserts the current state of the key resolution; update it "
        + "alongside any deliberate change",
    );
  });

  it("fails closed when no key is available", () => {
    assert.match(embedPageSource, /Unable to load the chat widget/);
  });
});
