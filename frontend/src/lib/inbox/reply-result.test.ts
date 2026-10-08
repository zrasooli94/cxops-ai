import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  REPLY_DUPLICATE,
  REPLY_SUCCESS_QUEUED,
  REPLY_SUCCESS_SENT,
  replySuccessMessage,
} from "./reply-result.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = join(HERE, "..", "..", "..");
const pageSource = readFileSync(
  join(
    FRONTEND_ROOT,
    "src",
    "app",
    "(control-center)",
    "inbox",
    "page.tsx",
  ),
  "utf8",
);

describe("replySuccessMessage", () => {
  it("reports a CXOps-local reply as sent when delivery_status is sent", () => {
    assert.equal(
      replySuccessMessage({
        delivery_status: "sent",
        duplicate: false,
      }),
      REPLY_SUCCESS_SENT,
    );
    assert.equal(REPLY_SUCCESS_SENT, "Reply sent.");
  });

  it("reports a worker-delivered reply as queued when delivery_status is queued", () => {
    assert.equal(
      replySuccessMessage({
        delivery_status: "queued",
        duplicate: false,
      }),
      REPLY_SUCCESS_QUEUED,
    );
    assert.equal(REPLY_SUCCESS_QUEUED, "Reply queued for delivery.");
  });

  it("keeps the queued wording for an absent or unknown delivery_status", () => {
    assert.equal(replySuccessMessage({}), REPLY_SUCCESS_QUEUED);
    assert.equal(
      replySuccessMessage({ delivery_status: null }),
      REPLY_SUCCESS_QUEUED,
    );
    assert.equal(
      replySuccessMessage({ delivery_status: "sending" }),
      REPLY_SUCCESS_QUEUED,
    );
    assert.equal(
      replySuccessMessage({ delivery_status: "retrying" }),
      REPLY_SUCCESS_QUEUED,
    );
    assert.equal(
      replySuccessMessage({ delivery_status: "failed" }),
      REPLY_SUCCESS_QUEUED,
    );
  });

  it("reports a replayed client request as already submitted regardless of status", () => {
    assert.equal(
      replySuccessMessage({
        duplicate: true,
        delivery_status: "sent",
      }),
      REPLY_DUPLICATE,
    );
    assert.equal(
      replySuccessMessage({
        duplicate: true,
        delivery_status: "queued",
      }),
      REPLY_DUPLICATE,
    );
    assert.equal(
      replySuccessMessage({ duplicate: true }),
      REPLY_DUPLICATE,
    );
    assert.equal(REPLY_DUPLICATE, "This reply was already submitted.");
  });

  it("treats any truthy duplicate field as a replay", () => {
    assert.equal(
      replySuccessMessage({
        duplicate: 1,
        delivery_status: "sent",
      }),
      REPLY_DUPLICATE,
    );
  });
});

describe("inbox page reply wiring", () => {
  it("derives the reply success message from the parsed API result", () => {
    const conflict = pageSource.indexOf("if (response.status === 409) {");
    assert.notEqual(conflict, -1, "reply conflict branch missing");
    const replyOkCheck = pageSource.indexOf(
      "if (!response.ok) {",
      conflict,
    );
    assert.notEqual(replyOkCheck, -1, "reply non-ok branch missing");
    const successRegion = pageSource.slice(replyOkCheck);
    assert.match(successRegion, /as ReplyResult;/);
    assert.match(
      successRegion,
      /setReplySuccess\(replySuccessMessage\(result\)\)/,
    );
    assert.ok(
      !successRegion.includes(
        'setReplySuccess("Reply queued for delivery.")',
      ),
      "hardcoded success copy must not remain in the reply path",
    );
  });

  it("keeps the conversation-closed 409 duplicate feedback", () => {
    const conflict = pageSource.indexOf("if (response.status === 409) {");
    const replyOkCheck = pageSource.indexOf(
      "if (!response.ok) {",
      conflict,
    );
    const duplicateRegion = pageSource.slice(conflict, replyOkCheck);
    assert.match(
      duplicateRegion,
      /setReplySuccess\(\s*"This reply was already submitted\."\s*,?\s*\);/,
    );
  });

  it("leaves the error path and the retry path untouched", () => {
    assert.match(pageSource, /Reply API returned \$\{response\.status\}/);
    assert.match(
      pageSource,
      /setReplySuccess\("Retry queued for delivery\."\)/,
    );
  });

  it("imports the reply-result helper into the page", () => {
    const importEnd = pageSource.indexOf(
      'from "@/lib/inbox/reply-result"',
    );
    assert.notEqual(importEnd, -1, "lib import missing");
    const importBlock = pageSource.slice(
      Math.max(0, importEnd - 400),
      importEnd,
    );
    assert.match(importBlock, /replySuccessMessage/);
    assert.match(importBlock, /type ReplyResult/);
  });
});