/* CXOps C7 | Offline browser interface. Works directly under file://.
 * No fetch/XMLHttpRequest/WebSocket, storage, external models or provider keys.
 * The Python C6 scorer alone decides whether a CSV can be combined/scored.
 */
(function () {
  "use strict";

  const core = window.CXReview;
  if (!core) throw new Error("Offline worksheet codec is unavailable.");
  const byId = (id) => document.getElementById(id);
  const ui = {
    role: byId("reviewer-role"), identity: byId("reviewer-id"),
    file: byId("worksheet-file"), workspace: byId("workspace"),
    exportSection: byId("export-section"), dataset: byId("dataset-name"),
    progress: byId("progress-text"), progressFill: byId("progress-fill"),
    navTotal: byId("nav-total"), list: byId("claim-list"),
    position: byId("claim-position"), key: byId("claim-key"),
    claim: byId("claim-heading"), answer: byId("answer-text"),
    source: byId("source-text"), label: byId("claim-label"),
    evidence: byId("claim-evidence"), note: byId("claim-note"),
    validation: byId("claim-validation"), previous: byId("previous-claim"),
    next: byId("next-claim"), useSelection: byId("use-selection"),
    exportDescription: byId("export-description"), download: byId("download-csv"),
    notice: byId("notice"), announcer: byId("announcer"),
  };

  let rows = [];
  let slot = 1;
  let identity = "";
  let current = 0;
  let changedSinceDownload = false;
  let noticeTimeout = null;

  function ready() {
    return rows.length > 0;
  }

  function inform(message, error) {
    ui.notice.textContent = message;
    ui.notice.hidden = false;
    ui.notice.style.background = error ? "#382722" : "#193b2b";
    ui.notice.style.borderColor = error ? "#b37861" : "#547d54";
    ui.announcer.textContent = message;
    if (noticeTimeout !== null) clearTimeout(noticeTimeout);
    noticeTimeout = setTimeout(() => {
      ui.notice.hidden = true;
      noticeTimeout = null;
    }, 5000);
  }

  function checkProgress() {
    const status = core.progress(rows, slot, identity);
    ui.progress.textContent = status.finished + " of " + status.total + " reviewed";
    ui.progressFill.style.width = (
      status.total ? Math.round(100 * status.finished / status.total) : 0
    ) + "%";
    ui.navTotal.textContent = String(status.total) + " TOTAL";
    ui.exportDescription.textContent = status.complete
      ? "All local review fields are complete. Download your private reviewer file for combination and human adjudication; this does NOT certify a model."
      : "Your review is incomplete. Download a DRAFT to continue later on this device; it cannot be combined as a finished independent review.";
    return status;
  }

  function renderNavigation() {
    ui.list.replaceChildren();
    rows.forEach((row, index) => {
      const judgment = core.reviewStatus(row, slot, identity);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "claim-item" + (index === current ? " active" : "");
      button.setAttribute("aria-current", index === current ? "step" : "false");
      const number = document.createElement("span");
      number.className = "item-number";
      number.textContent = String(index + 1).padStart(2, "0");
      const body = document.createElement("span");
      body.className = "item-content";
      const title = document.createElement("strong");
      title.dir = "auto";
      title.textContent = row.claim_text;
      const subtitle = document.createElement("small");
      subtitle.textContent = row.case_id;
      body.append(title, subtitle);
      const icon = document.createElement("span");
      icon.className = "item-status" + (judgment.complete ? " done" : "");
      icon.textContent = judgment.complete ? "✓" : "○";
      icon.setAttribute(
        "aria-label", judgment.complete ? "Review complete" : "Not yet complete"
      );
      button.append(number, body, icon);
      button.addEventListener("click", () => {
        current = index;
        renderAll();
        ui.claim.focus?.();
      });
      ui.list.append(button);
    });
  }

  function renderCurrent() {
    if (!ready()) return;
    const row = rows[current];
    const prefix = "reviewer_" + slot;
    ui.position.textContent = String(current + 1).padStart(2, "0")
      + " / " + String(rows.length).padStart(2, "0");
    ui.key.textContent = row.review_key;
    ui.claim.textContent = row.claim_text;
    ui.claim.setAttribute("tabindex", "-1");
    ui.answer.textContent = row.answer_text;
    ui.source.textContent = row.cited_source_passages;
    ui.label.value = row[prefix + "_label"];
    ui.evidence.value = row[prefix + "_evidence"];
    ui.note.value = row[prefix + "_note"];
    ui.previous.disabled = current === 0;
    ui.next.disabled = current === rows.length - 1;
    refreshValidation();
  }

  function refreshValidation() {
    if (!ready()) return;
    const state = core.reviewStatus(rows[current], slot, identity);
    ui.validation.classList.toggle("good", state.complete);
    ui.validation.textContent = state.complete
      ? "✓ Ready to export. This is a recorded judgment, not a factuality certificate."
      : state.reason;
  }

  function renderAll() {
    checkProgress();
    renderNavigation();
    renderCurrent();
  }

  function editCurrent() {
    if (!ready()) return;
    try {
      rows = core.editRow(rows, current, slot, identity, {
        label: ui.label.value,
        evidence: ui.evidence.value,
        note: ui.note.value,
      });
      changedSinceDownload = true;
      checkProgress();
      renderNavigation();
      refreshValidation();
    } catch (error) {
      inform(error.message || "Review edit was refused.", true);
    }
  }

  for (const control of [ui.label, ui.evidence, ui.note]) {
    control.addEventListener("input", editCurrent);
    control.addEventListener("change", editCurrent);
  }

  ui.previous.addEventListener("click", () => {
    if (!ready() || current <= 0) return;
    current -= 1;
    renderAll();
    ui.claim.focus();
  });
  ui.next.addEventListener("click", () => {
    if (!ready() || current >= rows.length - 1) return;
    current += 1;
    renderAll();
    ui.claim.focus();
  });

  ui.useSelection.addEventListener("click", () => {
    if (!ready()) return;
    const selection = window.getSelection();
    const chosen = selection ? selection.toString().trim() : "";
    if (!chosen || chosen.length < 8 || chosen.length > 1000
      || !core.sourceBodies(rows[current].cited_source_passages).some(
        (body) => body.includes(chosen)
      )) {
      inform("Select 8–1,000 exact characters inside a cited source passage.", true);
      return;
    }
    ui.evidence.value = chosen;
    editCurrent();
    ui.evidence.focus();
    inform("The selected passage was added as evidence. Choose a claim label too.", false);
  });

  ui.file.addEventListener("change", async () => {
    const file = ui.file.files && ui.file.files[0];
    if (!file || ready()) return;
    try {
      if (file.size > 12_000_000 || file.size <= 0) {
        throw new Error("Choose a nonempty C6 review CSV smaller than 12 MB.");
      }
      const selectedSlot = Number(ui.role.value);
      const selectedIdentity = ui.identity.value.trim();
      const parsed = core.validateWorksheet(
        await file.text(), selectedSlot, selectedIdentity
      );
      rows = parsed;
      slot = selectedSlot;
      identity = selectedIdentity;
      current = 0;
      changedSinceDownload = false;
      ui.role.disabled = true;
      ui.identity.disabled = true;
      ui.file.disabled = true;
      ui.workspace.hidden = false;
      ui.exportSection.hidden = false;
      ui.dataset.textContent = core.EXPECTED_DATASET;
      renderAll();
      ui.workspace.scrollIntoView({ behavior: "smooth", block: "start" });
      inform(
        "Loaded " + rows.length + " fictional claims for Reviewer "
          + (slot === 1 ? "A" : "B") + ". All work stays local.",
        false
      );
    } catch (error) {
      ui.file.value = "";
      inform(error.message || "The worksheet could not be imported.", true);
    }
  });

  ui.download.addEventListener("click", () => {
    if (!ready()) return;
    try {
      const status = core.progress(rows, slot, identity);
      const body = core.exportReviewerCsv(rows, slot, identity, {
        allowIncomplete: !status.complete,
      });
      const blob = new Blob(["\ufeff", body], {
        type: "text/csv;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "c6-reviewer-" + (slot === 1 ? "A" : "B") + "-"
        + (status.complete ? "completed" : "draft") + ".csv";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      changedSinceDownload = false;
      inform(status.complete
        ? "Private reviewer file exported. Python still validates every source fingerprint."
        : "Draft exported. Finish every claim before combining reviewer files.",
      false);
    } catch (error) {
      inform(error.message || "Export blocked by a validation error.", true);
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (!changedSinceDownload) return;
    event.preventDefault();
    event.returnValue = "";
  });
})();
