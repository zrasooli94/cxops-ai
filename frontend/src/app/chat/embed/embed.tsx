"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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

// Client widget for the public web-chat embed (Phase 1P.1).
//
// The widget mirrors backend state transitions: launcher -> connecting ->
// ai_active; ai_active may move to human_requested (handoff) after a message
// or a manual human request; a staff member may then claim the session
// (human_assigned) or return it to the queue (back to human_requested); the
// session ends in error or closed.
//
// Security: the embedding origin is derived from document.referrer and sent in
// X-Embedding-Origin for server-side allow-listing. Every tenant-supplied
// string (display_name, welcome_message, replies) is rendered as a plain text
// node — never as HTML.

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

function clientMessageId(): string {
  return crypto.randomUUID().replaceAll("-", "");
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

  const embeddingOriginRef =
    useRef<string | null>(null);
  const sessionTokenRef =
    useRef<string | null>(null);
  const pollRef =
    useRef<ReturnType<typeof setInterval> | null>(null);

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

  const connect = useCallback(async () => {
    setErrorMessage(null);
    setState("connecting");

    let token = sessionTokenRef.current;
    let loadedState: WidgetState | null = null;

    const storedToken = (() => {
      try {
        return window.sessionStorage.getItem(
          CHAT_SESSION_STORAGE_KEY,
        );
      } catch {
        return null;
      }
    })();

    if (!token && storedToken) {
      try {
        loadedState =
          await loadState(storedToken);
        token = storedToken;
      } catch {
        discardSession();
      }
    }

    if (!token) {
      try {
        const created =
          await createPublicChatSession(
            widgetKey,
            embeddingOriginRef.current,
          );
        setConfig((previous) => previous ?? created.config);
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
      } catch (error) {
        setErrorMessage(
          error instanceof PublicChatApiError
            ? error.message
            : "Unable to reach the support channel.",
        );
        setState("error");
        return;
      }
    }

    if (token) {
      setSessionToken(token);
      if (loadedState !== null) {
        setState(loadedState);
      } else {
        setState("ai_active");
      }
    }
  }, [
    discardSession,
    loadState,
    persistSession,
    widgetKey,
  ]);

  const open = useCallback(async () => {
    if (!config) {
      try {
        const resolved =
          await fetchPublicChatConfig(widgetKey);
        setConfig(resolved);
      } catch (error) {
        setErrorMessage(
          error instanceof PublicChatApiError
            ? error.message
            : "Unable to reach the support channel.",
        );
        setState("error");
        return;
      }
    }
    await connect();
  }, [config, connect, widgetKey]);

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
        if (result.handoff) {
          setState("human_requested");
        } else if (result.status === "human_assigned") {
          setState("human_assigned");
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
    let width: number;
    let height: number;
    if (isPanel) {
      width = Math.min(380, window.innerWidth - 24);
      height = Math.min(540, window.innerHeight - 24);
    } else {
      width = 56;
      height = 56;
    }
    window.parent.postMessage(
      {
        type: "cxops-embed:resize",
        width,
        height,
      },
      "*",
    );
  }, [state]);

  useEffect(() => {
    return () => {
      clearPoll();
    };
  }, [clearPoll]);

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
        <button
          type="button"
          onClick={open}
          aria-label="Open support chat"
          style={{
            width: "56px",
            height: "56px",
            borderRadius: "50%",
            border: "none",
            background: "#0f766e",
            color: "#ffffff",
            fontSize: "24px",
            cursor: "pointer",
            boxShadow:
              "0 4px 12px rgba(0, 0, 0, 0.25)",
          }}
        >
          <span aria-hidden="true">💬</span>
        </button>
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
        <button
          type="button"
          onClick={() => setState("launcher")}
          aria-label="Reopen support chat"
          style={{
            width: "56px",
            height: "56px",
            borderRadius: "50%",
            border: "none",
            background: "#0f766e",
            color: "#ffffff",
            fontSize: "24px",
            cursor: "pointer",
            boxShadow:
              "0 4px 12px rgba(0, 0, 0, 0.25)",
          }}
        >
          <span aria-hidden="true">💬</span>
        </button>
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
      style={{
        position: "fixed",
        right: "20px",
        bottom: "20px",
        zIndex: 9990,
        width: "min(380px, calc(100vw - 24px))",
        height: "min(540px, calc(100vh - 24px))",
        display: "flex",
        flexDirection: "column",
        borderRadius: "12px",
        border: "1px solid #e2e8f0",
        background: "#ffffff",
        boxShadow:
          "0 8px 24px rgba(0, 0, 0, 0.2)",
        overflow: "hidden",
        fontFamily:
          "var(--font-geist-sans, system-ui, sans-serif)",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          background: "#0f766e",
          color: "#ffffff",
        }}
      >
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
        <button
          type="button"
          onClick={closeChat}
          aria-label="Close chat"
          style={{
            border: "none",
            background: "transparent",
            color: "#ffffff",
            fontSize: "20px",
            cursor: "pointer",
            lineHeight: 1,
          }}
        >
          <span aria-hidden="true">✕</span>
        </button>
      </header>

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
          gap: "8px",
        }}
      >
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
                borderRadius: "12px",
                background: isInbound
                  ? "#f1f5f9"
                  : "#0f766e",
                color: isInbound
                  ? "#0f172a"
                  : "#ffffff",
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
              color: "#64748b",
              fontSize: "13px",
              fontStyle: "italic",
            }}
          >
            …sending
          </li>
        )}
      </ul>

      {state === "human_requested" && (
        <p
          style={{
            margin: 0,
            padding: "8px 16px",
            background: "#fef3c7",
            color: "#78350f",
            fontSize: "13px",
          }}
        >
          A support team member will review your request.
          Closing this window does not cancel it.
        </p>
      )}

      {state === "human_assigned" && (
        <p
          style={{
            margin: 0,
            padding: "8px 16px",
            background: "#eff6ff",
            color: "#1e40af",
            fontSize: "13px",
          }}
        >
          A support team member has joined this
          conversation and will respond here shortly.
        </p>
      )}

      {errorMessage && (
        <p
          role="alert"
          style={{
            margin: 0,
            padding: "8px 16px",
            background: "#fee2e2",
            color: "#7f1d1d",
            fontSize: "13px",
          }}
        >
          {errorMessage}
        </p>
      )}

      <footer
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          padding: "12px 16px",
          borderTop: "1px solid #e2e8f0",
          background: "#f8fafc",
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
            type="text"
            value={input}
            onChange={(event) =>
              setInput(event.target.value)
            }
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey
              ) {
                event.preventDefault();
                void sendMessage(input);
              }
            }}
            maxLength={config?.max_message_length ?? 4000}
            disabled={state !== "ai_active" || sending}
            placeholder="Type your message…"
            autoComplete="off"
            style={{
              flex: 1,
              padding: "8px 12px",
              borderRadius: "8px",
              border: "1px solid #cbd5e1",
              fontSize: "14px",
              minWidth: 0,
            }}
          />
          <button
            type="button"
            onClick={() => void sendMessage(input)}
            disabled={
              state !== "ai_active" ||
              sending ||
              !input.trim()
            }
            aria-label="Send message"
            style={{
              padding: "8px 14px",
              borderRadius: "8px",
              border: "none",
              background: "#0f766e",
              color: "#ffffff",
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
              border: "1px solid #cbd5e1",
              background: "#ffffff",
              color: "#0f172a",
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