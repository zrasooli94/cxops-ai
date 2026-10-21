/* CXOps C7 — Offline, synthetic-only reviewer worksheet codec.
 *
 * Classic-script UMD works when index.html is opened with file://. No
 * dependencies, fetch, storage, provider calls, dynamic code or network.
 * The Python C6 dataset and scorer remain authoritative for SHA-256 source
 * fingerprints and claim/evidence completeness. This UI never certifies
 * a model, reviewer identity or production deployment.
 */
(function (root) {
  "use strict";

  const EXPECTED_DATASET = "c6-fictional-faq-20261021";
  const MAX_CSV_BYTES = 12_000_000;
  const MAX_ROWS = 500;
  const LABELS = Object.freeze([
    "supported", "contradicted", "unsupported", "unclear",
  ]);
  const FIELDS = Object.freeze([
    "dataset_id", "review_key", "fingerprint", "tenant_id", "case_id",
    "claim_id", "claim_text", "answer_text", "cited_source_ids",
    "cited_source_passages", "reviewer_1", "reviewer_1_label",
    "reviewer_1_evidence", "reviewer_1_note", "reviewer_2",
    "reviewer_2_label", "reviewer_2_evidence", "reviewer_2_note",
    "adjudicator", "adjudicated_label", "adjudication_note",
  ]);
  const IMMUTABLE = FIELDS.slice(0, FIELDS.indexOf("reviewer_1"));
  const DISALLOWED_FORMULA = /^[ \t\r\n]*[=+\-@]/;
  const ID = /^[a-z][a-z0-9-]{0,79}$/;
  const HASH = /^[a-f0-9]{64}$/;

  function fail(message) {
    throw new Error(message);
  }

  function parseCsv(input) {
    if (typeof input !== "string" || input.length > MAX_CSV_BYTES) {
      fail("This CSV file is missing or exceeds the 12 MB local limit.");
    }
    const text = input.startsWith("\ufeff") ? input.slice(1) : input;
    const result = [];
    let currentRow = [];
    let cell = "";
    let inQuote = false;
    let justClosedQuote = false;
    let atCellStart = true;

    function pushCell() {
      currentRow.push(cell);
      cell = "";
      atCellStart = true;
      justClosedQuote = false;
    }
    function pushRow() {
      pushCell();
      result.push(currentRow);
      currentRow = [];
      if (result.length > MAX_ROWS + 1) {
        fail("Worksheet contains more than 500 claims.");
      }
    }

    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (inQuote) {
        if (ch === '"') {
          if (text[i + 1] === '"') {
            cell += '"';
            i += 1;
          } else {
            inQuote = false;
            justClosedQuote = true;
          }
        } else {
          cell += ch;
        }
      } else if (ch === ",") {
        pushCell();
      } else if (ch === "\r" || ch === "\n") {
        pushRow();
        if (ch === "\r" && text[i + 1] === "\n") i += 1;
      } else if (ch === '"') {
        if (!atCellStart || justClosedQuote) {
          fail("Invalid CSV quoting. Export a clean C6 worksheet.");
        }
        inQuote = true;
        atCellStart = false;
      } else if (justClosedQuote) {
        fail("Unexpected text after a closing CSV quote.");
      } else {
        cell += ch;
        atCellStart = false;
      }
    }
    if (inQuote) fail("Unterminated CSV quote.");
    if (currentRow.length > 0 || cell !== "" || justClosedQuote) pushRow();
    if (result.length < 2) fail("No review rows were found.");
    return result;
  }

  function serializeCsv(rows) {
    if (!Array.isArray(rows) || rows.length < 1 || rows.length > MAX_ROWS + 1) {
      fail("Invalid worksheet size.");
    }
    return rows.map((row) => {
      if (!Array.isArray(row) || row.length !== FIELDS.length) {
        fail("Unexpected CSV column count.");
      }
      return row.map((value) => {
        if (typeof value !== "string") fail("CSV cell must be text.");
        return '"' + value.replace(/"/g, '""') + '"';
      }).join(",");
    }).join("\r\n") + "\r\n";
  }

  function sourceBodies(passages) {
    if (typeof passages !== "string" || passages.length > 110_000) {
      fail("Missing or overlong cited source passages.");
    }
    const parts = passages.split(/\n\n(?=\[S[1-9][0-9]*\] REV )/);
    const bodies = parts.map((part) => {
      if (!/^\[S[1-9][0-9]*\] REV [^\n]+\n/.test(part)) {
        fail("Cited sources must carry a versioned source header.");
      }
      return part.slice(part.indexOf("\n") + 1);
    });
    if (!bodies.length || bodies.some((body) => !body.trim())) {
      fail("A cited passage is empty.");
    }
    return bodies;
  }

  function safeIdentity(value) {
    return typeof value === "string"
      && value.trim().length >= 2
      && value.trim().length <= 80
      && !DISALLOWED_FORMULA.test(value);
  }

  function safelyEditable(value, max) {
    return typeof value === "string"
      && value.length <= max
      && !DISALLOWED_FORMULA.test(value);
  }

  function validateWorksheet(text, reviewerSlot, identity) {
    if (reviewerSlot !== 1 && reviewerSlot !== 2) {
      fail("Choose Reviewer A or B before loading the worksheet.");
    }
    if (!safeIdentity(identity)) {
      fail("Enter a reviewer ID (2–80 characters; no formula-like prefix).");
    }
    const parsed = parseCsv(text);
    const headers = parsed[0];
    if (headers.length !== FIELDS.length
        || headers.some((name, i) => name !== FIELDS[i])) {
      fail("The CSV columns do not match the C6 review schema.");
    }
    const seen = new Set();
    const ownPrefix = "reviewer_" + reviewerSlot;
    const otherPrefix = "reviewer_" + (reviewerSlot === 1 ? 2 : 1);
    const rows = parsed.slice(1).map((cells, index) => {
      if (cells.length !== FIELDS.length) {
        fail("A worksheet row has an unexpected number of columns.");
      }
      const row = Object.fromEntries(FIELDS.map((field, i) => [field, cells[i]]));
      if (row.dataset_id !== EXPECTED_DATASET
          || !ID.test(row.tenant_id)
          || !row.tenant_id.endsWith("-synthetic")
          || !ID.test(row.case_id)
          || !ID.test(row.claim_id)
          || row.review_key !== row.case_id + ":" + row.claim_id
          || !HASH.test(row.fingerprint)
          || seen.has(row.review_key)) {
        fail("Only the exact C6 synthetic worksheet is supported.");
      }
      seen.add(row.review_key);
      if (!row.claim_text || !row.answer_text || !row.cited_source_ids) {
        fail("The worksheet is missing its claim, answer or citations.");
      }
      const cited = row.cited_source_ids.split(",");
      if (cited.length < 1 || cited.length > 10
          || new Set(cited).size !== cited.length
          || cited.some((id) => !/^S[1-9][0-9]*$/.test(id)
            || !row.cited_source_passages.includes("[" + id + "] REV "))) {
        fail("A cited source ID is missing or invalid.");
      }
      sourceBodies(row.cited_source_passages);
      const extra = [
        otherPrefix, otherPrefix + "_label", otherPrefix + "_evidence",
        otherPrefix + "_note", "adjudicator", "adjudicated_label",
        "adjudication_note",
      ];
      if (extra.some((field) => row[field] !== "")) {
        fail("This file contains another reviewer's or an adjudicator's data.");
      }
      if (row[ownPrefix] && row[ownPrefix] !== identity.trim()) {
        fail("Review ID does not match the author of the imported draft.");
      }
      for (const field of IMMUTABLE) {
        if (typeof row[field] !== "string" || row[field].length > 120_000
            || DISALLOWED_FORMULA.test(row[field])) {
          fail("Protected source data is too long or spreadsheet-unsafe.");
        }
      }
      if (!safelyEditable(row[ownPrefix], 80)
          || !safelyEditable(row[ownPrefix + "_label"], 20)
          || !safelyEditable(row[ownPrefix + "_evidence"], 1000)
          || !safelyEditable(row[ownPrefix + "_note"], 500)
          || (row[ownPrefix + "_label"]
            && !LABELS.includes(row[ownPrefix + "_label"]))) {
        fail("An imported private reviewer field is invalid.");
      }
      if (index > MAX_ROWS) fail("Too many claims.");
      return row;
    });
    if (rows.length < 1) fail("No reviewable claims in the worksheet.");
    return rows;
  }

  function reviewStatus(row, slot, identity) {
    const prefix = "reviewer_" + slot;
    const label = row[prefix + "_label"];
    const evidence = row[prefix + "_evidence"];
    const note = row[prefix + "_note"];
    if (!label) return { complete: false, reason: "Choose a claim label." };
    if (!LABELS.includes(label)) {
      return { complete: false, reason: "Choose a valid claim label." };
    }
    if (!safeIdentity(identity) || row[prefix] !== identity.trim()) {
      return { complete: false, reason: "Reviewer ID does not match." };
    }
    if (!safelyEditable(evidence, 1000) || !safelyEditable(note, 500)) {
      return { complete: false, reason: "A field is overlong or formula-like." };
    }
    if (label === "supported" || label === "contradicted") {
      if (evidence.trim().length < 8 || !sourceBodies(
        row.cited_source_passages
      ).some((body) => body.includes(evidence.trim()))) {
        return { complete: false, reason: "Quote 8+ exact characters from a cited source." };
      }
    } else if (note.trim().length < 10) {
      return { complete: false, reason: "Add a rationale of at least 10 characters." };
    }
    return { complete: true, reason: "" };
  }

  function progress(rows, slot, identity) {
    const finished = rows.filter((row) => reviewStatus(row, slot, identity).complete).length;
    return { finished, total: rows.length, complete: finished === rows.length };
  }

  function editRow(rows, index, slot, identity, changes) {
    if (!Array.isArray(rows) || index < 0 || index >= rows.length) {
      fail("Invalid claim selection.");
    }
    if (slot !== 1 && slot !== 2 || !safeIdentity(identity)) {
      fail("Missing reviewer role or ID.");
    }
    const prefix = "reviewer_" + slot;
    if (Object.keys(changes).some(
      (key) => !["label", "evidence", "note"].includes(key)
    )) {
      fail("Only your own label, evidence and note may be edited.");
    }
    const row = { ...rows[index] };
    row[prefix] = identity.trim();
    for (const [key, value] of Object.entries(changes)) {
      const max = key === "evidence" ? 1000 : key === "note" ? 500 : 20;
      if (!safelyEditable(value, max)
          || (key === "label" && value && !LABELS.includes(value))) {
        fail("Invalid or formula-like review field.");
      }
      row[prefix + "_" + key] = value;
    }
    const updated = rows.slice();
    updated[index] = row;
    return updated;
  }

  function exportReviewerCsv(rows, slot, identity, options = {}) {
    if (!Array.isArray(rows) || rows.length === 0 || rows.length > MAX_ROWS) {
      fail("No bounded review data to export.");
    }
    const prefix = "reviewer_" + slot;
    for (const row of rows) {
      if (row[prefix] && row[prefix] !== identity.trim()) {
        fail("Imported reviewer identity changed.");
      }
      if (row[prefix + "_label"] && !LABELS.includes(row[prefix + "_label"])) {
        fail("A claim has an invalid label.");
      }
      const checked = reviewStatus(row, slot, identity);
      if (!checked.complete && row[prefix + "_label"] && !options.allowIncomplete) {
        fail("Finish or clear the invalid judgment before export.");
      }
      for (const field of [prefix, prefix + "_label", prefix + "_evidence",
        prefix + "_note"]) {
        const max = field === prefix ? 80
          : field.endsWith("_note") ? 500
            : field.endsWith("_evidence") ? 1000 : 20;
        if (!safelyEditable(row[field], max)) {
          fail("A reviewer field contains an unsafe spreadsheet value.");
        }
      }
      const other = "reviewer_" + (slot === 1 ? 2 : 1);
      if ([other, other + "_label", other + "_evidence",
        other + "_note", "adjudicator", "adjudicated_label",
        "adjudication_note"].some((name) => row[name])) {
        fail("Export would leak other reviewer or adjudication fields.");
      }
    }
    return serializeCsv([
      [...FIELDS],
      ...rows.map((row) => FIELDS.map((field) => row[field])),
    ]);
  }

  const api = Object.freeze({
    EXPECTED_DATASET, FIELDS, LABELS,
    parseCsv, serializeCsv, sourceBodies, safeIdentity, validateWorksheet,
    reviewStatus, progress, editRow, exportReviewerCsv,
  });
  if (typeof module === "object" && module.exports) module.exports = api;
  root.CXReview = api;
})(typeof window === "object" ? window : globalThis);
