import type { Metadata } from "next";
import Link from "next/link";
import { stagingAgentSignInTarget } from "@/lib/staging/agent-enrollment";
import { neonStagingEnabled } from "@/lib/staging/neon-policy";
import { AgentSignInForms } from "./forms";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Synthetic Agent Sign-in | CXOps AI",
  robots: { index: false, follow: false },
};

export default function AgentSignInPage() {
  const enabled = neonStagingEnabled(process.env) && Boolean(stagingAgentSignInTarget(process.env));
  return <main className="min-h-screen bg-slate-50 px-5 py-14 text-slate-950">
    <section className="mx-auto max-w-lg rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
      <p className="text-xs font-bold uppercase tracking-widest text-violet-700">CXOps AI · Isolated Staging</p>
      <h1 className="mt-3 text-2xl font-semibold">Verified synthetic agent sign-in</h1>
      {enabled ? <>
        <p className="mt-3 text-sm leading-6 text-slate-600">
          Use your private staging setup code to request an email one-time sign-in code.
          This does not independently grant ticket permissions.
        </p>
        <AgentSignInForms />
      </> : <p className="mt-5 rounded-xl bg-amber-50 p-4 text-sm text-amber-900">
        Agent sign-in is awaiting independent verification and staging-only release.
      </p>}
      <Link href="/staging/login" className="mt-7 inline-block text-sm font-semibold text-violet-700">
        Back to staging login
      </Link>
    </section>
  </main>;
}
