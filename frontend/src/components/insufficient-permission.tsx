"use client";

import { ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

interface InsufficientPermissionProps {
  /**
   * `page` fills the route segment (used by the route guard); `inline` renders a
   * compact card for a gated panel inside an otherwise accessible page.
   */
  variant?: "page" | "inline";
  title?: string;
  description?: string;
}

const DEFAULT_TITLE = "You don't have permission to view this page";
const DEFAULT_DESCRIPTION =
  "Your role in this organization doesn't include access to this area. If you think this is a mistake, contact an organization administrator.";

/**
 * Friendly, non-leaky denial for a capability the subject lacks.
 *
 * Distinct from `AuthorizationUnavailable`: authorization was verified, the
 * subject simply isn't permitted. The shell and navigation stay intact, and no
 * raw capability string or backend detail is shown.
 */
export default function InsufficientPermission({
  variant = "page",
  title = DEFAULT_TITLE,
  description = DEFAULT_DESCRIPTION,
}: InsufficientPermissionProps) {
  const router = useRouter();

  const card = (
    <div className="mx-auto w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50 text-amber-500">
        <ShieldAlert className="h-6 w-6" strokeWidth={1.7} />
      </div>

      <h2 className="mt-5 text-xl font-semibold tracking-[-0.025em] text-slate-950">
        {title}
      </h2>

      <p className="mt-3 text-sm leading-6 text-slate-500">{description}</p>
    </div>
  );

  if (variant === "inline") {
    return card;
  }

  return (
    <div className="min-h-screen">
      <div className="xl:pl-[230px]">
        <header className="fixed left-0 right-0 top-0 z-40 border-b border-slate-200/60 bg-white/70 backdrop-blur-xl xl:left-[230px]">
          <div className="mx-auto flex h-[74px] max-w-[1450px] items-center px-6 lg:px-10">
            <p className="text-sm font-semibold tracking-[-0.03em] text-slate-950">
              Access restricted
            </p>
          </div>
        </header>

        <main className="mx-auto max-w-[1450px] px-6 pb-16 pt-[112px] lg:px-10">
          {card}

          <div className="mx-auto mt-6 flex max-w-md flex-col gap-2 sm:flex-row sm:justify-center">
            <Link
              href="/dashboard"
              className="rounded-xl bg-slate-900 px-5 py-3 text-center text-sm font-medium text-white transition hover:bg-slate-800"
            >
              Go to dashboard
            </Link>
            <button
              type="button"
              onClick={() => router.back()}
              className="rounded-xl bg-slate-50 px-5 py-3 text-sm font-medium text-slate-600 transition hover:bg-slate-100 hover:text-slate-950"
            >
              Go back
            </button>
          </div>
        </main>
      </div>
    </div>
  );
}
