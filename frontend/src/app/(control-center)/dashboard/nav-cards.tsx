"use client";

import Link from "next/link";

import NavigationIcon from "@/components/navigation-icon";
import { useAuthorization } from "@/lib/authorization/context";
import {
  DASHBOARD_NAVIGATION,
  filterNavigationByCapabilities,
} from "@/lib/authorization/navigation";

/**
 * Capability-aware quick-link cards.
 *
 * Kept as a small client component so the dashboard page itself remains a
 * Server Component; visibility uses the same capability predicate as the shell.
 */
export default function DashboardNavCards() {
  const { can } = useAuthorization();
  const cards = filterNavigationByCapabilities(DASHBOARD_NAVIGATION, can);

  if (cards.length === 0) {
    return (
      <section className="mb-12 rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
        No workspaces are available with your current access.
      </section>
    );
  }

  return (
    <section className="mb-12 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
      {cards.map((card) => (
        <Link
          key={card.href}
          href={card.href}
          className="rounded-2xl border border-slate-200 bg-white p-6 transition hover:border-violet-300 hover:-translate-y-0.5 hover:shadow-lg"
        >
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-50 text-violet-500">
              <NavigationIcon name={card.icon} className="h-5 w-5" />
            </div>
            <div>
              <h3 className="font-semibold text-slate-950">{card.label}</h3>
              <p className="text-xs text-slate-400">{card.description}</p>
            </div>
          </div>

          <div className="mt-5 text-sm font-medium text-violet-600">
            Open workspace <span aria-hidden="true">→</span>
          </div>
        </Link>
      ))}
    </section>
  );
}
