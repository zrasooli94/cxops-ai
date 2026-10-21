"use strict";

const assert = require("node:assert/strict");
const { describe, it } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { runInNewContext } = require("node:vm");

const core = require("./review-core.js");

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.hidden = true;
    this.disabled = false;
    this.files = null;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.style = {};
    this.className = "";
    this.focused = false;
    this.classList = {
      toggle: () => {},
    };
  }
  addEventListener(name, callback) {
    (this.listeners[name] ||= []).push(callback);
  }
  async fire(name, event = {}) {
    for (const fn of this.listeners[name] || []) await fn(event);
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes.slice(); }
  setAttribute(name, value) { this.attributes[name] = value; }
  scrollIntoView() { this.scrolled = true; }
  focus() { this.focused = true; }
  remove() { this.removed = true; }
  click() { this.clicked = true; }
}

function sample() {
  const row = Object.fromEntries(core.FIELDS.map((key) => [key, ""]));
  Object.assign(row, {
    dataset_id: core.EXPECTED_DATASET,
    review_key: "aster-direct-contradiction:c1",
    fingerprint: "a".repeat(64),
    tenant_id: "aster-synthetic",
    case_id: "aster-direct-contradiction",
    claim_id: "c1",
    claim_text: "All Aster bicycle repairs are free.",
    answer_text: "All Aster bicycle repairs are free. [S1]",
    cited_source_ids: "S1",
    cited_source_passages: "[S1] REV aster-v1\nAster bicycle repairs are not free.",
  });
  const other = {
    ...row,
    review_key: "birch-farsi:c1",
    tenant_id: "birch-synthetic",
    case_id: "birch-farsi",
    fingerprint: "b".repeat(64),
    claim_text: "این متن نادرست است.",
    answer_text: "این متن نادرست است. [S1]",
    cited_source_passages: "[S1] REV birch-v1\nاین متن صحیح است.",
  };
  return core.serializeCsv([
    [...core.FIELDS],
    ...[row, other].map((r) => core.FIELDS.map((key) => r[key])),
  ]);
}

function boot() {
  const source = readFileSync(join(__dirname, "review-core.js"), "utf8");
  const app = readFileSync(join(__dirname, "app.js"), "utf8");
  const ids = [
    "reviewer-role", "reviewer-id", "worksheet-file", "workspace",
    "export-section", "dataset-name", "progress-text", "progress-fill",
    "nav-total", "claim-list", "claim-position", "claim-key",
    "claim-heading", "answer-text", "source-text", "claim-label",
    "claim-evidence", "claim-note", "claim-validation", "previous-claim",
    "next-claim", "use-selection", "export-description", "download-csv",
    "notice", "announcer",
  ];
  const el = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
  const doc = {
    getElementById: (id) => el[id],
    createElement: (tag) => new FakeElement(tag),
    body: new FakeElement("body"),
  };
  let download = null;
  let selected = "";
  let downloads = 0;
  const fakeUrl = {
    createObjectURL: (blob) => {
      download = blob;
      downloads += 1;
      return "blob:offline-only";
    },
    revokeObjectURL: (value) => {
      assert.equal(value, "blob:offline-only");
    },
  };
  const win = {
    addEventListener: () => {},
    getSelection: () => ({ toString: () => selected }),
  };
  const context = {
    window: win, document: doc, URL: fakeUrl,
    setTimeout: () => 1, clearTimeout: () => {},
    Blob: class FakeBlob {
      constructor(parts, options) {
        this.parts = parts;
        this.type = options.type;
      }
    },
    console,
  };
  runInNewContext(source, context);
  runInNewContext(app, context);
  return {
    el, doc,
    setSelected: (text) => { selected = text; },
    downloaded: () => download,
    downloadCount: () => downloads,
  };
}

describe("actual offline reviewer UI event flow (Node DOM harness)", () => {
  it("imports, displays plain text, quotes evidence and exports only review A", async () => {
    const { el, setSelected, downloaded, downloadCount } = boot();
    const file = sample();
    el["reviewer-role"].value = "1";
    el["reviewer-id"].value = "A1";
    el["worksheet-file"].files = [{
      size: file.length, text: async () => file,
    }];
    await el["worksheet-file"].fire("change");
    assert.equal(el.workspace.hidden, false);
    assert.equal(el["export-section"].hidden, false);
    assert.equal(el["reviewer-role"].disabled, true);
    assert.equal(el["reviewer-id"].disabled, true);
    assert.equal(el["progress-text"].textContent, "0 of 2 reviewed");
    assert.equal(
      el["claim-heading"].textContent,
      "All Aster bicycle repairs are free.",
    );
    assert.equal(
      el["source-text"].textContent,
      "[S1] REV aster-v1\nAster bicycle repairs are not free.",
    );
    assert.equal(el["claim-list"].children.length, 2);

    el["claim-label"].value = "contradicted";
    await el["claim-label"].fire("input");
    assert.equal(el["progress-text"].textContent, "0 of 2 reviewed");

    setSelected("Aster bicycle repairs are not free.");
    await el["use-selection"].fire("click");
    assert.equal(
      el["claim-evidence"].value,
      "Aster bicycle repairs are not free.",
    );
    assert.equal(el["progress-text"].textContent, "1 of 2 reviewed");

    await el["next-claim"].fire("click");
    assert.equal(el["claim-heading"].textContent, "این متن نادرست است.");

    await el["download-csv"].fire("click");
    assert.equal(downloadCount(), 1);
    const exported = downloaded().parts.join("");
    assert.match(exported, /Aster bicycle repairs are not free/);
    const rows = core.validateWorksheet(exported, 1, "A1");
    assert.equal(rows[0].reviewer_1_label, "contradicted");
    assert.equal(rows[0].reviewer_1_evidence, "Aster bicycle repairs are not free.");
    assert.equal(rows[0].reviewer_2_label, "");
    assert.equal(rows[1].reviewer_1_label, "");
    assert.equal(core.progress(rows, 1, "A1").complete, false);
  });

  it("refuses a fabricated other-reviewer column rather than importing it", async () => {
    const { el, downloadCount } = boot();
    const baseline = core.parseCsv(sample());
    const bIdx = core.FIELDS.indexOf("reviewer_2");
    baseline[1][bIdx] = "bad";
    const file = core.serializeCsv(baseline);
    el["reviewer-role"].value = "1";
    el["reviewer-id"].value = "A1";
    el["worksheet-file"].files = [{
      size: file.length, text: async () => file,
    }];
    await el["worksheet-file"].fire("change");
    assert.equal(el.workspace.hidden, true);
    assert.match(el.notice.textContent, /another reviewer/);
    assert.equal(downloadCount(), 0);
  });

  it("ignores an inaccurate text selection and keeps evidence blank", async () => {
    const { el, setSelected } = boot();
    const file = sample();
    el["reviewer-role"].value = "2";
    el["reviewer-id"].value = "B2";
    el["worksheet-file"].files = [{ size: file.length, text: async () => file }];
    await el["worksheet-file"].fire("change");
    setSelected("quoted text that does not exist in source");
    await el["use-selection"].fire("click");
    assert.equal(el["claim-evidence"].value, "");
    assert.match(el.notice.textContent, /Select 8/);
  });
});
