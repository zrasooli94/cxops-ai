import Link from "next/link";
import { neonStagingEnabled } from "@/lib/staging/neon-policy";
import { StagingDemoForm, StagingSignInForm } from "../SignInForm";

export const dynamic = "force-dynamic";
export default function StagingLogin() {
  return <main className="min-h-screen bg-slate-50 px-5 py-16 text-slate-950"><section className="mx-auto max-w-md rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
    <p className="text-xs font-bold uppercase tracking-widest text-violet-700">CXOps AI · Isolated staging</p>
    <h1 className="mt-4 text-3xl font-semibold">Staging sign-in</h1>
    <p className="mt-3 text-sm leading-7 text-slate-600">Use an enrolled synthetic test account.</p>
    {neonStagingEnabled(process.env) ? <>
      {process.env.CXOPS_STAGING_DEMO_EMAIL === "phase1w-alpha@example.test" && process.env.CXOPS_STAGING_DEMO_PASSWORD ? <StagingDemoForm /> : null}
      <details className="mt-6"><summary className="cursor-pointer text-sm text-slate-600">Sign in with another enrolled test account</summary><StagingSignInForm /></details>
    </> : <p className="mt-6 rounded-xl bg-amber-50 p-4 text-sm">Staging is being connected. Sign-in is not enabled yet.</p>}
    <Link href="/staging" className="mt-6 inline-block text-sm text-violet-700">Back to staging →</Link>
  </section></main>;
}
