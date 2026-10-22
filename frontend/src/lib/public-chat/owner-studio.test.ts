import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildKnowledgeGapReview,
  evidenceCoverageLabel,
  refreshSelectedConversation,
  selectCurrentEvidence,
  staffReplyOutcomeCopy,
  summarizeOwnerStudio,
} from "./owner-studio.ts";

describe("refreshSelectedConversation", () => {
  it("drops a late reply refresh after the operator switches conversations", async () => {
    let release!: (messages: string[]) => void;
    let selected: number | null = 11;
    const applied: Array<{ messages: string[]; conversationId: number }> = [];
    const pending = refreshSelectedConversation(
      11,
      () => new Promise<string[]>((resolve) => { release = resolve; }),
      () => selected,
      (messages, conversationId) => applied.push({ messages, conversationId }),
    );

    selected = 22;
    release(["late conversation 11 reply"]);

    assert.equal(await pending, false);
    assert.deepEqual(applied, []);
  });

  it("applies a reply refresh to the conversation that is still selected", async () => {
    const applied: Array<{ messages: string[]; conversationId: number }> = [];
    const refreshed = await refreshSelectedConversation(
      11,
      async () => ["current conversation reply"],
      () => 11,
      (messages, conversationId) => applied.push({ messages, conversationId }),
    );

    assert.equal(refreshed, true);
    assert.deepEqual(applied, [{ messages: ["current conversation reply"], conversationId: 11 }]);
  });

  it("does not apply a failed refresh to either conversation", async () => {
    const applied: Array<{ messages: string[]; conversationId: number }> = [];
    await assert.rejects(
      refreshSelectedConversation(
        11,
        async () => { throw new Error("synthetic refresh failure"); },
        () => 22,
        (messages, conversationId) => applied.push({ messages, conversationId }),
      ),
      /synthetic refresh failure/,
    );
    assert.deepEqual(applied, []);
  });
});

describe("staffReplyOutcomeCopy", () => {
  it("only offers refresh-only recovery after a confirmed save", () => {
    assert.equal(staffReplyOutcomeCopy("saved_refresh_failed", "en").action, "Refresh conversation");
    assert.equal(staffReplyOutcomeCopy("send_failed", "en").action, null);
    assert.equal(staffReplyOutcomeCopy("send_uncertain", "en").action, null);
  });

  it("distinguishes confirmed rejection and uncertain delivery in Persian", () => {
    assert.match(staffReplyOutcomeCopy("send_failed", "fa").message, /پذیرفته نشد/);
    assert.match(staffReplyOutcomeCopy("send_uncertain", "fa").message, /نامشخص/);
    assert.match(staffReplyOutcomeCopy("saved_refresh_failed", "fa").message, /ذخیره شد/);
  });
});

describe("summarizeOwnerStudio", () => {
  it("derives tenant inbox metrics without counting closed handoffs as active", () => {
    assert.deepEqual(summarizeOwnerStudio(
      [
        { status: "open", needs_response: true },
        { status: "solved", needs_response: false },
        { status: "closed", needs_response: false },
      ],
      [{ status: "human_requested" }, { status: "human_assigned" }, { status: "closed" }],
    ), { conversations: 3, awaitingReply: 1, activeHandoffs: 2, resolved: 2 });
  });
});

describe("buildKnowledgeGapReview", () => {
  it("finds the latest customer question and ranks only possibly relevant tenant sources", () => {
    assert.deepEqual(buildKnowledgeGapReview(
      [
        { direction: "inbound", body: "Do you repair skis?" },
        { direction: "outbound", body: "A support team member needs to review this request." },
        { direction: "inbound", body: "Can you repair a snowboard?" },
      ],
      [
        { title: "Bicycle repair", content: "We repair bicycles." },
        { title: "Snowboard rentals", content: "Snowboard rentals are documented." },
        { title: "Store hours", content: "Open Tuesday." },
      ],
      true,
    ), {
      question: "Do you repair skis?",
      relevantSources: [
        { title: "Bicycle repair", content: "We repair bicycles.", overlap: 1 },
      ],
    });
  });

  it("never turns a post-handoff visitor update into the suggested FAQ", () => {
    const review = buildKnowledgeGapReview([
      { direction: "inbound", body: "Do you offer wheel building?" },
      { direction: "outbound", body: "A support team member needs to review this request." },
      { direction: "inbound", body: "I am still here after staff takeover" },
    ], [], true);
    assert.equal(review?.question, "Do you offer wheel building?");
  });

  it("does not infer a gap without a handoff or customer message", () => {
    assert.equal(buildKnowledgeGapReview([{ direction: "inbound", body: "Unknown" }], [], false), null);
    assert.equal(buildKnowledgeGapReview([{ direction: "outbound", body: "Transferred" }], [], true), null);
    assert.equal(buildKnowledgeGapReview([
      { direction: "inbound", body: "Original question" },
      { direction: "outbound", body: "A localized or alternate handoff reply" },
      { direction: "inbound", body: "Later visitor update" },
    ], [], true), null);
  });

  it("normalizes Persian Arabic variants and zero-width joiners", () => {
    const review = buildKnowledgeGapReview(
      [
        { direction: "inbound", body: "آیا دوچرخه‌ی كودك داريد؟" },
        { direction: "outbound", body: "A support team member needs to review this request." },
      ],
      [{ title: "دوچرخه کودک", content: "دوچرخه‌های کودکان موجود است." }],
      true,
    );
    assert.equal(review?.relevantSources[0]?.title, "دوچرخه کودک");
  });
});

describe("owner evidence panel states", () => {
  const missing = { question: "پرسش قدیمی", coverage: "missing" as const, items: [] };

  it("hides late evidence from the previously selected conversation", () => {
    assert.equal(selectCurrentEvidence("پرسش تازه", missing), null);
    assert.equal(selectCurrentEvidence(undefined, missing), null);
    assert.equal(selectCurrentEvidence("پرسش قدیمی", null), null);
    assert.equal(selectCurrentEvidence("پرسش قدیمی", missing), missing);
  });

  it("labels missing, ambiguous, and related results as lexical evidence in both languages", () => {
    assert.equal(evidenceCoverageLabel("missing", "en"), "No lexical coverage");
    assert.equal(evidenceCoverageLabel("ambiguous", "en"), "Multiple lexical matches");
    assert.equal(evidenceCoverageLabel("related", "en"), "Lexical match found");
    assert.equal(evidenceCoverageLabel("missing", "fa"), "پوشش واژگانی یافت نشد");
    assert.equal(evidenceCoverageLabel("ambiguous", "fa"), "چند هم‌پوشانی واژگانی");
    assert.equal(evidenceCoverageLabel("related", "fa"), "هم‌پوشانی واژگانی یافت شد");
  });
});
