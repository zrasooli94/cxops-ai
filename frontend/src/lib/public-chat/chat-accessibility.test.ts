import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Source-level assertions for the React widget; executable pure UI and
 * loader behavior is separately exercised in conversation-view.test.ts
 * and embed-sizing.test.ts. This is NOT browser/assistive-technology QA.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..", "..");
const embed = readFileSync(
  join(ROOT, "src", "app", "chat", "embed", "embed.tsx"),
  "utf8",
);

describe("support dialog keyboard focus", () => {
  it("uses a non-modal dialog: the embedded host is still interactive", () => {
    assert.match(embed, /role="dialog"\s+aria-modal="false"/);
    assert.match(embed, /aria-labelledby="cxops-chat-title"/);
  });

  it("moves focus into the opened dialog and restores the launcher", () => {
    assert.match(embed, /minimizeButtonRef\.current\?\.focus\(\)/);
    assert.match(embed, /launcherButtonRef\.current\?\.focus\(\)/);
    assert.match(embed, /ref=\{minimizeButtonRef\}/);
    assert.match(embed, /buttonRef=\{launcherButtonRef\}/);
    assert.match(embed, /restoreLauncherFocusRef\.current = true/);
    assert.doesNotMatch(
      embed.slice(embed.indexOf("const minimize = useCallback"), embed.indexOf("const endConversation = useCallback")),
      /closePublicChatSession|discardSession\(|setSessionToken\(null\)/,
    );
  });

  it("Escape dismisses confirmations before minimizing, without closing the server session", () => {
    const section = embed.slice(
      embed.indexOf('role="dialog"'),
      embed.indexOf('className="cxops-chat-panel"'),
    );
    assert.match(section, /event\.key !== "Escape"/);
    assert.match(section, /setNewConversationConfirm\(false\)/);
    assert.match(section, /setEndConfirm\(false\)/);
    assert.match(section, /minimize\(\)/);
    assert.doesNotMatch(section, /closePublicChatSession/);
  });
});

describe("customer-first message log", () => {
  it("names the history for screen readers and keeps bidirectional text readable", () => {
    assert.match(embed, /role="log"\s+aria-label="Support conversation"/);
    assert.match(embed, /dir="auto"/);
    assert.match(embed, /aria-label=\{presentation\.sender \+ " message"\}/);
    assert.doesNotMatch(embed, /dangerouslySetInnerHTML/);
  });

  it("does not snap a visitor reading older messages to the bottom", () => {
    assert.match(embed, /const nearBottom = isNearChatBottom\(/);
    assert.match(embed, /followLatestRef\.current = nearBottom/);
    assert.match(embed, /if \(panelOpen && followLatestRef\.current && messageLogRef\.current\)/);
    assert.match(embed, /Jump to latest messages/);
    assert.match(embed, /onClick=\{jumpToLatest\}/);
  });

  it("uses the authored direction mapping rather than reversing conversation semantics", () => {
    assert.match(embed, /messagePresentation\(message\.direction\)/);
    assert.match(embed, /alignSelf: presentation\.alignSelf/);
    assert.match(embed, /isInbound\s*\? theme\.bubble\.inboundBackground/);
    assert.match(embed, /!composerEnabled \|\| sending/);
  });
});
