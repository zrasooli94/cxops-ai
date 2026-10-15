import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { neonStagingEnabled, stagingIdentity, stagingSubjects, STAGING_BACKEND_URL } from "@/lib/staging/neon-policy";
import { stagingAuth } from "@/lib/staging/neon-server";
import { stagingSignOut } from "./actions";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Isolated staging | CXOps AI", robots: { index: false, follow: false } };

export default async function StagingPage() {
  const enabled = neonStagingEnabled(process.env);
  let identity = null;
  let unavailable = false;
  if (enabled) {
    try { identity = await stagingIdentity(stagingAuth(), stagingSubjects(process.env)); } catch { unavailable = true; }
  }
  if (enabled && !identity && !unavailable) redirect("/staging/login");
  let organizations: { id: number; name: string }[] = [];
  let backendStatus = "Not checked";
  if (identity) {
    try {
      const response = await fetch(`${STAGING_BACKEND_URL}/me/organizations`, {
        headers: { Authorization: `Bearer ${identity.token}` }, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw new Error("Staging API unavailable");
      const body: unknown = await response.json();
      if (!Array.isArray(body) || body.length > 100 || !body.every(x => x && Number.isSafeInteger(x.id) && typeof x.name === "string")) throw new Error("Invalid staging response");
      organizations = body.map(x => ({ id: x.id, name: x.name }));
      backendStatus = "Identity verified by staging API";
    } catch { backendStatus = "Staging API unavailable — no workspace data loaded"; }
  }
  return <main className="min-h-screen bg-slate-50 px-5 py-16 text-slate-950"><section className="mx-auto max-w-2xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
    <p className="text-xs font-bold uppercase tracking-widest text-violet-700">CXOps AI · Isolated staging</p>
    <h1 className="mt-4 text-3xl font-semibold">Your staging workspace</h1>
    <p className="mt-3 text-sm leading-7 text-slate-600">Synthetic test data only. This workspace supports read-only verification.</p>
    {!enabled ? <div className="mt-6 rounded-2xl bg-amber-50 p-5"><h2 className="font-semibold">Staging is being connected</h2><p className="mt-2 text-sm leading-6">Sign-in will open after the isolated backend, test accounts and tenant access checks pass.</p></div> : identity ? <>
      <div className="mt-6 rounded-2xl bg-violet-50 p-5"><h2 className="font-semibold">Welcome, {identity.user.name}</h2><p className="mt-2 text-sm">{backendStatus}</p></div>
      <h2 className="mt-6 font-semibold">Test workspaces</h2>
      {organizations.length ? <ul className="mt-3 space-y-2">{organizations.map(org => <li key={org.id} className="rounded-xl border border-slate-200 p-4">{org.name}</li>)}</ul> : <p className="mt-3 text-sm text-slate-600">No verified workspace memberships loaded.</p>}
      {backendStatus === "Identity verified by staging API" && organizations.length === 1 &&
        <Link href="/staging/operations" className="mt-6 flex items-center justify-between rounded-2xl border border-violet-200 bg-violet-600 p-5 text-white shadow-sm transition hover:bg-violet-700">
          <span><strong className="block text-base">Open staging Control Center</strong>
            <span className="mt-1 block text-xs text-violet-100">Explore synthetic tickets, customers and real queue metrics</span>
          </span>
          <span aria-hidden="true" className="text-2xl">→</span>
        </Link>}
      <form action={stagingSignOut}><button className="mt-6 rounded-xl border border-slate-300 px-5 py-3 text-sm font-semibold">Sign out</button></form>
    </> : <p role="alert" className="mt-6 text-sm text-red-700">Staging authentication is temporarily unavailable. Please try again.</p>}
    <Link href="/login" className="mt-8 inline-block text-sm text-violet-700">Back to sign-in information →</Link>
  </section></main>;
}
