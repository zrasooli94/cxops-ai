"use client";
import { useActionState } from "react";
import { stagingSignIn } from "./actions";

export function StagingSignInForm() {
  const [error, action, pending] = useActionState(stagingSignIn, null);
  return <form action={action} className="mt-6 grid gap-4">
    <label className="grid gap-2 text-sm font-medium">Staging email<input name="email" type="email" autoComplete="username" required className="rounded-xl border border-slate-300 bg-white p-3 text-slate-950" /></label>
    <label className="grid gap-2 text-sm font-medium">Password<input name="password" type="password" autoComplete="current-password" required className="rounded-xl border border-slate-300 bg-white p-3 text-slate-950" /></label>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    <button type="submit" disabled={pending} className="rounded-xl bg-violet-700 p-3 font-semibold text-white disabled:opacity-60">{pending ? "Signing in…" : "Sign in to staging"}</button>
  </form>;
}
