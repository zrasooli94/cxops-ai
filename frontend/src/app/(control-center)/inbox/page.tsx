"use client";

import {
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Inbox as InboxIcon,
  LoaderCircle,
  Mail,
  MessageSquare,
  RefreshCw,
  Search,
  TriangleAlert,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

type DeliveryStatus =
  | "queued"
  | "sending"
  | "retrying"
  | "sent"
  | "failed"
  | null;

type ConversationMessage = {
  id: number;
  conversation_id: number;
  provider: string;
  direction: string;
  visibility: string;
  body: string;
  sent_at: string | null;
  created_at: string;
  delivery_status: DeliveryStatus;
  delivered_at: string | null;
  can_retry: boolean;
};

type ConversationListItem = {
  id: number;
  provider: string;
  channel: string;
  external_thread_id: string | null;
  subject: string | null;
  status: string;
  customer_id: number | null;
  ticket_id: number | null;
  latest_message_at: string | null;
  needs_response: boolean;
  reply_mode: "zendesk" | "local_only" | "unsupported";
  latest_message: {
    body: string;
    direction: string;
    visibility: string;
    sent_at: string | null;
  } | null;
};

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "violet";

function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
}) {
  const styles: Record<BadgeVariant, string> = {
    default:
      "border-slate-200 bg-slate-50 text-slate-600",
    success:
      "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning:
      "border-amber-200 bg-amber-50 text-amber-700",
    danger:
      "border-rose-200 bg-rose-50 text-rose-700",
    info:
      "border-blue-200 bg-blue-50 text-blue-700",
    violet:
      "border-violet-200 bg-violet-50 text-violet-700",
  };

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

function statusVariant(
  status: string,
): BadgeVariant {
  switch (status.toLowerCase()) {
    case "closed":
    case "resolved":
      return "success";
    case "open":
    case "pending":
    case "new":
      return "warning";
    default:
      return "default";
  }
}

function replyModeLabel(
  mode: ConversationListItem["reply_mode"],
): string {
  switch (mode) {
    case "zendesk":
      return "Zendesk reply";
    case "local_only":
      return "Local reply";
    default:
      return "Unsupported";
  }
}

function deliveryStatusLabel(
  status: DeliveryStatus,
): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "sending":
      return "Sending";
    case "retrying":
      return "Retrying";
    case "sent":
      return "Sent";
    case "failed":
      return "Failed";
    default:
      return "";
  }
}

function deliveryStatusVariant(
  status: DeliveryStatus,
): BadgeVariant {
  switch (status) {
    case "queued":
    case "sending":
      return "info";
    case "retrying":
      return "warning";
    case "sent":
      return "success";
    case "failed":
      return "danger";
    default:
      return "default";
  }
}

function timestampValue(
  sent_at: string | null,
  created_at: string,
): string {
  return sent_at ?? created_at;
}

function formatTimestamp(value: string) {
  return new Date(value).toLocaleString();
}

export default function InboxPage() {
  const [conversations, setConversations] =
    useState<ConversationListItem[]>([]);

  const [selectedId, setSelectedId] =
    useState<number | null>(null);

  const [messages, setMessages] =
    useState<ConversationMessage[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] = useState("");

  const [threadLoading, setThreadLoading] =
    useState(false);

  const [threadError, setThreadError] =
    useState("");

  const [search, setSearch] = useState("");

  const [replyBody, setReplyBody] = useState("");

  const [replySubmitting, setReplySubmitting] =
    useState(false);

  const [replyError, setReplyError] = useState("");

  const [replySuccess, setReplySuccess] = useState("");

  const [retryingMessageId, setRetryingMessageId] =
    useState<number | null>(null);

  const loadConversations =
    useCallback(async (refreshSelected = false) => {
      setLoading(true);
      setError("");

      try {
        const response = await fetch(
          "/api/backend/conversations",
          { cache: "no-store" },
        );

        if (!response.ok) {
          throw new Error(
            `Inbox API returned ${response.status}`,
          );
        }

        const data = await response.json();

        const rows: ConversationListItem[] =
          Array.isArray(data?.items)
            ? data.items
            : [];

        setConversations(rows);

        if (rows.length > 0) {
          setSelectedId((current) => {
            if (
              refreshSelected &&
              current !== null &&
              rows.some(
                (conversation) =>
                  conversation.id ===
                  current,
              )
            ) {
              return current;
            }

            if (
              current !== null &&
              rows.some(
                (conversation) =>
                  conversation.id ===
                  current,
              )
            ) {
              return current;
            }

            return rows[0].id;
          });
        } else {
          setSelectedId(null);
          setMessages([]);
        }
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load conversations.",
        );
      } finally {
        setLoading(false);
      }
    }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadConversations();
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [loadConversations]);

  const loadThread =
    useCallback(async (conversationId: number) => {
      setThreadLoading(true);
      setThreadError("");

      try {
        const response = await fetch(
          `/api/backend/conversations/${conversationId}/messages`,
          { cache: "no-store" },
        );

        if (!response.ok) {
          throw new Error(
            `Thread API returned ${response.status}`,
          );
        }

        const data = (await response.json()) as
          | ConversationMessage[]
          | null;

        setMessages(
          Array.isArray(data) ? data : [],
        );
      } catch (err) {
        setMessages([]);
        setThreadError(
          err instanceof Error
            ? err.message
            : "Failed to load the conversation thread.",
        );
      } finally {
        setThreadLoading(false);
      }
    }, []);

  useEffect(() => {
    if (selectedId === null) {
      return;
    }

    const timer = window.setTimeout(() => {
      void loadThread(selectedId);
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [selectedId, loadThread]);

  const submitReply = useCallback(
    async (conversationId: number) => {
      const body = replyBody.trim();

      if (!body) {
        setReplyError("Reply body cannot be empty.");
        return;
      }

      setReplySubmitting(true);
      setReplyError("");
      setReplySuccess("");

      try {
        const response = await fetch(
          `/api/backend/conversations/${conversationId}/replies`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              body,
              client_request_id: crypto.randomUUID(),
            }),
          },
        );

        if (response.status === 409) {
          setReplyBody("");
          setReplySuccess(
            "This reply was already submitted.",
          );
          void loadThread(conversationId);
          return;
        }

        if (!response.ok) {
          const payload = (await response
            .json()
            .catch(() => ({}))) as {
            detail?: string;
          };
          throw new Error(
            payload.detail ??
              `Reply API returned ${response.status}`,
          );
        }

        setReplyBody("");
        setReplySuccess("Reply queued for delivery.");
        void loadThread(conversationId);
      } catch (err) {
        setReplyError(
          err instanceof Error
            ? err.message
            : "Failed to send reply.",
        );
      } finally {
        setReplySubmitting(false);
      }
    },
    [replyBody, loadThread],
  );

  const retryMessage = useCallback(
    async (
      conversationId: number,
      messageId: number,
    ) => {
      setRetryingMessageId(messageId);
      setReplyError("");
      setReplySuccess("");

      try {
        const response = await fetch(
          `/api/backend/conversations/${conversationId}/messages/${messageId}/retry`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
          },
        );

        if (!response.ok) {
          const payload = (await response
            .json()
            .catch(() => ({}))) as {
            detail?: string;
          };
          throw new Error(
            payload.detail ??
              `Retry API returned ${response.status}`,
          );
        }

        setReplySuccess("Retry queued for delivery.");
        void loadThread(conversationId);
      } catch (err) {
        setReplyError(
          err instanceof Error
            ? err.message
            : "Failed to retry message.",
        );
      } finally {
        setRetryingMessageId(null);
      }
    },
    [loadThread],
  );

  useEffect(() => {
    if (selectedId === null) {
      return;
    }

    const interval = window.setInterval(() => {
      if (
        messages.some(
          (message) =>
            message.direction === "outbound" &&
            message.delivery_status &&
            message.delivery_status !== "sent" &&
            message.delivery_status !== "failed",
        )
      ) {
        void loadThread(selectedId);
      }
    }, 3000);

    return () => {
      window.clearInterval(interval);
    };
  }, [selectedId, messages, loadThread]);

  const selectedConversation =
    useMemo(
      () =>
        selectedId === null
          ? null
          : (conversations.find(
              (conversation) =>
                conversation.id ===
                selectedId,
            ) ?? null),
      [conversations, selectedId],
    );

  const filteredConversations =
    useMemo(() => {
      const value = search
        .trim()
        .toLowerCase();

      if (!value) {
        return conversations;
      }

      return conversations.filter(
        (conversation) =>
          [
            conversation.subject,
            conversation.latest_message
              ?.body,
            conversation.provider,
            conversation.channel,
            conversation.status,
          ]
            .filter(Boolean)
            .some((field) =>
              String(field)
                .toLowerCase()
                .includes(value),
            ),
      );
    }, [conversations, search]);

  const needsResponseCount =
    useMemo(
      () =>
        conversations.filter(
          (conversation) =>
            conversation.needs_response,
        ).length,
      [conversations],
    );

  const openCount = useMemo(
    () =>
      conversations.filter((conversation) =>
        !["closed", "resolved"].includes(
          conversation.status.toLowerCase(),
        ),
      ).length,
    [conversations],
  );

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center justify-between px-6 lg:px-10">
            <div>
              <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
                Inbox
              </p>

              <p className="hidden text-[11px] text-slate-400 sm:block">
                Unified conversation workspace
              </p>
            </div>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() =>
                  void loadConversations(true)
                }
                disabled={loading}
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-violet-300 hover:text-violet-600 disabled:opacity-50"
                aria-label="Refresh conversations"
              >
                <RefreshCw
                  className={`h-4 w-4 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          <section className="mb-8">
            <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-end">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#7160ff]">
                  Customer Operations
                </p>

                <h1 className="mt-4 text-4xl font-light tracking-[-0.055em] text-slate-950 md:text-5xl">
                  Unified inbox
                </h1>

                <p className="mt-3 max-w-xl text-sm leading-7 text-slate-500">
                  Every customer conversation from
                  tickets and connected channels in a
                  single thread view.
                </p>
              </div>

              <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white/70 px-4 py-2 text-[11px] text-slate-500 shadow-sm">
                <CircleDot className="h-3.5 w-3.5 text-emerald-500" />
                Live conversation data
              </div>
            </div>
          </section>

          {error && (
            <div className="mb-7 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
              <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
              {error}
            </div>
          )}

          <section className="mb-7 grid gap-4 md:grid-cols-3">
            <div className="app-panel relative overflow-hidden rounded-[20px] bg-gradient-to-br from-violet-500/10 to-indigo-500/[0.025] p-6">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                Total conversations
              </p>

              <p className="editorial-number mt-4 text-4xl font-medium tracking-[-0.045em] text-slate-950">
                {conversations.length}
              </p>

              <p className="mt-3 text-xs leading-5 text-slate-500">
                All conversations currently stored
                in CXOps.
              </p>
            </div>

            <div className="app-panel relative overflow-hidden rounded-[20px] bg-gradient-to-br from-amber-400/12 to-orange-400/[0.025] p-6">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                Open
              </p>

              <p className="editorial-number mt-4 text-4xl font-medium tracking-[-0.045em] text-slate-950">
                {openCount}
              </p>

              <p className="mt-3 text-xs leading-5 text-slate-500">
                Conversations still awaiting
                resolution.
              </p>
            </div>

            <div className="app-panel relative overflow-hidden rounded-[20px] bg-gradient-to-br from-rose-400/12 to-red-400/[0.025] p-6">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                Needs response
              </p>

              <p className="editorial-number mt-4 text-4xl font-medium tracking-[-0.045em] text-slate-950">
                {needsResponseCount}
              </p>

              <p className="mt-3 text-xs leading-5 text-slate-500">
                Inbound threads with no reply yet.
              </p>
            </div>
          </section>

          <div className="grid gap-6 xl:grid-cols-[390px_minmax(0,1fr)]">
            <section className="app-panel self-start overflow-hidden rounded-[22px] xl:sticky xl:top-[96px]">
              <div className="border-b border-slate-200/70 p-4">
                <div className="relative">
                  <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />

                  <input
                    value={search}
                    onChange={(event) =>
                      setSearch(
                        event.target.value,
                      )
                    }
                    placeholder="Search conversations..."
                    className="w-full rounded-xl border border-slate-200 bg-[#fbfcff] py-3 pl-10 pr-4 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50"
                  />
                </div>

                <div className="mt-3 flex justify-between px-1 text-[10px] uppercase tracking-[0.13em] text-slate-400">
                  <span>
                    {filteredConversations.length}{" "}
                    results
                  </span>

                  <span>Updated live</span>
                </div>
              </div>

              <div
                data-lenis-prevent
                className="max-h-[760px] overflow-y-auto overscroll-contain"
              >
                {loading &&
                conversations.length === 0 ? (
                  <div className="flex h-52 items-center justify-center">
                    <LoaderCircle className="h-6 w-6 animate-spin text-violet-500" />
                  </div>
                ) : filteredConversations.length ===
                  0 ? (
                  <div className="p-10 text-center">
                    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-50">
                      <MessageSquare className="h-5 w-5 text-slate-400" />
                    </div>

                    <p className="mt-4 text-sm font-medium text-slate-700">
                      No conversations found
                    </p>

                    <p className="mt-1 text-xs text-slate-400">
                      Try another search term.
                    </p>
                  </div>
                ) : (
                  filteredConversations.map(
                    (conversation) => {
                      const selected =
                        selectedId ===
                        conversation.id;

                      return (
                        <button
                          key={conversation.id}
                          type="button"
                          onClick={() => {
                            setSelectedId(
                              conversation.id,
                            );
                            setThreadError("");
                            setReplyBody("");
                            setReplyError("");
                            setReplySuccess("");
                          }}
                          className={`group relative w-full border-b border-slate-200/60 p-4 text-left transition last:border-b-0 ${
                            selected
                              ? "bg-gradient-to-r from-violet-50/90 via-blue-50/50 to-white"
                              : "bg-white/30 hover:bg-slate-50/80"
                          }`}
                        >
                          {selected && (
                            <span className="absolute bottom-3 left-0 top-3 w-[3px] rounded-r-full bg-gradient-to-b from-violet-500 to-blue-500" />
                          )}

                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400">
                                  #{conversation.id}
                                </span>

                                <span className="text-[10px] capitalize text-blue-500">
                                  {conversation.provider}
                                  {conversation.channel
                                    ? ` · ${conversation.channel}`
                                    : ""}
                                </span>
                              </div>

                              <p
                                className={`mt-2 truncate text-sm font-medium ${
                                  selected
                                    ? "text-slate-950"
                                    : "text-slate-800"
                                }`}
                              >
                                {conversation.subject ??
                                  "Untitled conversation"}
                              </p>

                              <p className="mt-1.5 truncate text-xs text-slate-400">
                                {conversation.latest_message
                                  ?.body ?? "No messages yet"}
                              </p>

                              <div className="mt-3 flex flex-wrap items-center gap-2">
                                <Badge
                                  variant={statusVariant(
                                    conversation.status,
                                  )}
                                >
                                  {
                                    conversation.status
                                  }
                                </Badge>

                                {conversation.needs_response && (
                                  <Badge variant="violet">
                                    Needs response
                                  </Badge>
                                )}

                                <Badge variant="info">
                                  {replyModeLabel(
                                    conversation.reply_mode,
                                  )}
                                </Badge>
                              </div>
                            </div>

                            <ChevronRight
                              className={`mt-1 h-4 w-4 shrink-0 transition ${
                                selected
                                  ? "translate-x-0.5 text-violet-500"
                                  : "text-slate-300 group-hover:translate-x-0.5 group-hover:text-slate-500"
                              }`}
                            />
                          </div>
                        </button>
                      );
                    },
                  )
                )}
              </div>
            </section>

            <section className="min-w-0">
              {!selectedConversation ? (
                <div className="app-panel flex min-h-[540px] items-center justify-center rounded-[22px]">
                  <div className="text-center">
                    <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-50 to-blue-50">
                      <InboxIcon className="h-6 w-6 text-violet-500" />
                    </div>

                    <p className="mt-4 font-medium text-slate-800">
                      Select a conversation
                    </p>

                    <p className="mt-1 text-sm text-slate-400">
                      The message thread will appear
                      here.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="app-panel overflow-hidden rounded-[22px]">
                    <div className="relative overflow-hidden p-6 md:p-8">
                      <div className="pointer-events-none absolute -right-28 -top-36 h-80 w-80 rounded-full bg-violet-300/10 blur-3xl" />

                      <div className="relative">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                            Conversation #
                            {
                              selectedConversation.id
                            }
                          </span>

                          <Badge
                            variant={statusVariant(
                              selectedConversation.status,
                            )}
                          >
                            {
                              selectedConversation.status
                            }
                          </Badge>

                          {selectedConversation.needs_response && (
                            <Badge variant="violet">
                              Needs response
                            </Badge>
                          )}

                          <Badge variant="info">
                            {replyModeLabel(
                              selectedConversation.reply_mode,
                            )}
                          </Badge>
                        </div>

                        <h2 className="mt-5 max-w-3xl text-2xl font-medium tracking-[-0.035em] text-slate-950 md:text-3xl">
                          {selectedConversation.subject ??
                            "Untitled conversation"}
                        </h2>

                        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-400">
                          <span className="inline-flex items-center gap-1.5 capitalize">
                            <Mail className="h-3.5 w-3.5" />
                            {selectedConversation.provider}
                            {selectedConversation.channel
                              ? ` · ${selectedConversation.channel}`
                              : ""}
                          </span>

                          {selectedConversation.ticket_id !==
                            null && (
                            <span>
                              Linked ticket #
                              {
                                selectedConversation.ticket_id
                              }
                            </span>
                          )}

                          {selectedConversation.latest_message_at && (
                            <span className="inline-flex items-center gap-1.5">
                              <CircleDot className="h-3 w-3" />
                              {formatTimestamp(
                                selectedConversation.latest_message_at,
                              )}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="app-panel overflow-hidden rounded-[22px]">
                    <div className="border-b border-slate-200/70 p-5 md:p-6">
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-500">
                          <MessageSquare className="h-5 w-5" />
                        </div>

                        <div>
                          <h3 className="font-medium text-slate-900">
                            Thread
                          </h3>

                          <p className="text-xs text-slate-400">
                            Full message history
                          </p>
                        </div>
                      </div>
                    </div>

                    {threadError && (
                      <div className="m-5 flex items-start gap-3 rounded-[18px] border border-red-200 bg-red-50/80 p-4 text-sm text-red-700">
                        <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
                        {threadError}
                      </div>
                    )}

                    <div className="p-5 md:p-6">
                      {threadLoading &&
                      messages.length === 0 ? (
                        <div className="flex h-40 items-center justify-center">
                          <LoaderCircle className="h-6 w-6 animate-spin text-violet-500" />
                        </div>
                      ) : messages.length === 0 ? (
                        <div className="py-10 text-center">
                          <p className="text-sm font-medium text-slate-700">
                            No messages yet
                          </p>

                          <p className="mt-1 text-xs text-slate-400">
                            This conversation has not
                            received any messages.
                          </p>
                        </div>
                      ) : (
                        <div className="space-y-5">
                          {messages.map((message) => {
                            const inbound =
                              message.direction ===
                              "inbound";

                            const internal =
                              message.visibility ===
                              "internal";

                            return (
                              <div
                                key={message.id}
                                className={`flex ${
                                  internal
                                    ? "justify-center"
                                    : inbound
                                      ? "justify-start"
                                      : "justify-end"
                                }`}
                              >
                                <div
                                  className={`max-w-[85%] rounded-2xl border p-4 md:max-w-[75%] ${
                                    internal
                                      ? "border-amber-200/70 bg-amber-50/60"
                                      : inbound
                                        ? "border-slate-200/80 bg-[#fbfcff]"
                                        : "border-violet-100 bg-gradient-to-br from-violet-50/80 to-blue-50/60"
                                  }`}
                                >
                                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                                    <span
                                      className={`text-[10px] font-semibold uppercase tracking-[0.13em] ${
                                        internal
                                          ? "text-amber-600"
                                          : inbound
                                            ? "text-slate-500"
                                            : "text-violet-600"
                                      }`}
                                    >
                                      {internal
                                        ? "Internal note"
                                        : inbound
                                          ? "Customer"
                                          : "Agent"}
                                    </span>

                                    <span className="text-[10px] capitalize text-slate-400">
                                      {message.provider}
                                    </span>

                                    <span className="text-[10px] text-slate-400">
                                      {formatTimestamp(
                                        timestampValue(
                                          message.sent_at,
                                          message.created_at,
                                        ),
                                      )}
                                    </span>

                                    {!internal &&
                                      !inbound &&
                                      message.delivery_status && (
                                        <Badge
                                          variant={deliveryStatusVariant(
                                            message.delivery_status,
                                          )}
                                        >
                                          {deliveryStatusLabel(
                                            message.delivery_status,
                                          )}
                                        </Badge>
                                      )}
                                  </div>

                                  <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-600">
                                    {message.body}
                                  </p>

                                  {!internal &&
                                    !inbound &&
                                    message.delivery_status ===
                                      "failed" &&
                                    message.can_retry &&
                                    selectedConversation && (
                                      <button
                                        type="button"
                                        onClick={() =>
                                          void retryMessage(
                                            selectedConversation.id,
                                            message.id,
                                          )
                                        }
                                        disabled={
                                          retryingMessageId ===
                                          message.id
                                        }
                                        className="mt-3 flex items-center gap-1.5 text-[11px] font-medium text-rose-600 hover:text-rose-700 disabled:opacity-50"
                                      >
                                        <RefreshCw
                                          className={`h-3 w-3 ${
                                            retryingMessageId ===
                                            message.id
                                              ? "animate-spin"
                                              : ""
                                          }`}
                                        />
                                        Retry delivery
                                      </button>
                                    )}
                                </div>
                              </div>
                            );
                          })}

                          {messages.length > 0 && (
                            <div className="flex items-center justify-center gap-2 pt-2 text-[10px] text-slate-400">
                              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                              End of thread
                            </div>
                          )}
                        </div>
                      )}

                      {selectedConversation &&
                        selectedConversation.reply_mode !==
                          "unsupported" && (
                          <div className="mt-6 border-t border-slate-200/70 pt-5">
                            {replyError && (
                              <div className="mb-3 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50/80 p-3 text-xs text-rose-700">
                                <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                                {replyError}
                              </div>
                            )}

                            {replySuccess && (
                              <div className="mb-3 flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50/80 p-3 text-xs text-emerald-700">
                                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                                {replySuccess}
                              </div>
                            )}

                            <div className="relative">
                              <textarea
                                value={replyBody}
                                onChange={(event) => {
                                  setReplyBody(
                                    event.target.value,
                                  );
                                  if (replyError) {
                                    setReplyError("");
                                  }
                                }}
                                onKeyDown={(event) => {
                                  if (
                                    event.key ===
                                      "Enter" &&
                                    (event.metaKey ||
                                      event.ctrlKey)
                                  ) {
                                    event.preventDefault();
                                    void submitReply(
                                      selectedConversation.id,
                                    );
                                  }
                                }}
                                placeholder="Write a reply..."
                                rows={4}
                                maxLength={10000}
                                disabled={replySubmitting}
                                className="w-full resize-none rounded-2xl border border-slate-200 bg-[#fbfcff] p-4 pr-14 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/50 disabled:opacity-60"
                              />

                              <div className="absolute bottom-3 right-3 text-[10px] text-slate-400">
                                {replyBody.length}/10000
                              </div>
                            </div>

                            <div className="mt-3 flex items-center justify-end gap-3">
                              <span className="text-[11px] text-slate-400">
                                Cmd/Ctrl + Enter to send
                              </span>

                              <button
                                type="button"
                                onClick={() =>
                                  void submitReply(
                                    selectedConversation.id,
                                  )
                                }
                                disabled={
                                  replySubmitting ||
                                  replyBody.trim().length === 0
                                }
                                className="inline-flex h-10 items-center gap-2 rounded-xl bg-violet-600 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
                              >
                                {replySubmitting ? (
                                  <>
                                    <LoaderCircle className="h-4 w-4 animate-spin" />
                                    Sending
                                  </>
                                ) : (
                                  <>
                                    <MessageSquare className="h-4 w-4" />
                                    Send reply
                                  </>
                                )}
                              </button>
                            </div>
                          </div>
                        )}
                    </div>
                  </div>
                </div>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}