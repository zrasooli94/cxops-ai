import { Metadata } from "next";
import Link from "next/link";
import { Building2 } from "lucide-react";
import { requireControlCenterAuth } from "@/lib/auth/control-center";

export const metadata: Metadata = {
  title: "No organization access | CXOps AI",
  robots: {
    index: false,
    follow: false,
  },
};

export default async function NoOrganizationPage() {
  await requireControlCenterAuth();

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50 flex items-center justify-center p-6">
      <div className="w-full max-w-md text-center">
        <div className="mb-6 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50 text-violet-600">
          <Building2 className="h-6 w-6" strokeWidth={1.7} />
        </div>

        <h1 className="text-2xl font-light tracking-[-0.03em] text-slate-950">
          No organization access
        </h1>
        <p className="mt-3 text-sm leading-6 text-slate-500">
          Your account is not assigned to any organization workspace yet.
          Contact your administrator to request access.
        </p>

        <div className="mt-8 space-y-3">
          <Link
            href="/select-organization"
            className="block w-full rounded-full bg-[#111827] px-5 py-3.5 text-sm font-medium text-white shadow-[0_12px_30px_rgba(17,24,39,0.15)] transition hover:-translate-y-0.5 hover:bg-gradient-to-r hover:from-[#765cff] hover:to-[#508cff]"
          >
            Check again
          </Link>
          <form
            action="/login"
            method="get"
            className="block"
          >
            <button
              type="submit"
              className="w-full rounded-full border border-slate-200 bg-white px-5 py-3.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            >
              Sign out
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}
