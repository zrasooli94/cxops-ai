# Service Transformation Simulation — Operator Guide

This guide explains how to use the Service Transformation Simulation workspace
(`/simulations`) in the control center. The workspace models deterministic
**what-if scenarios** over your observed service data. Everything here is
presented exactly as the simulation engine computed it — the frontend never
recalculates projections, ROI, or rates.

> Scenario projections are deterministic estimates, not forecasts or guaranteed
> outcomes.

## 1. Access

- **View only** — requires `transformation.simulation.read` (supervisor and
  above include it).
- **Manage** — requires `transformation.simulation.manage`. Creating,
  evaluating/re-evaluating, and archiving scenarios are manage-only actions. A
  read-only session simply sees no management buttons; the backend enforces the
  same boundary on every request.

## 2. Create a scenario

1. Open **Simulations** in the sidebar and choose **Create scenario**.
2. Give the scenario a name and an optional description.
3. Pick the **observed window**: 7, 30, or 90 days. The engine snapshots that
   window's Phase 1L transformation summary at evaluate time.
4. Enable at least one **assumption** and set its target as a 0–100 percentage:
   - **Autonomous execution rate target** — % of observed agent runs executed
     autonomously. Eligibility is not modelled.
   - **Human approval rate target** — % of observed runs requiring human
     approval (independent demand, not a partition of outcomes).
   - **Knowledge usage rate target** — % of observed runs routed to the
     knowledge specialist (usage only, not resolution quality).
   - **Reopen rate target** — target reopen % of observed resolved tickets
     (Phase 1L reopen denominator).
   - **SLA breach reduction** — arithmetic % reduction of observed breaches
     (no process or policy change is modelled).
5. A disabled assumption is simply **not modelled** — the projection equals the
   observed value for that dimension.

## 3. Evaluate

**Evaluate** captures the observed baseline snapshot and computes projections
(and, when measurable, value/ROI) using the engine's persisted formula version.
**Re-evaluate** refreshes the snapshot on the same row with the same
assumptions. Archived scenarios cannot be re-evaluated.

## 4. Read the detail view

The detail view keeps three things visually distinct:

- **Observed baseline** — what actually happened in the window.
- **Assumptions** — what you asked the engine to model; unmodelled dimensions
  are labelled "Not assumed — unchanged / not modelled", never zeroed.
- **Projected result** — the deterministic engine output, with per-metric
  observed → projected deltas.

**Value & ROI** rows are labelled with the projected qualifier
(e.g. "Estimated net savings (projected)") and only display when the observed
baseline was measurable. If pricing is not configured or the autonomous sample
is insufficient, ROI renders `—` with the engine's explanation — it is
unavailable, not zero.

The **Model limitations** panel restates the engine's modelling constraints.
They are surfaced, never silenced, because they are material to a decision.

## 5. Compare scenarios

Select **2–4** scenarios (choose **Compare** on each card, then **Compare (N)**)
to open `/simulations/compare?ids=…`. The matrix shows observed, projected, and
presentation deltas per metric per scenario.

Comparison caveats are shown verbatim when they apply:

- Scenarios using different observed windows are **not directly comparable**.
- Scenarios evaluated with different formula versions are flagged.
- Unevaluated scenarios are flagged.

The comparison never ranks or recommends a scenario — the decision stays with
you.

## 6. Executive summary and print

The **Executive summary** panel is a deterministic snapshot (observed,
assumptions, projected, value, ROI, limitations) built from the persisted row —
no AI is involved. **Print** exports the current view (detail or comparison)
through the browser's print dialog using CSS print rules: headers, sidebar, and
controls are hidden; content only.

## 7. What the workspace never does

- Never mutates live tickets, agent runs, SLA policies, or authorization rules.
- Never forecasts, runs Monte Carlo, or predicts employee/customer outcomes.
- Never writes to the backend except through the documented scenario endpoints
  (create / evaluate / archive).