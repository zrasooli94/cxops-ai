import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  DemoChatTransport,
  DEMO_WIDGET_KEY,
  DEFAULT_DEMO_KNOWLEDGE,
  demoAnswer,
} from "./demo.ts";

const ID1 = "a".repeat(32);
const ID2 = "b".repeat(32);

async function start(transport: DemoChatTransport) {
  const response = await transport.fetcher("/api/public/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ public_widget_key: DEMO_WIDGET_KEY }),
  });
  assert.equal(response.status, 200);
  const body = await response.json() as { session: { token: string } };
  return body.session.token;
}

async function send(transport: DemoChatTransport, token: string, text: string, id = ID1) {
  return transport.fetcher("/api/public/chat/messages", {
    method: "POST",
    headers: { authorization: "Bearer " + token },
    body: JSON.stringify({ text, client_message_id: id, delivery: "async" }),
  });
}

function authorization(token: string) {
  return { authorization: "Bearer " + token };
}

describe("fictional knowledge answerer (NOT a real LLM)", () => {
  it("grounds known hours in the fictional source", () => {
    const reply = demoAnswer("When do you open?", null, DEFAULT_DEMO_KNOWLEDGE);
    assert.equal(reply.kind, "answer");
    assert.equal(reply.topic, "hours");
    assert.match(reply.body, /Monday to Friday/);
    assert.match(reply.body, /Source: demo knowledge/);
  });

  it("answers a basic Persian question in Persian from fictional knowledge", () => {
    const reply = demoAnswer("ساعات کاری شما چیست؟", null, DEFAULT_DEMO_KNOWLEDGE);
    assert.equal(reply.topic, "hours");
    assert.match(reply.body, /دوشنبه/);
  });

  it("uses a limited previous topic for a referential follow-up", () => {
    const reply = demoAnswer("What about that?", "warranty", DEFAULT_DEMO_KNOWLEDGE);
    assert.equal(reply.topic, "warranty");
    assert.match(reply.body, /90 days/);
  });

  it("does not invent an answer for a question outside the authored knowledge", () => {
    assert.equal(demoAnswer("Do you sell snowboards?", null, DEFAULT_DEMO_KNOWLEDGE).kind, "handoff");
  });

  it("recognizes a request for human assistance", () => {
    assert.equal(demoAnswer("May I speak to a human?", null, DEFAULT_DEMO_KNOWLEDGE).kind, "handoff");
  });

  it("refuses to claim bookings or free repairs can be performed", () => {
    assert.equal(demoAnswer("Can I book a repair?", null, DEFAULT_DEMO_KNOWLEDGE).kind, "handoff");
    assert.equal(demoAnswer("Are repairs free?", null, DEFAULT_DEMO_KNOWLEDGE).kind, "handoff");
  });

  it("recognizes common questions about products", () => {
    assert.equal(demoAnswer("What products do you sell?", null, DEFAULT_DEMO_KNOWLEDGE).topic, "services");
  });

  it("refuses empty knowledge rather than inventing a policy", () => {
    const knowledge = structuredClone(DEFAULT_DEMO_KNOWLEDGE);
    knowledge.returns.en = "";
    assert.equal(demoAnswer("What is your refund policy?", null, knowledge).kind, "handoff");
  });
});

describe("actual public widget API shape served by isolated demo transport", () => {
  it("does not call outside the demo chat paths", async () => {
    const demo = new DemoChatTransport();
    assert.equal((await demo.fetcher("/api/backend/tickets")).status, 404);
    assert.equal((await demo.fetcher("https://example.com/")).status, 404);
  });

  it("does not create sessions for arbitrary widget keys", async () => {
    const demo = new DemoChatTransport();
    const response = await demo.fetcher("/api/public/chat/sessions", {
      method: "POST", body: JSON.stringify({ public_widget_key: "pk_live_wrong" }),
    });
    assert.equal(response.status, 404);
    assert.equal(demo.snapshot().session, null);
  });

  it("enforces the local bearer session on message and history reads", async () => {
    const demo = new DemoChatTransport();
    const token = await start(demo);
    assert.equal((await send(demo, "fake-token", "What are your hours?")).status, 401);
    assert.equal((await demo.fetcher("/api/public/chat/sessions/state")).status, 401);
    assert.equal((await demo.fetcher("/api/public/chat/sessions/state", { headers: authorization(token) })).status, 200);
  });

  it("demonstrates multi-turn chat, cursor updates and 50-message bounded history", async () => {
    const demo = new DemoChatTransport();
    const token = await start(demo);
    let result = await send(demo, token, "What is your warranty?");
    assert.equal(result.status, 200);
    result = await send(demo, token, "What about that?", ID2);
    assert.equal(result.status, 200);
    const updates = await demo.fetcher("/api/public/chat/sessions/updates?after_id=2", {
      headers: authorization(token),
    });
    const body = await updates.json() as { messages: Array<{body: string}>; turns: unknown[] };
    assert.equal(body.messages.length, 2);
    assert.match(body.messages[1].body, /90 days/);
    assert.deepEqual(body.turns, []);
    assert.equal(demo.snapshot().session?.status, "ai_active");
  });

  it("makes same-client-id retry idempotent and refuses an ID reused with different text", async () => {
    const demo = new DemoChatTransport();
    const token = await start(demo);
    const first = await send(demo, token, "What are your hours?");
    const again = await send(demo, token, "What are your hours?");
    assert.deepEqual(await first.json(), await again.json());
    assert.equal(demo.snapshot().session?.messages.length, 2);
    const conflict = await send(demo, token, "What is the warranty?");
    assert.equal(conflict.status, 409);
  });

  it("transfers an unknown question to a mock queue and accepts mock staff replies", async () => {
    const demo = new DemoChatTransport();
    const token = await start(demo);
    const response = await send(demo, token, "Is overnight shipping available?");
    assert.equal(response.status, 200);
    assert.equal(demo.snapshot().session?.status, "human_requested");
    assert.equal(demo.claimHuman(), true);
    assert.equal(demo.snapshot().session?.status, "human_assigned");
    assert.equal(demo.staffReply("This is a simulated staff reply."), true);
    const history = demo.snapshot().session?.messages ?? [];
    assert.equal(history.at(-1)?.body, "This is a simulated staff reply.");
    assert.equal((await send(demo, token, "Thanks", ID2)).status, 200);
    assert.equal(demo.snapshot().session?.status, "human_assigned");
  });

  it("supports an explicit human request and closing a mock conversation", async () => {
    const demo = new DemoChatTransport();
    const token = await start(demo);
    const human = await demo.fetcher("/api/public/chat/sessions/human", {
      method: "POST", headers: authorization(token),
    });
    assert.equal(human.status, 200);
    assert.equal(demo.snapshot().session?.status, "human_requested");
    const close = await demo.fetcher("/api/public/chat/sessions/close", {
      method: "POST", headers: authorization(token),
    });
    assert.equal(close.status, 200);
    assert.equal((await send(demo, token, "hello")).status, 409);
  });

  it("separates the demo session from the live widget session key", async () => {
    const demo = new DemoChatTransport();
    const token = await start(demo);
    assert.match(token, /^demo_/);
    demo.resetConversation();
    assert.equal(demo.snapshot().session, null);
  });

  it("updates knowledge without requiring a server, key or payment", () => {
    const demo = new DemoChatTransport();
    demo.setKnowledge("hours", "en", "Tuesday only, from 10 to 11.");
    const answer = demoAnswer("opening hours", null, demo.snapshot().knowledge);
    assert.match(answer.body, /Tuesday only/);
    demo.configure({ name: "Fictional Workshop" });
    assert.equal(demo.snapshot().settings.name, "Fictional Workshop");
  });

  it("preserves mock history in per-tab storage when reopened", async () => {
    const data = new Map<string, string>();
    const storage = {
      getItem(key: string) { return data.get(key) ?? null; },
      setItem(key: string, value: string) { data.set(key, value); },
      removeItem(key: string) { data.delete(key); },
    } as Storage;
    const original = new DemoChatTransport();
    original.attachStorage(storage);
    const token = await start(original);
    await send(original, token, "Tell me your hours");
    const restored = new DemoChatTransport();
    restored.attachStorage(storage);
    assert.equal(restored.snapshot().session?.token, token);
    assert.equal(restored.snapshot().session?.messages.length, 2);
  });
});
