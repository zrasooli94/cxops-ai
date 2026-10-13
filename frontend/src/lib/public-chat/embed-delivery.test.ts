import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Phase 1P.9 / 1P.9R delivery contract, asserted against source (embed.tsx
 * and the staff page render JSX and cannot be imported under `node --test`):
 *
 *   - sends ask for async delivery, retry ambiguous failures with the SAME
 *     client_message_id, and remember a failed send so a manual resend of
 *     the same text reuses its id;
 *   - polling follows the server's pace, backs off in a hidden tab, and
 *     polls immediately when the tab becomes visible again (reconnection);
 *   - an older backend without /sessions/updates falls back to full state;
 *   - the staff handoff inbox refreshes itself only while visible, quietly.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = join(HERE, "..", "..", "..");
const embed = readFileSync(
  join(FRONTEND_ROOT, "src", "app", "chat", "embed", "embed.tsx"),
  "utf8",
);
const staffPage = readFileSync(
  join(FRONTEND_ROOT, "src", "app", "(control-center)", "public-chat", "page.tsx"),
  "utf8",
);

describe("widget send (embed.tsx)", () => {
  it("retries through sendWithRetry with one stable id", () => {
    assert.match(embed, /const messageId = clientMessageIdForSend\(/);
    assert.match(embed, /sendWithRetry\(\(\) =>\s*sendPublicChatMessage\(\s*token,\s*embeddingOriginRef\.current,\s*messageId,/);
    assert.ok(!/sendPublicChatMessage\(\s*token,\s*embeddingOriginRef\.current,\s*clientMessageId\(\)/.test(embed));
  });

  it("clears the remembered send only after success", () => {
    const success = embed.indexOf("pendingSendRef.current = null;");
    const call = embed.indexOf("sendWithRetry(");
    assert.ok(call > 0 && success > call);
  });

  it("asks for async delivery", () => {
    assert.match(embed, /messageId,\s*trimmed,\s*fetch,\s*"async",/);
  });
});

describe("widget polling (embed.tsx)", () => {
  it("follows the server's poll hint and backs off when hidden", () => {
    assert.match(embed, /nextPollDelay\(delay, document\.hidden\)/);
  });

  it("polls immediately when the tab becomes visible again", () => {
    assert.match(embed, /addEventListener\("visibilitychange", onVisibility\)/);
    assert.match(embed, /if \(!document\.hidden\) \{\s*schedule\(0\);/);
    assert.match(embed, /removeEventListener\("visibilitychange", onVisibility\)/);
  });

  it("falls back to full-state polling on an older backend", () => {
    assert.match(embed, /updatesSupportedRef\.current = false;/);
    assert.match(embed, /await fetchPublicChatState\(token\)/);
  });
});

describe("staff inbox auto-refresh (public-chat/page.tsx)", () => {
  it("refreshes quietly, only while visible, and cleans up", () => {
    assert.match(staffPage, /const INBOX_REFRESH_MS = 15_000;/);
    assert.match(staffPage, /if \(!document\.hidden\) void loadSessions\(true\);/);
    assert.match(staffPage, /clearInterval\(timer\);/);
    assert.match(staffPage, /removeEventListener\("visibilitychange", onVisibility\)/);
  });
});

describe("widget first load (layout + smooth-scroll gate)", () => {
  const layout = readFileSync(join(FRONTEND_ROOT, "src", "app", "layout.tsx"), "utf8");
  const gate = readFileSync(
    join(FRONTEND_ROOT, "src", "components", "smooth-scroll-gate.tsx"),
    "utf8",
  );

  it("never imports gsap/lenis statically from the root layout", () => {
    assert.ok(!layout.includes('from "@/components/smooth-scroll"'));
    assert.match(layout, /<SmoothScrollGate \/>/);
  });

  it("loads smooth scrolling lazily and never on the widget route", () => {
    assert.match(gate, /dynamic\(\(\) => import\("\.\/smooth-scroll"\), \{ ssr: false \}\)/);
    assert.match(gate, /pathname\.startsWith\("\/chat\/embed"\)/);
    assert.match(gate, /if \(isWidgetRoute\(pathname\)\) \{\s*return null;/);
  });
});
