"use client";

import { useActionState } from "react";
import {
  requestStagingAgentSignInOtp, verifyStagingAgentSignInOtp,
} from "./actions";

export function AgentSignInForms() {
  const [requestMessage, requestAction, requesting] = useActionState(requestStagingAgentSignInOtp, null);
  const [verifyMessage, verifyAction, verifying] = useActionState(verifyStagingAgentSignInOtp, null);
  return <div className="mt-6 space-y-5">
    <form action={requestAction} className="rounded-2xl border border-violet-100 bg-violet-50 p-5">
      <h2 className="font-semibold">1. Request agent sign-in code</h2>
      <label className="mt-4 block text-sm font-medium" htmlFor="agent-private-setup">Private setup code</label>
      <input id="agent-private-setup" name="setup_code" required type="password" autoComplete="off"
        minLength={24} maxLength={128} placeholder="Code from your private setup email"
        className="mt-2 w-full rounded-xl border border-slate-300 bg-white p-3 text-slate-900" />
      {requestMessage && <p role="status" className="mt-3 text-sm text-slate-700">{requestMessage}</p>}
      <button type="submit" disabled={requesting}
        className="mt-4 w-full rounded-xl bg-violet-700 p-3 font-semibold text-white disabled:opacity-50">
        {requesting ? "Requesting code…" : "Send sign-in code"}
      </button>
    </form>
    <form action={verifyAction} className="rounded-2xl border border-slate-200 bg-white p-5">
      <h2 className="font-semibold">2. Sign in with your email code</h2>
      <label className="mt-4 block text-sm font-medium" htmlFor="agent-email-otp">Six-digit code</label>
      <input id="agent-email-otp" name="otp" required type="text" inputMode="numeric"
        pattern="[0-9]{6}" minLength={6} maxLength={6} autoComplete="one-time-code"
        placeholder="Enter private email code"
        className="mt-2 w-full rounded-xl border border-slate-300 bg-white p-3 tracking-widest text-slate-900" />
      {verifyMessage && <p role="alert" className="mt-3 text-sm text-red-700">{verifyMessage}</p>}
      <button type="submit" disabled={verifying}
        className="mt-4 w-full rounded-xl bg-slate-900 p-3 font-semibold text-white disabled:opacity-50">
        {verifying ? "Signing in…" : "Sign in to staging"}
      </button>
      <p className="mt-4 text-xs text-slate-500">Never share your setup code or email OTP in ChatGPT.</p>
    </form>
  </div>;
}
