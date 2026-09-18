import Link from "next/link";
import { ShieldAlert } from "lucide-react";

/**
 * Fail-closed control-center state shown when authorization could not be
 * verified (malformed, mismatched, or unavailable). It deliberately renders no
 * shell, navigation, or page content, and never fabricates a role/capability
 * set. Raw backend details are not surfaced.
 */
export default function AuthorizationUnavailable() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-blue-50 px-6">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50 text-amber-500">
          <ShieldAlert className="h-6 w-6" strokeWidth={1.7} />
        </div>

        <h1 className="mt-5 text-2xl font-semibold tracking-[-0.025em] text-slate-950">
          We couldn&apos;t verify your access
        </h1>

        <p className="mt-3 text-sm leading-6 text-slate-500">
          Your permissions for this organization could not be confirmed. Please
          try again, or choose a different organization.
        </p>

        <div className="mt-7 flex flex-col gap-2">
          <Link
            href="/dashboard"
            className="rounded-xl bg-slate-900 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800"
          >
            Try again
          </Link>
          <Link
            href="/select-organization"
            className="rounded-xl bg-slate-50 px-4 py-3 text-sm font-medium text-slate-600 transition hover:bg-slate-100 hover:text-slate-950"
          >
            Switch organization
          </Link>
        </div>
      </div>
    </main>
  );
}
