"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import {
  CHAT_SESSION_STORAGE_KEY,
  createEmbeddingOrigin,
  createPublicChatSession,
  closePublicChatSession,
  fetchPublicChatConfig,
  fetchPublicChatState,
  PublicChatApiError,
  requestPublicChatHuman,
  sendPublicChatMessage,
} from "@/lib/public-chat/client";
import type {
  PublicChatConfigResponse,
  PublicChatMessageRead,
} from "@/lib/public-chat/types";
import {
  resolveTheme,
  type PublicChatTheme,
} from "@/lib/public-chat/theme";

// Client widget for the public web-chat embed (Phase 1P.1, hardened 1P.6).
//
// The widget mirrors backend state transitions: launcher -> connecting ->
// ai_active; ai_active may move to human_requested (handoff) after a message
// or a manual human request; a staff member may then claim the session
// (human_assigned) or return it to the queue (back to human_requested); the
// session ends in error or closed.
//
// Phase 1P.6: the first click opens the panel immediately — the connect work
// starts behind a composing/session request while the panel is already being
// shown in the connecting state, and the widest expansion message is posted to
// the parent loader with no config round-trip in front of it. A single-flight
// ref means the launcher cannot be double-clicked into two sessions. The tenant
// config is also prefetched (GET-only, best-effort, never session-creating) on
// mount so a RISPU theme is already resolved before the first click. Once a
// session is handed off to a human the composer stays enabled: follow-up
// messages go straight to the human conversation and the backend runs no AI.
//
// Security: the embedding origin is derived from document.referrer and sent in
// X-Embedding-Origin for server-side allow-listing. Every tenant-supplied
// string (display_name, welcome_message, replies) is rendered as a plain text
// node — never as HTML. The theme is resolved from a closed allowlist: the
// tenant's opaque theme_token can only ever select one of two authored
// palettes, so no tenant input becomes CSS or markup.

type WidgetState =
  | "launcher"
  | "connecting"
  | "ai_active"
  | "human_requested"
  | "human_assigned"
  | "error"
  | "closed";

const POLL_INTERVAL_MS = 20_000;

const WELCOME_FALLBACK =
  "Welcome to CXOps support.";

// Authored animations for the widget's own chrome. Closed constants, never
// tenant-controlled; the reduced-motion media query disables them.
const EMBED_STYLE_RULES = `
@keyframes cxops-chat-spin {
  to { transform: rotate(360deg); }
}
@keyframes cxops-chat-panel-in {
  from {
    opacity: 0;
    transform: translateY(10px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
.cxops-chat-spinner {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid rgba(128, 128, 128, 0.25);
  border-top-color: currentColor;
  animation: cxops-chat-spin 0.8s linear infinite;
  flex: none;
}
.cxops-chat-panel {
  animation: cxops-chat-panel-in 200ms ease-out;
}
.cxops-chat-input::placeholder {
  color: var(--cxops-chat-placeholder, #64748b);
}
/* Visual hotfix (Phase 1P.6): the embed runs in an iframe the loader sizes
   ~40px larger than the panel so the 20px breathing room and shadow are
   preserved. Keep that document's html/body/main transparent so the host
   site stays visible behind the panel; this component is rendered only by
   /chat/embed, so the rules never reach normal CXOps/control-center pages. */
html,
body {
  background: transparent !important;
}
body {
  min-height: 0;
}
main {
  background: transparent !important;
}
@media (prefers-reduced-motion: reduce) {
  .cxops-chat-spinner,
  .cxops-chat-panel { animation: none; }
}
`;

function clientMessageId(): string {
  return crypto.randomUUID().replaceAll("-", "");
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    );
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

function LauncherButton({
  theme,
  onClick,
  ariaLabel,
}: {
  theme: PublicChatTheme;
  onClick: () => void;
  ariaLabel: string;
}) {
  const reducedMotion = usePrefersReducedMotion();
  const [hovered, setHovered] = useState(false);
  const [pressed, setPressed] = useState(false);

  const transform = reducedMotion
    ? undefined
    : pressed
      ? "scale(0.94)"
      : hovered
        ? "scale(1.06)"
        : undefined;

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => {
        setHovered(false);
        setPressed(false);
      }}
      onMouseDown={() => setPressed(true)}
      onMouseUp={() => setPressed(false)}
      style={{
        width: "56px",
        height: "56px",
        borderRadius: "50%",
        border:
          theme.launcher.borderWidth > 0
            ? `${theme.launcher.borderWidth}px solid ${theme.launcher.border}`
            : "none",
        background: theme.launcher.background,
        color: theme.launcher.foreground,
        cursor: "pointer",
        boxShadow: "0 6px 16px rgba(0, 0, 0, 0.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
        transform,
        transition: reducedMotion
          ? "none"
          : "transform 180ms ease, opacity 180ms ease",
        opacity: pressed ? 0.85 : 1,
      }}
    >
      {theme.launcher.glyph === "svg" ? (
        <svg
          width="26"
          height="26"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="M4 5.5h16a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5H11l-4.25 3.2a.6.6 0 0 1-.95-.48V17.6H4A1.5 1.5 0 0 1 2.5 16V7A1.5 1.5 0 0 1 4 5.5Z"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinejoin="round"
          />
          <circle cx="8" cy="11" r="1" fill="currentColor" />
          <circle cx="12" cy="11" r="1" fill="currentColor" />
          <circle cx="16" cy="11" r="1" fill="currentColor" />
        </svg>
      ) : (
        <span
          aria-hidden="true"
          style={{ fontSize: "24px", lineHeight: 1 }}
        >
          💬
        </span>
      )}
    </button>
  );
}

export function PublicChatWidget({
  widgetKey,
}: {
  widgetKey: string;
}) {
  const [state, setState] =
    useState<WidgetState>("launcher");
  const [config, setConfig] =
    useState<PublicChatConfigResponse | null>(null);
  const [sessionToken, setSessionToken] =
    useState<string | null>(null);
  const [messages, setMessages] =
    useState<PublicChatMessageRead[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [errorMessage, setErrorMessage] =
    useState<string | null>(null);
  const [composerFocused, setComposerFocused] =
    useState(false);

  const embeddingOriginRef =
    useRef<string | null>(null);
  const sessionTokenRef =
    useRef<string | null>(null);
  const pollRef =
    useRef<ReturnType<typeof setInterval> | null>(null);
  const connectInflightRef =
    useRef(false);

  const theme = resolveTheme(config?.theme_token);

  const composerEnabled =
    state === "ai_active" ||
    state === "human_requested" ||
    state === "human_assigned";

  const clearPoll = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const loadState = useCallback(
    async (token: string): Promise<WidgetState> => {
      const nextState =
        await fetchPublicChatState(token);
      setMessages(nextState.messages);
      if (nextState.status === "human_requested") {
        setState("human_requested");
        return "human_requested";
      }
      if (nextState.status === "human_assigned") {
        setState("human_assigned");
        return "human_assigned";
      }
      if (nextState.status === "closed") {
        setState("closed");
        return "closed";
      }
      setState("ai_active");
      return "ai_active";
    },
    [],
  );

  const persistSession = useCallback(
    (token: string) => {
      try {
        window.sessionStorage.setItem(
          CHAT_SESSION_STORAGE_KEY,
          token,
        );
      } catch {
        // Storage may be unavailable (private mode); the session still works.
      }
    },
    [],
  );

  const discardSession = useCallback(() => {
    try {
      window.sessionStorage.removeItem(
        CHAT_SESSION_STORAGE_KEY,
      );
    } catch {
      // Ignore storage failures.
    }
  }, []);

  const readStoredSession = useCallback((): string | null => {
    try {
      return window.sessionStorage.getItem(
        CHAT_SESSION_STORAGE_KEY,
      );
    } catch {
      return null;
    }
  }, []);

  // Bounded, best-effort public-config prefetch started when the widget
  // mounts, so a tenant theme (e.g. RISPU) is resolved before the customer
  // ever clicks the launcher. It only ever GETs public config — it never
  // creates a session — and the shared promise dedupes concurrent callers so
  // open() does not issue a second GET while the prefetch is in flight.
  const configPrefetchRef =
    useRef<Promise<PublicChatConfigResponse> | null>(null);

  const getPublicConfig = useCallback(
    (): Promise<PublicChatConfigResponse> => {
      if (configPrefetchRef.current === null) {
        configPrefetchRef.current =
          fetchPublicChatConfig(widgetKey).finally(() => {
            configPrefetchRef.current = null;
          });
      }
      return configPrefetchRef.current;
    },
    [widgetKey],
  );

  // The whole connect flow lives behind one button, so the panel opens in the
  // connecting state synchronously on the first click. A single-flight ref
  // makes rapid double-clicks collapse into the same work instead of creating
  // two server sessions. open() never awaits a config request before
  // expanding the panel: a session is still only created after the click.
  const open = useCallback(async () => {
    if (connectInflightRef.current) {
      return;
    }
    connectInflightRef.current = true;
    setErrorMessage(null);
    setState("connecting");

    try {
      let token = sessionTokenRef.current;
      let resumed: WidgetState | null = null;

      const storedToken =
        !token ? readStoredSession() : null;

      if (!token && storedToken) {
        // Resume: load the session state and the tenant config at the same
        // time; neither waits on the other. When the mount prefetch has
        // already resolved, reuse that authored config instead of issuing a
        // second public-config GET.
        const statePromise = loadState(storedToken);
        const configPromise = config
          ? Promise.resolve(config)
          : getPublicConfig();
        const [stateResult, configResult] =
          await Promise.allSettled([
            statePromise,
            configPromise,
          ]);
        if (stateResult.status === "fulfilled") {
          token = storedToken;
          resumed = stateResult.value;
        } else {
          discardSession();
        }
        if (configResult.status === "fulfilled") {
          setConfig(configResult.value);
        }
      }

      if (!token) {
        // Fresh session. createPublicChatSession returns the config, so there
        // is no separate, redundant config request to open the panel.
        const created =
          await createPublicChatSession(
            widgetKey,
            embeddingOriginRef.current,
          );
        setConfig(created.config);
        token = created.session.token;
        persistSession(token);
        const welcome: PublicChatMessageRead = {
          id: -1,
          direction: "outbound",
          body:
            created.config.welcome_message ||
            WELCOME_FALLBACK,
          sent_at: null,
        };
        setMessages([welcome]);
      }

      if (token) {
        setSessionToken(token);
        setState(resumed ?? "ai_active");
      }
    } catch (error) {
      setErrorMessage(
        error instanceof PublicChatApiError
          ? error.message
          : "Unable to reach the support channel.",
      );
      setState("error");
    } finally {
      connectInflightRef.current = false;
    }
  }, [
    config,
    discardSession,
    getPublicConfig,
    loadState,
    persistSession,
    readStoredSession,
    widgetKey,
  ]);

  const sendMessage = useCallback(
    async (text: string) => {
      const token = sessionTokenRef.current;
      if (!token || !text.trim() || sending) {
        return;
      }
      const trimmed = text.trim();
      setInput("");
      setSending(true);

      const optimistic: PublicChatMessageRead = {
        id: -Date.now(),
        direction: "inbound",
        body: trimmed,
        sent_at: null,
      };
      setMessages((previous) => [
        ...previous,
        optimistic,
      ]);

      try {
        const result = await sendPublicChatMessage(
          token,
          embeddingOriginRef.current,
          clientMessageId(),
          trimmed,
        );
        const reply = result.reply;
        if (reply) {
          setMessages((previous) => [
            ...previous,
            {
              id: -Date.now(),
              direction: "outbound",
              body: reply,
              sent_at: null,
            },
          ]);
        }
        if (result.status === "human_assigned") {
          setState("human_assigned");
        } else if (
          result.handoff ||
          result.status === "human_requested"
        ) {
          setState("human_requested");
        } else {
          setState("ai_active");
        }
      } catch (error) {
        setErrorMessage(
          error instanceof PublicChatApiError
            ? error.message
            : "Your message could not be sent. Please try again.",
        );
        setMessages((previous) =>
          previous.filter(
            (message) => message !== optimistic,
          ),
        );
        setState("error");
      } finally {
        setSending(false);
      }
    },
    [sending],
  );

  const requestHuman = useCallback(async () => {
    const token = sessionTokenRef.current;
    if (!token) {
      return;
    }
    setSending(true);
    try {
      const result =
        await requestPublicChatHuman(token);
      setState("human_requested");
      if (result.reply) {
        setMessages((previous) => [
          ...previous,
          {
            id: -Date.now(),
            direction: "outbound",
            body: result.reply,
            sent_at: null,
          },
        ]);
      }
    } catch (error) {
      setErrorMessage(
        error instanceof PublicChatApiError
          ? error.message
          : "Unable to request human support.",
      );
      setState("error");
    } finally {
      setSending(false);
    }
  }, []);

  const closeChat = useCallback(async () => {
    clearPoll();
    const token = sessionTokenRef.current;
    if (token) {
      try {
        await closePublicChatSession(token);
      } catch {
        // The widget still closes locally even if the backend is unreachable.
      }
    }
    discardSession();
    setSessionToken(null);
    setState("closed");
  }, [clearPoll, discardSession]);

  useEffect(() => {
    embeddingOriginRef.current =
      createEmbeddingOrigin(document.referrer);
  }, []);

  useEffect(() => {
    // Best-effort theme prefetch, bounded to a single GET via getPublicConfig.
    // It never creates a session and never gates the launcher: on failure the
    // widget keeps the default theme, and open() can still create a session
    // normally and receive authoritative config from the response.
    let cancelled = false;
    getPublicConfig()
      .then((resolved) => {
        if (!cancelled) {
          setConfig((previous) => previous ?? resolved);
        }
      })
      .catch(() => {
        // The prefetch is an optimization, not a gate.
      });
    return () => {
      cancelled = true;
    };
  }, [getPublicConfig]);

  useEffect(() => {
    sessionTokenRef.current = sessionToken;
  }, [sessionToken]);

  useEffect(() => {
    if (
      state !== "ai_active" &&
      state !== "human_requested" &&
      state !== "human_assigned"
    ) {
      return;
    }
    const token = sessionTokenRef.current;
    if (!token) {
      return;
    }

    clearPoll();
    pollRef.current = setInterval(() => {
      loadState(token).catch(() => {
        // Poll failures are transient; the next tick retries.
      });
    }, POLL_INTERVAL_MS);

    return clearPoll;
  }, [state, clearPoll, loadState]);

  useEffect(() => {
    const isPanel =
      state !== "launcher" && state !== "closed";
    // The panel requests its intended logical size, not a size derived from
    // this iframe's own viewport: inside the iframe, window.inner* reports the
    // iframe's viewport (the loader creates it at 96x96), so deriving the
    // panel size from it produces a tiny panel on open. The parent loader
    // clamps against the real page viewport instead. The logical width comes
    // from the authored theme (380 default / 400 rispu), never a shared
    // constant, so default tenants keep their original dimensions.
    const width = isPanel ? theme.panel.width : 56;
    const height = isPanel ? 540 : 56;
    window.parent.postMessage(
      {
        type: "cxops-embed:resize",
        width,
        height,
      },
      "*",
    );
  }, [state, theme]);

  useEffect(() => {
    return () => {
      clearPoll();
    };
  }, [clearPoll]);

  // The authored chrome styles must apply to the /chat/embed document in every
  // widget state — launcher and closed render a div, not the panel section —
  // so the transparent-frame rules reach html/body even before a click.
  const embedStyles = <style>{EMBED_STYLE_RULES}</style>;

  if (state === "launcher") {
    return (
      <div
        style={{
          position: "fixed",
          right: "20px",
          bottom: "20px",
          zIndex: 9990,
        }}
      >
        {embedStyles}
        <LauncherButton
          theme={theme}
          onClick={() => void open()}
          ariaLabel="Open support chat"
        />
      </div>
    );
  }

  if (state === "closed") {
    return (
      <div
        style={{
          position: "fixed",
          right: "20px",
          bottom: "20px",
          zIndex: 9990,
        }}
      >
        {embedStyles}
        <LauncherButton
          theme={theme}
          onClick={() => setState("launcher")}
          ariaLabel="Reopen support chat"
        />
      </div>
    );
  }

  const panelTitle =
    state === "error"
      ? "Something went wrong"
      : state === "connecting"
        ? "Connecting"
        : config?.display_name ?? "Support";

  return (
    <section
      role="dialog"
      aria-modal="true"
      aria-labelledby="cxops-chat-title"
      className="cxops-chat-panel"
      style={
        {
          position: "fixed",
          right: "20px",
          bottom: "20px",
          zIndex: 9990,
          width: `min(${theme.panel.width}px, calc(100vw - 24px))`,
          height: "min(540px, calc(100vh - 24px))",
          display: "flex",
          flexDirection: "column",
          borderRadius: `${theme.panel.radius}px`,
          border: `1px solid ${theme.panel.border}`,
          background: theme.panel.background,
          boxShadow: theme.panel.shadow,
          overflow: "hidden",
          fontFamily:
            "var(--font-geist-sans, system-ui, sans-serif)",
          // Author-only CSS variable: the closed palette supplies the
          // composer placeholder color; the ::placeholder rule lives in
          // EMBED_STYLE_RULES. Tenants can never inject CSS here.
          "--cxops-chat-placeholder":
            theme.composer.placeholder,
        } as CSSProperties
      }
    >
      {embedStyles}

      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "12px",
          padding: "12px 16px",
          background: theme.header.background,
          color: theme.header.foreground,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <h2
            id="cxops-chat-title"
            style={{
              margin: 0,
              fontSize: "15px",
              fontWeight: 600,
            }}
          >
            {panelTitle}
          </h2>
          {theme.header.subtitle && (
            <p
              style={{
                margin: "2px 0 0",
                fontSize: "12px",
                color: theme.header.subtitleForeground,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {theme.header.subtitle}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={closeChat}
          aria-label="Close chat"
          style={{
            border: "none",
            background: "transparent",
            color: theme.header.foreground,
            fontSize: "20px",
            cursor: "pointer",
            lineHeight: 1,
            flex: "none",
          }}
        >
          <span aria-hidden="true">✕</span>
        </button>
      </header>

      {(state === "human_requested" ||
        state === "human_assigned") && (
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            padding: "8px 16px 0",
          }}
        >
          <p
            role="status"
            style={{
              margin: 0,
              padding: "4px 12px",
              borderRadius: "999px",
              background: theme.statusChip.background,
              color: theme.statusChip.foreground,
              border: `1px solid ${theme.statusChip.border}`,
              fontSize: "12px",
              fontWeight: 500,
            }}
          >
            {state === "human_assigned"
              ? "An agent is reviewing this conversation"
              : "Sent for human review"}
          </p>
        </div>
      )}

      <ul
        role="log"
        aria-live="polite"
        aria-atomic="false"
        style={{
          flex: 1,
          overflowY: "auto",
          margin: 0,
          padding: "16px",
          listStyle: "none",
          display: "flex",
          flexDirection: "column",
          justifyContent: state === "connecting"
            ? "center"
            : undefined,
          gap: "8px",
        }}
      >
        {state === "connecting" && messages.length === 0 && (
          <li
            role="status"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "10px",
              color: theme.text.secondary,
              fontSize: "13px",
            }}
          >
            <span
              className="cxops-chat-spinner"
              aria-hidden="true"
            />
            Connecting to support…
          </li>
        )}
        {messages.map((message, index) => {
          const isInbound =
            message.direction === "inbound";
          return (
            <li
              key={`${message.id}-${index}`}
              style={{
                alignSelf: isInbound
                  ? "flex-start"
                  : "flex-end",
                maxWidth: "85%",
                padding: "8px 12px",
                borderRadius: `${theme.bubble.radius}px`,
                background: isInbound
                  ? theme.bubble.inboundBackground
                  : theme.bubble.outboundBackground,
                color: isInbound
                  ? theme.bubble.inboundForeground
                  : theme.bubble.outboundForeground,
                border: isInbound
                  ? "none"
                  : `1px solid ${theme.bubble.outboundBorder}`,
                fontSize: "14px",
                lineHeight: 1.4,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {message.body}
            </li>
          );
        })}
        {sending && (
          <li
            style={{
              alignSelf: "flex-start",
              color: theme.text.secondary,
              fontSize: "13px",
              fontStyle: "italic",
            }}
          >
            …sending
          </li>
        )}
      </ul>

      {errorMessage && (
        <div
          style={{
            padding: "8px 16px",
            background: "#fee2e2",
            color: "#7f1d1d",
            fontSize: "13px",
          }}
        >
          <p role="alert" style={{ margin: 0 }}>
            {errorMessage}
          </p>
          {state === "error" && (
            <button
              type="button"
              onClick={() => void open()}
              style={{
                marginTop: "8px",
                padding: "6px 12px",
                borderRadius: "8px",
                border: "1px solid #fca5a5",
                background: "#ffffff",
                color: "#7f1d1d",
                fontSize: "13px",
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              Try again
            </button>
          )}
        </div>
      )}

      <footer
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          padding: "12px 16px",
          borderTop: `1px solid ${theme.panel.border}`,
          background: theme.composer.background,
        }}
      >
        <div
          style={{
            display: "flex",
            gap: "8px",
          }}
        >
          <label
            htmlFor="cxops-chat-input"
            style={{
              position: "absolute",
              width: "1px",
              height: "1px",
              overflow: "hidden",
              clip: "rect(0 0 0 0)",
            }}
          >
            Message
          </label>
          <input
            id="cxops-chat-input"
            className="cxops-chat-input"
            type="text"
            value={input}
            onChange={(event) =>
              setInput(event.target.value)
            }
            onFocus={() => setComposerFocused(true)}
            onBlur={() => setComposerFocused(false)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey
              ) {
                event.preventDefault();
                if (composerEnabled) {
                  void sendMessage(input);
                }
              }
            }}
            maxLength={config?.max_message_length ?? 4000}
            disabled={!composerEnabled || sending}
            placeholder="Type your message…"
            autoComplete="off"
            style={{
              flex: 1,
              padding: "8px 12px",
              borderRadius: "8px",
              border: `1px solid ${
                composerFocused
                  ? theme.composer.focusBorder
                  : theme.composer.border
              }`,
              background: "transparent",
              color: theme.composer.foreground,
              fontSize: "14px",
              minWidth: 0,
              outline: "none",
              boxShadow: composerFocused
                ? `0 0 0 3px ${theme.composer.focusBorder}22`
                : undefined,
            }}
          />
          <button
            type="button"
            onClick={() => void sendMessage(input)}
            disabled={
              !composerEnabled ||
              sending ||
              !input.trim()
            }
            aria-label="Send message"
            style={{
              padding: "8px 14px",
              borderRadius: "8px",
              border: "none",
              background: theme.composer.sendBackground,
              color: theme.composer.sendForeground,
              fontSize: "14px",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            Send
          </button>
        </div>

        {state === "ai_active" && (
          <button
            type="button"
            onClick={() => void requestHuman()}
            disabled={sending}
            style={{
              padding: "8px 12px",
              borderRadius: "8px",
              border: `1px solid ${theme.composer.border}`,
              background: "transparent",
              color: theme.text.primary,
              fontSize: "13px",
              cursor: "pointer",
            }}
          >
            Chat with a human
          </button>
        )}
      </footer>
    </section>
  );
}