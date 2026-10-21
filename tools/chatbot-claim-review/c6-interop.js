"use strict";

/* CI-only C6 interoperability proof: two FICTIONAL worksheet reviewers.
 * This is not a human evaluation or a measure of chatbot accuracy.
 * Python's C6 validator, not the browser interface, verifies source hashes.
 */
const { readFileSync, writeFileSync } = require("node:fs");
const { join } = require("node:path");
const core = require("./review-core.js");

if (!process.env.CI || !process.env.GITHUB_ACTIONS
    || process.env.ENVIRONMENT !== "development"
    || process.env.DATABASE_URL
    || process.env.OPENAI_API_KEY
    || process.env.EVALUATION_OPENAI_API_KEY) {
  throw new Error("C7 interop may run only in offline GitHub Actions.");
}
const [blankPath, directory] = process.argv.slice(2);
if (!blankPath || !directory) {
  throw new Error("Expected a blank C6 CSV path and a temporary output directory.");
}
const text = readFileSync(blankPath, "utf8");
for (const slot of [1, 2]) {
  const identity = "fixture-only-reviewer-" + (slot === 1 ? "A" : "B");
  let rows = core.validateWorksheet(text, slot, identity);
  rows.forEach((_row, index) => {
    rows = core.editRow(rows, index, slot, identity, {
      label: "unclear",
      note: "CI fabricated label only: not a real independent human review.",
      evidence: "",
    });
  });
  if (!core.progress(rows, slot, identity).complete) {
    throw new Error("Fake C6 reviewer test fixture unexpectedly incomplete.");
  }
  const out = join(directory, "fixture-only-reviewer-" + slot + ".csv");
  writeFileSync(out, "\ufeff" + core.exportReviewerCsv(rows, slot, identity), {
    flag: "wx", encoding: "utf8",
  });
}
console.log("Synthetic-only reviewer CSV files exported; NOT CERTIFIED.");
