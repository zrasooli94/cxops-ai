import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { LoginForm } from "./LoginForm";
import { readControlCenterSession } from "@/lib/auth/control-center";
import { loginPageDecision } from "@/lib/session/read";
import { previewGateFromEnvironment } from "@/lib/staging/preview-isolation";

export const metadata: Metadata = {
  title: "Sign in | CXOps AI",
  description: "Sign in to CXOps AI Control Center",
  robots: {
    index: false,
    follow: false,
  },
};

export default async function LoginPage() {
  // Preview must never collect credentials before an independently verified
  // staging identity AND isolated backend have been provisioned.
  if (previewGateFromEnvironment() === "blocked") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-violet-50 px-5 py-16">
        <section className="w-full max-w-xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm sm:p-12">
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-violet-700">CXOps AI · Preview environment</p>
          <h1 className="mt-4 text-3xl font-semibold tracking-tight text-slate-950">Staff sign-in is not enabled on this preview</h1>
          <p className="mt-4 text-sm leading-7 text-slate-600">
            This preview does not yet have a separate test identity and isolated backend.
            Production credentials are intentionally not accepted here. Your existing
            CXOps AI account is unchanged.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="https://cxops-ocds9ldjz-zrasooli95.vercel.app/demo/handoff"
              className="inline-flex items-center rounded-xl bg-violet-700 px-5 py-3 text-sm font-semibold text-white hover:bg-violet-800">
              Open interactive handoff demo →
            </Link>
            <Link href="https://cxops-ai.vercel.app/login"
              className="inline-flex items-center rounded-xl border border-slate-200 px-5 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50">
              Sign in to existing production app
            </Link>
          </div>
          <p className="mt-6 text-xs text-slate-500">The demo uses fictional data and never connects to your real backend.</p>
        </section>
      </main>
    );
  }

  const { state } = await readControlCenterSession();
  const decision = loginPageDecision(state);

  // A valid session is forwarded through the landing Route Handler, which owns
  // the post-auth destination decision (memberships / active organization).
  if (decision === "forward") {
    redirect("/api/auth/landing");
  }

  // A stale (expired) session must be recovered through a MUTABLE context
  // (Route Handler): refreshing or clearing cookies is not supported during
  // Server Component rendering and would crash the page.
  if (decision === "recover") {
    redirect("/api/auth/session");
  }

  return <LoginForm />;
}