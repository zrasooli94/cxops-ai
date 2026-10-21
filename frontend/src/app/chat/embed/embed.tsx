"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";

import {
  CHAT_SESSION_STORAGE_KEY,
  createEmbeddingOrigin,
  createPublicChatSession,
  closePublicChatSession,
  fetchPublicChatConfig,
  fetchPublicChatState,
  fetchPublicChatUpdates,
  PublicChatApiError,
  requestPublicChatHuman,
  sendPublicChatMessage,
} from "@/lib/public-chat/client";
import {
  handoffCopy,
  isHandoffState,
} from "@/lib/public-chat/handoff-copy";
import {
  isNearChatBottom,
  messagePresentation,
} from "@/lib/public-chat/conversation-view";
import {
  clientMessageIdForSend,
  sendWithRetry,
  type PendingSend,
} from "@/lib/public-chat/send-retry";
import {
  availableChatStarterPrompts,
  shouldShowChatStarters,
} from "@/lib/public-chat/starter-prompts";
import {
  cursorOf,
  LEGACY_POLL_INTERVAL_MS,
  mergeMessages,
  nextPollDelay,
} from "@/lib/public-chat/updates";
import type {
  PublicChatConfigResponse,
  PublicChatMessageRead,
} from "@/lib/public-chat/types";
import {
  resolveTheme,
  type PublicChatTheme,
} from "@/lib/public-chat/theme";

// Client widget for the public web-chat embed (Phase 1P.1, hardened 1P.6+).
//
// Server/session state transitions: connecting -> ai_active; ai_active may move
// to human_requested (handoff) after a message or a manual human request; a
// staff member may then claim the session (human_assigned) or return it to the
// queue (back to human_requested); "closed" is terminal (explicit End or a
// server-side close).
//
// The launcher is NOT part of session state (Phase 1P.6.1): a UI-only
// ``panelOpen`` flag decides whether the panel or the launcher is visible. The
// header X minimizes — it only hides the panel and keeps the conversation,
// token, and stored session so reopening restores the same chat. Ending a
// conversation is an explicit, confirmed action (sent to the backend and the
// stored token removed). Reload persistence still relies on sessionStorage
// (D): CHAT_SESSION_STORAGE_KEY + GET /sessions/state.
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
  | "connecting"
  | "ai_active"
  | "human_requested"
  | "human_assigned"
  | "error"
  | "closed";

// Polling is paced by the server's ``poll_after_ms`` hint (Phase 1P.9): about
// 1 s while an AI reply is pending, slower when idle, slower again in a hidden
// tab. Older backends without /sessions/updates fall back to the original 20 s
// full-state poll.

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
@media (prefers-reduced-motion: reduce) {
  .cxops-chat-spinner,
  .cxops-chat-panel { animation: none; }
}
`;

// The iframe host needs transparency; the development showroom does not.
// Keep this as a separate constant so previewing the actual widget inline
// does not make the showroom page's html/body/main transparent.
const EMBED_DOCUMENT_STYLE_RULES = "/* Visual hotfix (Phase 1P.6): the embed runs in an iframe the loader sizes\n   ~40px larger than the panel so the 20px breathing room and shadow are\n   preserved. Keep that document's html/body/main transparent so the host\n   site stays visible behind the panel; this component is rendered only by\n   /chat/embed, so the rules never reach normal CXOps/control-center pages. */\nhtml,\nbody {\n  background: transparent !important;\n}\nbody {\n  min-height: 0;\n}\nmain {\n  background: transparent !important;\n}\n";

// Phase 1Q-R: the API refuses a widget's config with 403/404 when the widget
// is switched off, its key is wrong, or the tenant is not approved/suspended.
// That is final for this page view: the launcher is not shown and no session
// is resumed or polled.
const UNAVAILABLE_MESSAGE = "Chat is not available right now.";

function isWidgetRefusal(error: unknown): boolean {
  return (
    error instanceof PublicChatApiError &&
    (error.status === 403 || error.status === 404)
  );
}

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
  buttonRef,
}: {
  theme: PublicChatTheme;
  onClick: () => void;
  ariaLabel: string;
  buttonRef: RefObject<HTMLButtonElement | null>;
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
      ref={buttonRef}
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
  fetcher = fetch,
  sessionStorageKey = CHAT_SESSION_STORAGE_KEY,
  transparentFrame = true,
  initialPollDelayMs = LEGACY_POLL_INTERVAL_MS,
}: {
  widgetKey: string;
  /** Optional isolated transport for a local-only synthetic preview. */
  fetcher?: typeof fetch;
  sessionStorageKey?: string;
  transparentFrame?: boolean;
  /** Faster synthetic preview polling without changing live-widget defaults. */
  initialPollDelayMs?: number;
}) {
  // Server/session state. "closed" is terminal (explicit End or a server-side
  // close); the launcher is a separate UI concern — see ``panelOpen`` below.
  const [state, setState] =
    useState<WidgetState>("closed");
  // UI-only visibility: when false, the launcher renders no matter what the
  // session state is. Minimize never touches ``state``.
  const [unavailable, setUnavailable] = useState(false);
  const [panelOpen, setPanelOpen] =
    useState(false);
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
  const [endConfirm, setEndConfirm] =
    useState(false);
  const [newConversationConfirm, setNewConversationConfirm] =
    useState(false);

  const embeddingOriginRef =
    useRef<string | null>(null);
  const sessionTokenRef =
    useRef<string | null>(null);
  const pollRef =
    useRef<ReturnType<typeof setTimeout> | null>(null);
  // Latest messages for the cursor, read inside timers without re-subscribing.
  const messagesRef =
    useRef<PublicChatMessageRead[]>([]);
  // False once the backend is known to lack /sessions/updates.
  const updatesSupportedRef =
    useRef(true);
  const pollNowRef =
    useRef<(() => void) | null>(null);
  // Phase 1P.9R: the last send that failed ambiguously. Resending the same
  // text reuses its client_message_id, so a message the server did store is
  // never stored twice.
  const pendingSendRef =
    useRef<PendingSend | null>(null);
  const composerRef = useRef<HTMLInputElement>(null);
  const messageLogRef = useRef<HTMLUListElement>(null);
  const launcherButtonRef = useRef<HTMLButtonElement>(null);
  const minimizeButtonRef = useRef<HTMLButtonElement>(null);
  const restoreLauncherFocusRef = useRef(false);
  const followLatestRef = useRef(true);
  const [readingOlderMessages, setReadingOlderMessages] = useState(false);
  // An accepted message whose AI reply has not arrived yet.
  const [awaitingReply, setAwaitingReply] =
    useState(false);
  const connectInflightRef =
    useRef(false);

  const theme = resolveTheme(config?.theme_token);

  const composerEnabled =
    state === "ai_active" ||
    state === "human_requested" ||
    state === "human_assigned";

  // These are authored suggestions, not tenant facts or automatic messages.
  // A click only fills the editable composer; Send is always a separate act.
  const starterPrompts = availableChatStarterPrompts(
    config?.max_message_length ?? 4000,
  );
  const showStarterPrompts =
    starterPrompts.length > 0 &&
    shouldShowChatStarters({
      status: state,
      hasVisitorMessage: messages.some(
        (message) => message.direction === "inbound",
      ),
      hasDraft: input.trim().length > 0,
      busy: sending || awaitingReply,
    });

  const clearPoll = useCallback(() => {
    if (pollRef.current !== null) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const applyStatus = useCallback(
    (status: string): WidgetState => {
      if (status === "human_requested") {
        setState("human_requested");
        return "human_requested";
      }
      if (status === "human_assigned") {
        setState("human_assigned");
        return "human_assigned";
      }
      if (status === "closed") {
        setState("closed");
        return "closed";
      }
      setState("ai_active");
      return "ai_active";
    },
    [],
  );

  // One poll: incremental updates when available, else the full state.
  // Returns the server's suggested delay before the next poll.
  const pollOnce = useCallback(
    async (token: string): Promise<number> => {
      if (updatesSupportedRef.current) {
        const updates = await fetchPublicChatUpdates(
          token,
          cursorOf(messagesRef.current),
          fetcher,
        );
        if (updates) {
          if (updates.messages.length > 0) {
            setMessages((previous) =>
              mergeMessages(previous, updates.messages),
            );
          }
          setAwaitingReply(updates.turns.length > 0);
          applyStatus(updates.status);
          return updates.poll_after_ms;
        }
        updatesSupportedRef.current = false;
      }
      const nextState =
        await fetchPublicChatState(token, fetcher);
      setMessages(nextState.messages);
      setAwaitingReply(false);
      applyStatus(nextState.status);
      return LEGACY_POLL_INTERVAL_MS;
    },
    [applyStatus, fetcher],
  );

  const loadState = useCallback(
    async (token: string): Promise<WidgetState> => {
      const nextState =
        await fetchPublicChatState(token, fetcher);
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
    [fetcher],
  );

  const persistSession = useCallback(
    (token: string) => {
      try {
        window.sessionStorage.setItem(
          sessionStorageKey,
          token,
        );
      } catch {
        // Storage may be unavailable (private mode); the session still works.
      }
    },
    [sessionStorageKey],
  );

  const discardSession = useCallback(() => {
    try {
      window.sessionStorage.removeItem(
        sessionStorageKey,
      );
    } catch {
      // Ignore storage failures.
    }
  }, [sessionStorageKey]);

  const readStoredSession = useCallback((): string | null => {
    try {
      return window.sessionStorage.getItem(
        sessionStorageKey,
      );
    } catch {
      return null;
    }
  }, [sessionStorageKey]);

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
          fetchPublicChatConfig(widgetKey, fetcher).finally(() => {
            configPrefetchRef.current = null;
          });
      }
      return configPrefetchRef.current;
    },
    [widgetKey, fetcher],
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
    setErrorMessage(null);

    // Reopening a minimized session: show the same in-memory conversation
    // immediately. No backend close, no storage discard, no token clear, and
    // no new session — the state (ai_active / human_requested /
    // human_assigned) is already correct and the active polling reconciles
    // server truth.
    if (sessionTokenRef.current) {
      followLatestRef.current = true;
      setReadingOlderMessages(false);
      setPanelOpen(true);
      return;
    }

    connectInflightRef.current = true;
    followLatestRef.current = true;
    setReadingOlderMessages(false);
    setPanelOpen(true);
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
        } else if (isWidgetRefusal(configResult.reason)) {
          // The widget was switched off or the tenant suspended since this
          // session started: do not resume it as if the chat were live.
          discardSession();
          throw configResult.reason;
        }
      }

      if (!token) {
        // Fresh session. createPublicChatSession returns the config, so there
        // is no separate, redundant config request to open the panel.
        const created =
          await createPublicChatSession(
            widgetKey,
            embeddingOriginRef.current,
            fetcher,
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
      if (isWidgetRefusal(error)) {
        setUnavailable(true);
        setErrorMessage(UNAVAILABLE_MESSAGE);
      } else {
        setErrorMessage(
          error instanceof PublicChatApiError
            ? error.message
            : "Unable to reach the support channel.",
        );
      }
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
    fetcher,
  ]);

  const sendMessage = useCallback(
    async (text: string) => {
      const token = sessionTokenRef.current;
      if (!token || !text.trim() || sending) {
        return;
      }
      const trimmed = text.trim();
      // An explicit customer send always reveals their own new message.
      followLatestRef.current = true;
      setReadingOlderMessages(false);
      setErrorMessage(null);
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

      const messageId = clientMessageIdForSend(
        trimmed,
        pendingSendRef.current,
        clientMessageId,
      );
      pendingSendRef.current = {
        text: trimmed,
        clientMessageId: messageId,
      };

      try {
        // Ambiguous failures (network drop, 5xx, 409 in flight, 429 busy)
        // are retried with the SAME id; the server de-duplicates.
        const result = await sendWithRetry(() =>
          sendPublicChatMessage(
            token,
            embeddingOriginRef.current,
            messageId,
            trimmed,
            fetcher,
            "async",
          ),
        );
        pendingSendRef.current = null;
        if (result.processing_state === "accepted") {
          // Stored; the reply follows through /sessions/updates.
          setAwaitingReply(true);
          pollNowRef.current?.();
        }
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
        // The server might already have accepted the ambiguous send.
        // Restore the draft and keep its pending client_message_id so an
        // identical retry is deduplicated rather than sent twice.
        setInput(trimmed);
        setMessages((previous) =>
          previous.filter(
            (message) => message !== optimistic,
          ),
        );
        // Transport failure must not disable a valid AI/human composer.
      } finally {
        setSending(false);
      }
    },
    [sending, fetcher],
  );

  const requestHuman = useCallback(async () => {
    const token = sessionTokenRef.current;
    if (!token) {
      return;
    }
    setErrorMessage(null);
    setSending(true);
    try {
      const result =
        await requestPublicChatHuman(token, fetcher);
      setState("human_requested");
      pollNowRef.current?.();
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
      // Failed handoff API requests do not invalidate the conversation.
      // The visitor can retry with the existing session and draft.
    } finally {
      setSending(false);
    }
  }, [fetcher]);

  const minimize = useCallback(() => {
    // UI-only collapse: hide the panel but keep the conversation, the in-memory
    // session token, and the persisted token. No backend call, no storage
    // removal, no status change — reopening shows the same conversation.
    // Restore the launcher as the keyboard/screenreader focus target.
    restoreLauncherFocusRef.current = true;
    setPanelOpen(false);
  }, []);

  const endConversation = useCallback(async () => {
    const token = sessionTokenRef.current;
    // No server token means there is no remote conversation to close; local
    // cleanup is safe because nothing remains running on the backend.
    if (!token) {
      clearPoll();
      discardSession();
      setSessionToken(null);
      setMessages([]);
      setPanelOpen(false);
      setEndConfirm(false);
      setState("closed");
      return;
    }

    try {
      await closePublicChatSession(token, fetcher);
    } catch {
      // A failed backend close must not diverge the client from a still-active
      // server session: keep the token, stored token, messages, panel, state,
      // and polling so the user can retry against the same conversation.
      setErrorMessage(
        "Unable to end this conversation right now. Please try again.",
      );
      setEndConfirm(false);
      return;
    }

    // End confirmed by the backend — only now is local cleanup safe.
    clearPoll();
    discardSession();
    setSessionToken(null);
    setMessages([]);
    setPanelOpen(false);
    setEndConfirm(false);
    setState("closed");
  }, [clearPoll, discardSession, fetcher]);

  // Close the current server session, then open a brand-new one. The old
  // conversation stays with the team; nothing is reactivated, and the AI never
  // answers inside a session that was handed to a human.
  const startNewConversation = useCallback(async () => {
    const token = sessionTokenRef.current;
    if (token) {
      try {
        await closePublicChatSession(token, fetcher);
      } catch {
        setErrorMessage(
          "Unable to start a new conversation right now. Please try again.",
        );
        return;
      }
    }
    clearPoll();
    discardSession();
    sessionTokenRef.current = null;
    pendingSendRef.current = null;
    setSessionToken(null);
    setMessages([]);
    setInput("");
    setEndConfirm(false);
    setNewConversationConfirm(false);
    setAwaitingReply(false);
    setState("closed");
    await open();
  }, [clearPoll, discardSession, open, fetcher]);

  useEffect(() => {
    embeddingOriginRef.current =
      createEmbeddingOrigin(document.referrer);
  }, []);

  // The launcher disappears when the panel opens; focus a named control
  // inside the dialog without automatically opening the mobile keyboard.
  // Minimize (including Escape) returns focus to the re-mounted launcher.
  useLayoutEffect(() => {
    if (panelOpen) {
      minimizeButtonRef.current?.focus();
    } else if (restoreLauncherFocusRef.current) {
      restoreLauncherFocusRef.current = false;
      launcherButtonRef.current?.focus();
    }
  }, [panelOpen]);

  useLayoutEffect(() => {
    if (panelOpen && followLatestRef.current && messageLogRef.current) {
      messageLogRef.current.scrollTop =
        messageLogRef.current.scrollHeight;
    }
  }, [messages, panelOpen]);

  const jumpToLatest = useCallback(() => {
    followLatestRef.current = true;
    setReadingOlderMessages(false);
    if (messageLogRef.current) {
      messageLogRef.current.scrollTop =
        messageLogRef.current.scrollHeight;
    }
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
      .catch((error: unknown) => {
        // The prefetch is an optimization, not a gate - except for a definite
        // refusal (widget off / tenant not live), which hides the launcher.
        if (!cancelled && isWidgetRefusal(error)) {
          setUnavailable(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [getPublicConfig]);

  useEffect(() => {
    sessionTokenRef.current = sessionToken;
  }, [sessionToken]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

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

    let cancelled = false;
    let inflight = false;
    let delay = initialPollDelayMs;

    const schedule = (ms: number) => {
      clearPoll();
      if (!cancelled) {
        pollRef.current = setTimeout(tick, ms);
      }
    };
    const tick = () => {
      if (cancelled || inflight) {
        return;
      }
      inflight = true;
      pollOnce(token)
        .then((hint) => {
          delay = hint;
        })
        .catch(() => {
          // Poll failures are transient; back off to the idle pace and retry.
          delay = LEGACY_POLL_INTERVAL_MS;
        })
        .finally(() => {
          inflight = false;
          schedule(nextPollDelay(delay, document.hidden));
        });
    };
    const onVisibility = () => {
      if (!document.hidden) {
        schedule(0);
      }
    };

    // A just-accepted message asks for a reply check without waiting for the
    // idle interval.
    pollNowRef.current = () => schedule(nextPollDelay(1_000, document.hidden));
    document.addEventListener("visibilitychange", onVisibility);
    schedule(nextPollDelay(delay, document.hidden));

    return () => {
      cancelled = true;
      pollNowRef.current = null;
      document.removeEventListener("visibilitychange", onVisibility);
      clearPoll();
    };
  }, [state, clearPoll, pollOnce, initialPollDelayMs]);

  useEffect(() => {
    const isPanel =
      panelOpen && state !== "closed";
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
  }, [panelOpen, state, theme]);

  useEffect(() => {
    return () => {
      clearPoll();
    };
  }, [clearPoll]);

  // The authored chrome styles must apply to the /chat/embed document in every
  // widget state — the launcher and the panel both render the sheet — so the
  // transparent-frame rules reach html/body even before a click.
  const embedStyles = (
    <style>{EMBED_STYLE_RULES}{transparentFrame ? EMBED_DOCUMENT_STYLE_RULES : ""}</style>
  );

  if (unavailable && !panelOpen) {
    // Nothing to offer: no launcher, no session, no polling.
    return embedStyles;
  }

  if (!panelOpen || state === "closed") {
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
          buttonRef={launcherButtonRef}
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
      aria-modal="false"
      aria-labelledby="cxops-chat-title"
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        if (newConversationConfirm) {
          setNewConversationConfirm(false);
        } else if (endConfirm) {
          setEndConfirm(false);
        } else {
          minimize();
        }
      }}
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
          ref={minimizeButtonRef}
          type="button"
          onClick={minimize}
          aria-label="Minimize chat"
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
            {handoffCopy(state).chip}
          </p>
        </div>
      )}

      {isHandoffState(state) && (
        <div
          style={{
            margin: "8px 16px 0",
            padding: "10px 12px",
            borderRadius: "10px",
            border: `1px solid ${theme.panel.border}`,
            color: theme.text.secondary,
            fontSize: "12.5px",
            lineHeight: 1.45,
          }}
        >
          <p style={{ margin: 0 }}>
            {handoffCopy(state).explanation}
          </p>
          {newConversationConfirm ? (
            <div style={{ marginTop: "8px" }}>
              <p style={{ margin: 0 }}>
                {handoffCopy(state).closeWarning}
              </p>
              <div
                style={{
                  display: "flex",
                  gap: "8px",
                  marginTop: "8px",
                  flexWrap: "wrap",
                }}
              >
                <button
                  type="button"
                  onClick={() => void startNewConversation()}
                  disabled={sending}
                  style={{
                    padding: "6px 12px",
                    borderRadius: "8px",
                    border: "none",
                    background: theme.composer.sendBackground,
                    color: theme.composer.sendForeground,
                    fontSize: "12.5px",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  {handoffCopy(state).confirmLabel}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setNewConversationConfirm(false)
                  }
                  style={{
                    padding: "6px 12px",
                    borderRadius: "8px",
                    border: `1px solid ${theme.composer.border}`,
                    background: "transparent",
                    color: theme.text.primary,
                    fontSize: "12.5px",
                    cursor: "pointer",
                  }}
                >
                  {handoffCopy(state).keepLabel}
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setNewConversationConfirm(true)}
              disabled={sending}
              style={{
                marginTop: "8px",
                padding: "6px 12px",
                borderRadius: "8px",
                border: `1px solid ${theme.composer.border}`,
                background: "transparent",
                color: theme.text.primary,
                fontSize: "12.5px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {handoffCopy(state).newConversationLabel}
            </button>
          )}
        </div>
      )}

      <ul
        ref={messageLogRef}
        role="log"
        aria-label="Support conversation"
        aria-live="polite"
        aria-atomic="false"
        onScroll={(event) => {
          const node = event.currentTarget;
          const nearBottom = isNearChatBottom(
            node.scrollHeight, node.scrollTop, node.clientHeight,
          );
          followLatestRef.current = nearBottom;
          setReadingOlderMessages(!nearBottom);
        }}
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
          const presentation = messagePresentation(message.direction);
          return (
            <li
              key={`${message.id}-${index}`}
              aria-label={presentation.sender + " message"}
              dir="auto"
              style={{
                alignSelf: presentation.alignSelf,
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
        {(sending || awaitingReply) && (
          <li
            style={{
              alignSelf: "flex-start",
              color: theme.text.secondary,
              fontSize: "13px",
              fontStyle: "italic",
            }}
          >
            {sending ? "…sending" : "…typing"}
          </li>
        )}
      </ul>

      {readingOlderMessages && (
        <button
          type="button"
          onClick={jumpToLatest}
          style={{
            alignSelf: "center",
            margin: "6px 0",
            padding: "8px 14px",
            borderRadius: "999px",
            border: `1px solid ${theme.statusChip.border}`,
            background: theme.statusChip.background,
            color: theme.statusChip.foreground,
            fontSize: "12px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Jump to latest messages
        </button>
      )}

      {showStarterPrompts && (
        <div
          aria-label="Start your support conversation"
          style={{
            padding: "10px 16px 12px",
            borderTop: `1px solid ${theme.panel.border}`,
            background: theme.panel.background,
          }}
        >
          <p
            style={{
              margin: 0,
              color: theme.text.primary,
              fontSize: "13px",
              fontWeight: 600,
            }}
          >
            Not sure where to begin?
          </p>
          <p
            style={{
              margin: "3px 0 9px",
              color: theme.text.secondary,
              fontSize: "12px",
              lineHeight: 1.4,
            }}
          >
            Choose a suggestion to fill your message. Nothing is sent until you press Send.
          </p>
          <div
            role="group"
            aria-label="Suggested messages"
            style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}
          >
            {starterPrompts.map((starter) => (
              <button
                key={starter.id}
                type="button"
                onClick={() => {
                  setInput(starter.draft);
                  composerRef.current?.focus();
                }}
                aria-label={`Draft a message: ${starter.label}`}
                style={{
                  padding: "7px 10px",
                  borderRadius: "999px",
                  border: `1px solid ${theme.composer.focusBorder}`,
                  background: theme.composer.background,
                  color: theme.text.primary,
                  fontSize: "12px",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                {starter.label}
              </button>
            ))}
          </div>
        </div>
      )}

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
            ref={composerRef}
            className="cxops-chat-input"
            type="text"
            dir="auto"
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
            placeholder={
              isHandoffState(state)
                ? handoffCopy(state).placeholder
                : "Type your message…"
            }
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

        <div
          style={{
            display: "flex",
            justifyContent: "center",
            paddingTop: "8px",
            borderTop: `1px solid ${theme.panel.border}`,
          }}
        >
          {endConfirm ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "8px",
                alignItems: "center",
                maxWidth: "100%",
              }}
            >
              <p
                style={{
                  margin: 0,
                  fontSize: "12px",
                  color: theme.text.secondary,
                  textAlign: "center",
                }}
              >
                End this conversation? You will start a
                new chat next time.
              </p>
              <div
                style={{
                  display: "flex",
                  gap: "8px",
                }}
              >
                <button
                  type="button"
                  onClick={() => void endConversation()}
                  style={{
                    padding: "6px 12px",
                    borderRadius: "8px",
                    border: "none",
                    background: theme.composer.sendBackground,
                    color: theme.composer.sendForeground,
                    fontSize: "12px",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  End conversation
                </button>
                <button
                  type="button"
                  onClick={() => setEndConfirm(false)}
                  style={{
                    padding: "6px 12px",
                    borderRadius: "8px",
                    border: `1px solid ${theme.composer.border}`,
                    background: "transparent",
                    color: theme.text.primary,
                    fontSize: "12px",
                    cursor: "pointer",
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setEndConfirm(true)}
              style={{
                border: "none",
                background: "transparent",
                color: theme.text.secondary,
                fontSize: "12px",
                cursor: "pointer",
                padding: "2px 6px",
                textDecoration: "underline",
              }}
            >
              End conversation
            </button>
          )}
        </div>
      </footer>
    </section>
  );
}