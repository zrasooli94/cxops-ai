import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { PublicChatApiError } from "./client.ts";
import {
  clientMessageIdForSend,
  isRetryableSendFailure,
  sendWithRetry,
} from "./send-retry.ts";

const noSleep = async () => {};

describe("isRetryableSendFailure", () => {
  it("retries ambiguous and come-back-later failures", () => {
    assert.equal(isRetryableSendFailure(new TypeError("Failed to fetch")), true);
    assert.equal(isRetryableSendFailure(new PublicChatApiError(503, "down")), true);
    assert.equal(isRetryableSendFailure(new PublicChatApiError(502, "bad gateway")), true);
    assert.equal(isRetryableSendFailure(new PublicChatApiError(409, "in flight")), true);
    assert.equal(isRetryableSendFailure(new PublicChatApiError(429, "busy")), true);
  });

  it("does not retry definitive rejections", () => {
    for (const status of [400, 401, 403, 404, 413, 422]) {
      assert.equal(isRetryableSendFailure(new PublicChatApiError(status, "no")), false);
    }
  });
});

describe("sendWithRetry", () => {
  it("retries with the same attempt until it succeeds", async () => {
    const ids: string[] = [];
    let calls = 0;
    const result = await sendWithRetry(
      async () => {
        ids.push("m-1");
        calls += 1;
        if (calls < 3) throw new TypeError("network down");
        return "ok";
      },
      { sleep: noSleep },
    );
    assert.equal(result, "ok");
    assert.deepEqual(ids, ["m-1", "m-1", "m-1"]);
  });

  it("gives up after the configured retries", async () => {
    let calls = 0;
    await assert.rejects(
      sendWithRetry(
        async () => {
          calls += 1;
          throw new PublicChatApiError(503, "down");
        },
        { sleep: noSleep, delaysMs: [1, 1] },
      ),
      PublicChatApiError,
    );
    assert.equal(calls, 3);
  });

  it("does not retry a definitive rejection", async () => {
    let calls = 0;
    await assert.rejects(
      sendWithRetry(
        async () => {
          calls += 1;
          throw new PublicChatApiError(413, "too long");
        },
        { sleep: noSleep },
      ),
    );
    assert.equal(calls, 1);
  });
});

describe("clientMessageIdForSend", () => {
  it("reuses the failed send's id for the same text only", () => {
    const pending = { text: "Where is my order?", clientMessageId: "m-1" };
    assert.equal(clientMessageIdForSend("Where is my order?", pending, () => "new"), "m-1");
    assert.equal(clientMessageIdForSend("Something else", pending, () => "new"), "new");
    assert.equal(clientMessageIdForSend("Where is my order?", null, () => "new"), "new");
  });
});
