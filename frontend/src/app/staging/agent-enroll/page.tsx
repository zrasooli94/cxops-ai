import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { stagingAgentEnrollmentTarget, stagingAgentEnrollmentViewer } from "@/lib/staging/agent-enrollment";
import {
  neonStagingEnabled, stagingIdentity, stagingSubjects,
} from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";
import { AgentVerificationForms } from "./verification-forms";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Staging Agent Verification | CXOps AI",
  robots: { index: false, follow: false },
};

export default async function StagingAgentEnrollmentPage() {
  if (!neonStagingEnabled(process.env) || !stagingAgentEnrollmentTarget(process.env)) {
    return <main className="min-h-screen bg-slate-50 p-8 text-slate-900">
      <div className="mx-auto max-w-xl rounded-2xl bg-white p-8">
        <h1 className="text-2xl font-semibold">Agent verification is not yet enabled</h1>
        <p className="mt-3 text-sm text-slate-600">This staging-only feature is awaiting isolation and code verification.</p>
        <Link href="/staging" className="mt-5 inline-block text-sm font-semibold text-violet-700">Back to staging</Link>
      </div>
    </main>;
  }

  let allowedViewer = false;
  try {
    const subjectList = stagingSubjects(process.env);
    const identity = await stagingIdentity(stagingAuth(), subjectList);
    allowedViewer = stagingAgentEnrollmentViewer(identity?.user ?? null, subjectList);
  } catch {
    allowedViewer = false;
  }
  if (!allowedViewer) redirect("/staging/login");

  return <main className="min-h-screen bg-[#F7F8FC] px-5 py-14 text-slate-900">
    <div className="mx-auto max-w-xl rounded-3xl border border-slate-200 bg-white p-7 shadow-sm sm:p-10">
      <p className="text-xs font-bold uppercase tracking-widest text-violet-700">CXOps AI · Isolated Staging</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight">Verify your staging agent email</h1>
      <p className="mt-4 text-sm leading-7 text-slate-600">
        The verification code is issued by Neon Managed Auth to the Gmail address you provided.
        This account is separate from the existing synthetic Alpha and Beta viewers.
      </p>
      <AgentVerificationForms />
      <Link href="/staging" className="mt-8 inline-block text-sm font-semibold text-violet-700 hover:underline">
        Back to read-only staging
      </Link>
    </div>
  </main>;
}
