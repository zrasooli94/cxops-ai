import type {
  PublicChatConfigResponse,
  PublicChatMessageRead,
  PublicChatMessageSendResponse,
  PublicChatSessionStatus,
} from "./types";

// An intentionally deterministic, local-only demonstration adapter.
// This is NOT an LLM, a real retrieval service, or a replacement for the backend.
// The existing production widget uses its normal fetch by default.
export const DEMO_WIDGET_KEY = "pk_demo_aster_cycle_studio";
export const DEMO_WIDGET_SESSION_KEY = "cxops.chat.demo.session.v1";
const DEMO_STORAGE_KEY = "cxops.chat.demo.data.v1";
const DEMO_TTL_MS = 60 * 60 * 1000;
const TOPICS = ["services", "hours", "prices", "returns", "warranty", "location"] as const;
export type DemoTopic = (typeof TOPICS)[number];
export type DemoLanguage = "en" | "fa";
export type DemoKnowledge = Record<DemoTopic, Record<DemoLanguage, string>>;

export interface DemoSettings {
  name: string;
  greeting: string;
  theme: "default" | "midnight" | "ocean" | "forest";
}

interface DemoSession {
  token: string;
  expiresAt: string;
  status: PublicChatSessionStatus;
  messages: PublicChatMessageRead[];
  lastTopic: DemoTopic | null;
  seen: Record<string, { text: string; response: PublicChatMessageSendResponse }>;
}

export interface DemoSnapshot {
  settings: DemoSettings;
  knowledge: DemoKnowledge;
  session: {
    status: PublicChatSessionStatus;
    messages: PublicChatMessageRead[];
    token: string;
  } | null;
}

export const DEFAULT_DEMO_SETTINGS: DemoSettings = {
  name: "Aster Cycle Studio",
  greeting: "Hi! Welcome to Aster Cycle Studio. Ask about our services, opening hours or policies. This is a fictional demo.",
  theme: "midnight",
};

// All details below are FICTIONAL. They must never be used as real customer facts.
export const DEFAULT_DEMO_KNOWLEDGE: DemoKnowledge = {
  services: {
    en: "We offer bicycle tune-ups, puncture repairs, safety inspections, and accessory sales.",
    fa: "خدمات ما شامل سرویس دوچرخه، تعمیر پنچری، بررسی ایمنی و فروش لوازم جانبی است.",
  },
  hours: {
    en: "Monday to Friday: 9:00 AM–6:00 PM. Saturday: 10:00 AM–4:00 PM. Sunday: closed.",
    fa: "دوشنبه تا جمعه: ۹ صبح تا ۶ عصر. شنبه: ۱۰ صبح تا ۴ عصر. یکشنبه تعطیل است.",
  },
  prices: {
    en: "A standard tune-up costs RM 80, and a puncture repair starts at RM 25. Other repairs need an inspection before a quote.",
    fa: "هزینه سرویس معمولی ۸۰ رینگیت و تعمیر پنچری از ۲۵ رینگیت شروع می‌شود. قیمت تعمیرات دیگر پس از بررسی مشخص می‌شود.",
  },
  returns: {
    en: "Unused accessories may be returned within 14 days with proof of purchase. Completed repair labor is not refundable.",
    fa: "لوازم جانبی استفاده‌نشده تا ۱۴ روز با رسید خرید قابل بازگشت‌اند. هزینه کار تعمیر تکمیل‌شده بازپرداخت نمی‌شود.",
  },
  warranty: {
    en: "Repair workmanship is covered for 90 days. New accidental damage and normal wear are excluded.",
    fa: "کیفیت انجام تعمیرات تا ۹۰ روز ضمانت دارد. آسیب جدید و فرسودگی معمول شامل ضمانت نیست.",
  },
  location: {
    en: "This is a fictional example business in Kuala Lumpur. No real shop address is provided.",
    fa: "این یک کسب‌وکار خیالی برای نمایش در کوالالامپور است و آدرس فروشگاه واقعی ندارد.",
  },
};

const TOPIC_LABEL: Record<DemoTopic, string> = {
  services: "Services", hours: "Opening hours", prices: "Pricing",
  returns: "Returns policy", warranty: "Warranty policy", location: "Location",
};

export interface DemoAnswer {
  kind: "answer" | "handoff";
  body: string;
  topic: DemoTopic | null;
}

export function demoAnswer(
  question: string,
  lastTopic: DemoTopic | null,
  knowledge: DemoKnowledge,
): DemoAnswer {
  const value = question.normalize("NFKC").toLowerCase().trim();
  const persian = /[\u0600-\u06ff]/.test(value);
  const lang: DemoLanguage = persian ? "fa" : "en";
  const handoff = (): DemoAnswer => ({
    kind: "handoff",
    body: persian
      ? "این اطلاعات در دانش آزمایشی ما موجود نیست. درخواست کمک انسانی به‌صورت نمایشی ثبت شد؛ هیچ کارمند واقعی مطلع نشده است."
      : "I don't have that information in this demo knowledge base. I've put this chat in the simulated human-support queue; no real staff member has been notified.",
    topic: null,
  });

  if (/(^|\s)(human|person|agent|representative|staff)(\s|$)|speak to|talk to someone|انسان|اپراتور|کارمند|پشتیبان واقعی/.test(value)) {
    return { kind: "handoff", body: persian
      ? "درخواست پشتیبانی انسانی به‌صورت آزمایشی ثبت شد. هیچ کارمند واقعی مطلع نشده است."
      : "Human assistance requested in this demo. No real support team has been notified.", topic: null };
  }
  // Transactional requests need a person: this mock never books, orders,
  // refunds, promises stock, or executes a real business action.
  if (/\b(book|reserve|place an order|make an appointment|cancel my|refund me|pay now|order status|track my order)\b|رزرو|ثبت سفارش|پرداخت|کنسل|لغو نوبت/.test(value)) {
    return handoff();
  }
  if (/\b(free|complimentary)\b|رایگان|مجانی/.test(value)) {
    return handoff();
  }
  if (/^(hi|hello|hey|سلام|درود)[!.؟\s]*$/.test(value)) {
    return { kind: "answer", body: persian
      ? "سلام! درباره خدمات، ساعات کاری، قیمت‌ها یا قوانین فروشگاه چه سؤالی دارید؟"
      : "Hello! You can ask about our services, hours, pricing or policies.", topic: null };
  }

  let topic: DemoTopic | null = null;
  if (/warrant|guarantee|ضمانت|گارانتی/.test(value)) topic = "warranty";
  else if (/refund|return|exchange|مرجوع|بازگشت|پس دادن|بازپرداخت/.test(value)) topic = "returns";
  else if (/price|cost|how much|fee|quote|قیمت|هزینه|چند|ریال|رینگیت/.test(value)) topic = "prices";
  else if (/hour|open|clos(e|ing)|schedule|time|weekend|sunday|saturday|ساعت|ساعات|زمان|باز|بسته|آخر هفته|شنبه|یکشنبه/.test(value)) topic = "hours";
  else if (/service|repair|fix|puncture|tune.?up|bike|bicycle|offer|products?|accessor|sell|محصول|خدمات|تعمیر|دوچرخه|پنچری/.test(value)) topic = "services";
  else if (/location|address|where are|find you|آدرس|کجا|موقعیت/.test(value)) topic = "location";
  else if (/what about (it|that)|how about (it|that)|در مورد آن|در موردش/.test(value) && lastTopic) topic = lastTopic;

  if (!topic) return handoff();
  const answer = knowledge[topic]?.[lang]?.trim();
  if (!answer) return handoff();
  return {
    kind: "answer",
    body: answer + (persian
      ? "\n\n[منبع: دانش آزمایشی — " + TOPIC_LABEL[topic] + "]"
      : "\n\n[Source: demo knowledge — " + TOPIC_LABEL[topic] + "]"),
    topic,
  };
}

function json(payload: unknown, status = 200): Response {
  return Response.json(payload, { status, headers: { "cache-control": "no-store" } });
}

function cloneKnowledge(knowledge: DemoKnowledge): DemoKnowledge {
  return Object.fromEntries(TOPICS.map((topic) => [topic, { ...knowledge[topic] }])) as DemoKnowledge;
}

export class DemoChatTransport {
  private settings: DemoSettings = { ...DEFAULT_DEMO_SETTINGS };
  private knowledge: DemoKnowledge = cloneKnowledge(DEFAULT_DEMO_KNOWLEDGE);
  private session: DemoSession | null = null;
  private storage: Storage | null = null;
  private listeners = new Set<() => void>();
  private nextId = 1;

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  snapshot(): DemoSnapshot {
    return {
      settings: { ...this.settings },
      knowledge: cloneKnowledge(this.knowledge),
      session: this.session ? {
        token: this.session.token,
        status: this.session.status,
        messages: this.session.messages.map((message) => ({ ...message })),
      } : null,
    };
  }

  attachStorage(storage: Storage): void {
    this.storage = storage;
    try {
      const raw = storage.getItem(DEMO_STORAGE_KEY);
      if (raw) {
        const data: unknown = JSON.parse(raw);
        if (data && typeof data === "object" && "version" in data && data.version === 1) {
          const saved = data as {
            settings?: DemoSettings; knowledge?: DemoKnowledge;
            session?: DemoSession | null; nextId?: number;
          };
          if (saved.settings &&
              typeof saved.settings.name === "string" &&
              typeof saved.settings.greeting === "string" &&
              ["default", "midnight", "ocean", "forest"].includes(saved.settings.theme)) {
            this.settings = {
              name: saved.settings.name.slice(0, 80),
              greeting: saved.settings.greeting.slice(0, 350),
              theme: saved.settings.theme,
            };
          }
          if (saved.knowledge && TOPICS.every((topic) =>
            typeof saved.knowledge?.[topic]?.en === "string" &&
            typeof saved.knowledge?.[topic]?.fa === "string")) {
            this.knowledge = Object.fromEntries(TOPICS.map((topic) =>
              [topic, {
                en: saved.knowledge![topic].en.slice(0, 1000),
                fa: saved.knowledge![topic].fa.slice(0, 1000),
              }])) as DemoKnowledge;
          }
          if (saved.session &&
              typeof saved.session.token === "string" &&
              saved.session.token.startsWith("demo_") &&
              Date.parse(saved.session.expiresAt) > Date.now() &&
              Array.isArray(saved.session.messages) &&
              saved.session.messages.length <= 50 &&
              saved.session.messages.every((message) =>
                Number.isSafeInteger(message.id) &&
                message.id > 0 &&
                (message.direction === "inbound" || message.direction === "outbound") &&
                typeof message.body === "string" && message.body.length <= 2000) &&
              ["ai_active", "human_requested", "human_assigned", "closed"].includes(saved.session.status)) {
            this.session = {
              ...saved.session,
              seen: {},
              lastTopic: TOPICS.includes(saved.session.lastTopic as DemoTopic)
                ? saved.session.lastTopic : null,
            };
            this.nextId = Math.max(1, ...saved.session.messages.map((m) => m.id + 1));
          }
        }
      }
    } catch {
      // Storage is optional; bad or unavailable browser storage does not block a demo.
    }
    this.notify();
  }

  configure(patch: Partial<DemoSettings>): void {
    if (patch.name !== undefined) this.settings.name = patch.name.slice(0, 80);
    if (patch.greeting !== undefined) this.settings.greeting = patch.greeting.slice(0, 350);
    if (patch.theme !== undefined && ["default", "midnight", "ocean", "forest"].includes(patch.theme))
      this.settings.theme = patch.theme;
    this.notify();
  }

  setKnowledge(topic: DemoTopic, lang: DemoLanguage, value: string): void {
    this.knowledge[topic][lang] = value.slice(0, 1000);
    this.notify();
  }

  resetConversation(): void {
    this.session = null;
    this.notify();
  }

  claimHuman(): boolean {
    if (!this.session || this.session.status !== "human_requested") return false;
    this.session.status = "human_assigned";
    this.notify();
    return true;
  }

  staffReply(text: string): boolean {
    if (!this.session || this.session.status !== "human_assigned" || !text.trim()) return false;
    this.addMessage(this.session, "outbound", text.trim().slice(0, 600));
    this.notify();
    return true;
  }

  private addMessage(session: DemoSession, direction: "inbound" | "outbound", body: string): number {
    const id = this.nextId++;
    session.messages.push({ id, direction, body, sent_at: new Date().toISOString() });
    session.messages = session.messages.slice(-50);
    return id;
  }

  private notify(): void {
    if (this.storage) {
      try {
        this.storage.setItem(DEMO_STORAGE_KEY, JSON.stringify({
          version: 1, settings: this.settings, knowledge: this.knowledge,
          session: this.session, nextId: this.nextId,
        }));
      } catch { /* Storage can be disabled/quota-limited. */ }
    }
    for (const listener of this.listeners) listener();
  }

  private requireSession(init?: RequestInit): DemoSession | null {
    const token = new Headers(init?.headers).get("authorization")?.replace(/^Bearer /i, "");
    if (!token || !this.session || token !== this.session.token ||
        Date.parse(this.session.expiresAt) <= Date.now()) return null;
    return this.session;
  }

  /** Purely in-browser fake of the existing public-chat BFF contract. */
  readonly fetcher: typeof fetch = async (input, init) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(raw, "http://localhost");
    const path = url.pathname;
    const method = init?.method?.toUpperCase() ?? "GET";
    // Never delegate unrecognized paths to a real backend or external network.
    if (!path.startsWith("/api/public/chat/")) return json({ detail: "Demo transport refuses non-chat requests." }, 404);

    const config: PublicChatConfigResponse = {
      display_name: this.settings.name,
      welcome_message: this.settings.greeting,
      enabled: true,
      theme_token: this.settings.theme,
      max_message_length: 600,
    };
    if (path === "/api/public/chat/config" && method === "GET") {
      return url.searchParams.get("key") === DEMO_WIDGET_KEY
        ? json(config) : json({ detail: "Unknown demo widget." }, 404);
    }
    if (path === "/api/public/chat/sessions" && method === "POST") {
      let body: { public_widget_key?: string };
      try { body = JSON.parse(String(init?.body ?? "")); }
      catch { return json({ detail: "Invalid JSON." }, 400); }
      if (!body || typeof body !== "object" || body.public_widget_key !== DEMO_WIDGET_KEY)
        return json({ detail: "Unknown demo widget." }, 404);
      const token = "demo_" + crypto.randomUUID();
      this.session = {
        token, status: "ai_active", messages: [], lastTopic: null, seen: {},
        expiresAt: new Date(Date.now() + DEMO_TTL_MS).toISOString(),
      };
      this.notify();
      return json({ config, session: { token, status: "ai_active", expires_at: this.session.expiresAt } });
    }

    const session = this.requireSession(init);
    if (!session) return json({ detail: "Demo session not found or expired." }, 401);
    if (path === "/api/public/chat/sessions/state" && method === "GET") {
      return json({ status: session.status, messages: session.messages, expires_at: session.expiresAt });
    }
    if (path === "/api/public/chat/sessions/updates" && method === "GET") {
      const after = Number(url.searchParams.get("after_id") ?? 0);
      const cursor = session.messages.at(-1)?.id ?? 0;
      return json({
        status: session.status,
        messages: session.messages.filter((message) => message.id > (Number.isSafeInteger(after) ? after : 0)),
        turns: [], cursor, poll_after_ms: 1000,
      });
    }
    if (path === "/api/public/chat/messages" && method === "POST") {
      if (session.status === "closed") return json({ detail: "Conversation is closed." }, 409);
      let body: { client_message_id?: string; text?: string };
      try { body = JSON.parse(String(init?.body ?? "")); }
      catch { return json({ detail: "Invalid JSON." }, 400); }
      const text = typeof body?.text === "string" ? body.text.trim() : "";
      const key = typeof body?.client_message_id === "string" ? body.client_message_id : "";
      if (!/^[a-f0-9]{32}$/i.test(key) || !text || text.length > config.max_message_length)
        return json({ detail: "Invalid message or message identifier." }, 422);
      const previous = session.seen[key];
      if (previous) return previous.text === text
        ? json(previous.response) : json({ detail: "Message ID already used." }, 409);
      const id = this.addMessage(session, "inbound", text);
      let handoff = session.status !== "ai_active";
      if (!handoff) {
        const answer = demoAnswer(text, session.lastTopic, this.knowledge);
        if (answer.kind === "handoff") {
          session.status = "human_requested";
          handoff = true;
        } else if (answer.topic) {
          session.lastTopic = answer.topic;
        }
        this.addMessage(session, "outbound", answer.body);
      }
      const response: PublicChatMessageSendResponse = {
        message_id: id, reply: null, status: session.status, handoff,
        processing_state: "accepted",
      };
      session.seen[key] = { text, response };
      if (Object.keys(session.seen).length > 60) {
        delete session.seen[Object.keys(session.seen)[0]];
      }
      this.notify();
      return json(response);
    }
    if (path === "/api/public/chat/sessions/human" && method === "POST") {
      if (session.status === "closed") return json({ detail: "Conversation closed." }, 409);
      if (session.status === "ai_active") session.status = "human_requested";
      const reply = "Human support requested in this simulation. No real person was contacted.";
      this.addMessage(session, "outbound", reply);
      this.notify();
      return json({ status: session.status, reply });
    }
    if (path === "/api/public/chat/sessions/close" && method === "POST") {
      session.status = "closed";
      this.notify();
      return json({ status: "closed" });
    }
    return json({ detail: "Unknown demo endpoint." }, 404);
  };
}
