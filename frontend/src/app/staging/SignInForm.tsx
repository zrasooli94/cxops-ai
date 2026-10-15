"use client";
import { useActionState } from "react";
import { stagingDemoSignIn, stagingSignIn } from "./actions";

export function StagingDemoForm() {
  const [error, action, pending] = useActionState(stagingDemoSignIn, null);
  return <form action={action} className="mt-6 rounded-2xl bg-violet-50 p-5">
    <p className="text-sm leading-6 text-slate-700">Explore the read-only workspace with a fictional test account.</p>
    {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    <button type="submit" disabled={pending} className="mt-4 w-full rounded-xl bg-violet-700 p-3 font-semibold text-white disabled:opacity-60">{pending ? "Opening workspace…" : "Open synthetic workspace"}</button>
  </form>;
}

export function StagingSignInForm() {
  const [error, action, pending] = useActionState(stagingSignIn, null);
  return <form action={action} className="mt-6 grid gap-4">
    <label className="grid gap-2 text-sm font-medium">Staging email<input name="email" type="email" autoComplete="username" required className="rounded-xl border border-slate-300 bg-white p-3 text-slate-950" /></label>
    <label className="grid gap-2 text-sm font-medium">Password<input name="password" type="password" autoComplete="current-password" required className="rounded-xl border border-slate-300 bg-white p-3 text-slate-950" /></label>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    <button type="submit" disabled={pending} className="rounded-xl bg-violet-700 p-3 font-semibold text-white disabled:opacity-60">{pending ? "Signing in…" : "Sign in to staging"}</button>
  </form>;
}
