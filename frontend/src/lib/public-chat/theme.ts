// Closed theme allowlist for the public web-chat embed (Phase 1P.6).
//
// The backend exposes an opaque ``theme_token`` per tenant; this module is the
// only place a token can become styling. ``PUBLIC_CHAT_THEMES`` holds a fixed
// set of authored palettes: ``default``, the live pilot's ``rispu``, and
// (Phase 1Q) three neutral palettes a business may pick during setup
// (``midnight``, ``ocean``, ``forest``). Any other token — missing, null,
// unknown, or mistyped — resolves to ``default``. Tenants can never
// inject CSS, markup, images, or URLs through the token: the widget only ever
// reads values out of the palette below.

export type PublicChatThemeName = "default" | "rispu" | "midnight" | "ocean" | "forest";

export interface PublicChatTheme {
  name: PublicChatThemeName;
  panel: {
    background: string;
    border: string;
    radius: number;
    shadow: string;
    width: number;
  };
  header: {
    background: string;
    foreground: string;
    subtitle: string | null;
    subtitleForeground: string;
  };
  launcher: {
    background: string;
    foreground: string;
    border: string;
    borderWidth: number;
    glyph: "emoji" | "svg";
  };
  bubble: {
    inboundBackground: string;
    inboundForeground: string;
    outboundBackground: string;
    outboundForeground: string;
    outboundBorder: string;
    radius: number;
  };
  composer: {
    background: string;
    border: string;
    focusBorder: string;
    foreground: string;
    placeholder: string;
    sendBackground: string;
    sendForeground: string;
  };
  statusChip: {
    background: string;
    foreground: string;
    border: string;
  };
  text: {
    primary: string;
    secondary: string;
  };
}

export const PUBLIC_CHAT_THEMES: Record<PublicChatThemeName, PublicChatTheme> = {
  default: {
    name: "default",
    panel: {
      background: "#ffffff",
      border: "#e2e8f0",
      radius: 12,
      shadow: "0 8px 24px rgba(0, 0, 0, 0.2)",
      width: 380,
    },
    header: {
      background: "#0f766e",
      foreground: "#ffffff",
      subtitle: null,
      subtitleForeground: "#ccfbf1",
    },
    launcher: {
      background: "#0f766e",
      foreground: "#ffffff",
      border: "#0f766e",
      borderWidth: 0,
      glyph: "emoji",
    },
    bubble: {
      inboundBackground: "#f1f5f9",
      inboundForeground: "#0f172a",
      outboundBackground: "#0f766e",
      outboundForeground: "#ffffff",
      outboundBorder: "#0f766e",
      radius: 12,
    },
    composer: {
      background: "#f8fafc",
      border: "#cbd5e1",
      focusBorder: "#0f766e",
      foreground: "#0f172a",
      placeholder: "#64748b",
      sendBackground: "#0f766e",
      sendForeground: "#ffffff",
    },
    statusChip: {
      background: "#fef3c7",
      foreground: "#78350f",
      border: "#fde68a",
    },
    text: {
      primary: "#0f172a",
      secondary: "#64748b",
    },
  },
  rispu: {
    name: "rispu",
    panel: {
      background: "#17181c",
      border: "#2a2b31",
      radius: 20,
      shadow: "0 12px 32px rgba(0, 0, 0, 0.45)",
      width: 400,
    },
    header: {
      background: "#1c1d21",
      foreground: "#f5f1e6",
      subtitle: "AI assistance · Human review available",
      subtitleForeground: "#a8a29e",
    },
    launcher: {
      background: "#17181c",
      foreground: "#d4af37",
      border: "#d4af37",
      borderWidth: 2,
      glyph: "svg",
    },
    bubble: {
      inboundBackground: "#f5f1e6",
      inboundForeground: "#2c2417",
      outboundBackground: "#26272d",
      outboundForeground: "#f5f1e6",
      outboundBorder: "#d4af37",
      radius: 18,
    },
    composer: {
      background: "#1c1d21",
      border: "#3a3b42",
      focusBorder: "#d4af37",
      foreground: "#f5f1e6",
      placeholder: "#8b8678",
      sendBackground: "#d4af37",
      sendForeground: "#17181c",
    },
    statusChip: {
      background: "#2a2415",
      foreground: "#e6c15a",
      border: "#d4af37",
    },
    text: {
      primary: "#f5f1e6",
      secondary: "#a8a29e",
    },
  },
  midnight: {
    name: "midnight",
    panel: {
      background: "#ffffff",
      border: "#e2e8f0",
      radius: 14,
      shadow: "0 10px 28px rgba(15, 23, 42, 0.18)",
      width: 380,
    },
    header: {
      background: "#1e293b",
      foreground: "#ffffff",
      subtitle: null,
      subtitleForeground: "#cbd5e1",
    },
    launcher: {
      background: "#1e293b",
      foreground: "#ffffff",
      border: "#1e293b",
      borderWidth: 0,
      glyph: "svg",
    },
    bubble: {
      inboundBackground: "#f1f5f9",
      inboundForeground: "#0f172a",
      outboundBackground: "#1e293b",
      outboundForeground: "#ffffff",
      outboundBorder: "#1e293b",
      radius: 14,
    },
    composer: {
      background: "#f8fafc",
      border: "#cbd5e1",
      focusBorder: "#1e293b",
      foreground: "#0f172a",
      placeholder: "#64748b",
      sendBackground: "#1e293b",
      sendForeground: "#ffffff",
    },
    statusChip: {
      background: "#fef3c7",
      foreground: "#78350f",
      border: "#fde68a",
    },
    text: {
      primary: "#0f172a",
      secondary: "#64748b",
    },
  },
  ocean: {
    name: "ocean",
    panel: {
      background: "#ffffff",
      border: "#e2e8f0",
      radius: 14,
      shadow: "0 10px 28px rgba(15, 23, 42, 0.18)",
      width: 380,
    },
    header: {
      background: "#1d4ed8",
      foreground: "#ffffff",
      subtitle: null,
      subtitleForeground: "#dbeafe",
    },
    launcher: {
      background: "#1d4ed8",
      foreground: "#ffffff",
      border: "#1d4ed8",
      borderWidth: 0,
      glyph: "svg",
    },
    bubble: {
      inboundBackground: "#eff6ff",
      inboundForeground: "#172554",
      outboundBackground: "#1d4ed8",
      outboundForeground: "#ffffff",
      outboundBorder: "#1d4ed8",
      radius: 14,
    },
    composer: {
      background: "#f8fafc",
      border: "#cbd5e1",
      focusBorder: "#1d4ed8",
      foreground: "#0f172a",
      placeholder: "#64748b",
      sendBackground: "#1d4ed8",
      sendForeground: "#ffffff",
    },
    statusChip: {
      background: "#fef3c7",
      foreground: "#78350f",
      border: "#fde68a",
    },
    text: {
      primary: "#0f172a",
      secondary: "#64748b",
    },
  },
  forest: {
    name: "forest",
    panel: {
      background: "#ffffff",
      border: "#e2e8f0",
      radius: 14,
      shadow: "0 10px 28px rgba(15, 23, 42, 0.18)",
      width: 380,
    },
    header: {
      background: "#15803d",
      foreground: "#ffffff",
      subtitle: null,
      subtitleForeground: "#dcfce7",
    },
    launcher: {
      background: "#15803d",
      foreground: "#ffffff",
      border: "#15803d",
      borderWidth: 0,
      glyph: "svg",
    },
    bubble: {
      inboundBackground: "#f0fdf4",
      inboundForeground: "#14532d",
      outboundBackground: "#15803d",
      outboundForeground: "#ffffff",
      outboundBorder: "#15803d",
      radius: 14,
    },
    composer: {
      background: "#f8fafc",
      border: "#cbd5e1",
      focusBorder: "#15803d",
      foreground: "#0f172a",
      placeholder: "#64748b",
      sendBackground: "#15803d",
      sendForeground: "#ffffff",
    },
    statusChip: {
      background: "#fef3c7",
      foreground: "#78350f",
      border: "#fde68a",
    },
    text: {
      primary: "#0f172a",
      secondary: "#64748b",
    },
  },
};

const THEME_NAMES: ReadonlySet<string> = new Set(Object.keys(PUBLIC_CHAT_THEMES));

export function resolveTheme(
  token: string | null | undefined,
): PublicChatTheme {
  if (typeof token === "string" && THEME_NAMES.has(token)) {
    return PUBLIC_CHAT_THEMES[token as PublicChatThemeName];
  }
  return PUBLIC_CHAT_THEMES.default;
}