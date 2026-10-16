"use client";

import { useActionState } from "react";

import {
  confirmAgentVerificationCode, sendAgentVerificationCode,
} from "./actions";

export function AgentVerificationForms() {
  const [sendResult, sendAction, sending] = useActionState(sendAgentVerificationCode, null);
  const [verifyResult, verifyAction, verifying] = useActionState(confirmAgentVerificationCode, null);
  const complete = verifyResult?.outcome === "verified";

  return <div className="mt-7 space-y-5">
    <form action={sendAction} className="rounded-2xl border border-slate-200 bg-slate-50 p-5">
      <h2 className="text-base font-semibold text-slate-950">1. Request your verification code</h2>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        Neon will email a six-digit code to the private staging-agent address you provided.
        No code is sent until you press the button.
      </p>
      {sendResult && <p role="status" className={`mt-3 text-sm ${sendResult.outcome === "error" ? "text-red-700" : "text-emerald-700"}`}>{sendResult.message}</p>}
      <button type="submit" disabled={sending || complete} className="mt-4 w-full rounded-xl bg-violet-700 px-4 py-3 text-sm font-semibold text-white hover:bg-violet-800 disabled:cursor-not-allowed disabled:opacity-60">
        {sending ? "Requesting code…" : "Send verification code"}
      </button>
    </form>

    <form action={verifyAction} className="rounded-2xl border border-slate-200 bg-white p-5">
      <h2 className="text-base font-semibold text-slate-950">2. Verify your email</h2>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        Enter the code from your email here. Don't share it in chat or screenshots.
      </p>
      <label htmlFor="staging-agent-otp" className="mt-4 block text-sm font-medium text-slate-700">Six-digit code</label>
      <input id="staging-agent-otp" name="otp" type="text" inputMode="numeric" pattern="[0-9]{6}" autoComplete="one-time-code" maxLength={6} required disabled={complete}
        placeholder="••••••"
        className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-base tracking-widest text-slate-900 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100 disabled:opacity-40" />
      {verifyResult && <p role="status" className={`mt-3 text-sm ${complete ? "text-emerald-700" : "text-red-700"}`}>{verifyResult.message}</p>}
      <button type="submit" disabled={verifying || complete} className="mt-4 w-full rounded-xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60">
        {complete ? "Verification accepted" : verifying ? "Verifying…" : "Verify email"}
      </button>
    </form>
    <p className="text-xs leading-5 text-slate-500">
      Email verification does not grant access to tickets. Your existing test viewer remains read-only.
      A separate review is required before a staging agent receives permissions.
    </p>
  </div>;
}
