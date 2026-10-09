import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  PUBLIC_CHAT_THEMES,
  resolveTheme,
  type PublicChatTheme,
} from "./theme.ts";

/**
 * Closed theme allowlist (Phase 1P.6).
 *
 * The tenant supplies an opaque `theme_token`; `resolveTheme` must be a pure,
 * closed mapping with exactly two authored palettes and a hard fallback to
 * `default` for everything else. Nothing tenant-controlled may become styling.
 */

describe("closed allowlist", () => {
  it("contains exactly the two authored themes", () => {
    assert.deepEqual(
      Object.keys(PUBLIC_CHAT_THEMES).sort(),
      ["default", "rispu"],
    );
  });

  it("resolves the two known tokens", () => {
    assert.equal(resolveTheme("default").name, "default");
    assert.equal(resolveTheme("rispu").name, "rispu");
  });

  it("falls back to default for every unknown token", () => {
    for (const token of [
      null,
      undefined,
      "",
      "unknown",
      "a1-dark",
      "brand-teal",
      "RISPU",
      "<img src=x onerror=alert(1)>",
    ] as const) {
      const resolved = resolveTheme(token);
      assert.equal(resolved.name, "default", `token ${String(token)}`);
    }
  });
});

describe("RISP U palette (live pilot)", () => {
  it("is a premium dark charcoal/gold theme", () => {
    const theme = resolveTheme("rispu");
    assert.equal(theme.panel.background, "#17181c");
    assert.equal(theme.header.background, "#1c1d21");
    assert.equal(theme.launcher.background, "#17181c");
    assert.equal(theme.launcher.border, "#d4af37");
    assert.equal(theme.launcher.glyph, "svg");
    assert.equal(theme.bubble.outboundBorder, "#d4af37");
    assert.equal(theme.composer.sendBackground, "#d4af37");
  });

  it("uses a generous panel and bubble radius", () => {
    const theme = resolveTheme("rispu");
    assert.equal(theme.panel.radius, 20);
    assert.equal(theme.bubble.radius, 18);
  });

  it("keeps its theme width RISPU-specific (400, not the default 380)", () => {
    const theme = resolveTheme("rispu");
    assert.equal(theme.panel.width, 400);
  });

  it("keeps the customer and agent bubbles distinct and plain-text safe", () => {
    const theme = resolveTheme("rispu");
    assert.notEqual(
      theme.bubble.inboundBackground,
      theme.bubble.outboundBackground,
    );
    // Values must be static palette strings, never functions or URLs.
    assert.equal(typeof theme.bubble.inboundBackground, "string");
  });
});

describe("default theme stays the classic white/teal", () => {
  it("preserves the original identity", () => {
    const theme = resolveTheme("default");
    assert.equal(theme.panel.background, "#ffffff");
    assert.equal(theme.header.background, "#0f766e");
    assert.equal(theme.launcher.glyph, "emoji");
    assert.equal(theme.header.subtitle, null);
  });

  it("keeps the original 380px logical panel width", () => {
    const theme = resolveTheme("default");
    assert.equal(theme.panel.width, 380);
  });
});

describe("palette completeness", () => {
  it("supplies every visual slot both themes need", () => {
    for (const theme of Object.values(PUBLIC_CHAT_THEMES) as PublicChatTheme[]) {
      assert.ok(theme.panel.border);
      assert.ok(theme.panel.shadow);
      assert.equal(typeof theme.panel.width, "number");
      assert.ok(theme.panel.width > 0);
      assert.ok(theme.header.foreground);
      assert.ok(theme.launcher.foreground);
      assert.ok(theme.bubble.outboundForeground);
      assert.ok(theme.composer.placeholder);
      assert.ok(theme.composer.focusBorder);
      assert.ok(theme.statusChip.foreground);
      assert.ok(theme.text.primary);
      assert.ok(theme.text.secondary);
    }
  });
});