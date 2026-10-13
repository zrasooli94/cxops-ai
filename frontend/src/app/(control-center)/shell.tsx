"use client";

import { Building2, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import NavigationIcon from "@/components/navigation-icon";
import OrganizationSwitcher from "@/components/organization-switcher";
import { useAuthorization } from "@/lib/authorization/context";
import {
  BUSINESS_NAVIGATION,
  filterNavigationByCapabilities,
  PRIMARY_NAVIGATION,
  type NavigationItem,
} from "@/lib/authorization/navigation";
import { lifecycleInfo } from "@/lib/commercial/onboarding";
import { switchOrganization } from "@/lib/tenant/actions";

interface Organization {
  id: number;
  name: string;
  industry: string | null;
}

interface ControlCenterShellProps {
  session: {
    displayName: string | null;
    email: string | null;
  };
  tenantContext: {
    activeOrganization: Organization;
    memberships: Organization[];
  };
  children: React.ReactNode;
}

export default function ControlCenterShell({
  session,
  tenantContext,
  children,
}: ControlCenterShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { can, organizationStatus } = useAuthorization();
  const businessNavItems = filterNavigationByCapabilities(BUSINESS_NAVIGATION, can);
  const visibleNavItems = filterNavigationByCapabilities(PRIMARY_NAVIGATION, can);
  const lifecycle = lifecycleInfo(organizationStatus);
  const mobileNavItems: NavigationItem[] = [
    ...businessNavItems,
    ...visibleNavItems.filter((item) =>
      ["/dashboard", "/inbox", "/public-chat", "/knowledge"].includes(item.href),
    ),
  ];

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-50 hidden w-[230px] border-r border-slate-200/70 bg-white/80 px-5 py-7 backdrop-blur-2xl xl:flex xl:flex-col">
        <Link href="/dashboard" className="flex items-center gap-3 px-2">
          <div className="grid h-9 w-9 grid-cols-3 gap-[3px]">
            {Array.from({ length: 9 }).map((_, index) => (
              <span
                key={index}
                className={`rounded-full ${
                  index % 2 === 0
                    ? "bg-[#7357ff]"
                    : "bg-[#42a5ff]"
                }`}
              />
            ))}
          </div>

          <div>
            <p className="text-[15px] font-semibold tracking-[-0.03em] text-slate-950">
              CXOps AI
            </p>
          </div>
        </Link>

        {organizationStatus !== "active" ? (
          <div
            role="status"
            className={`mt-8 rounded-xl border px-3 py-2.5 text-xs leading-5 ${
              lifecycle.tone === "bad"
                ? "border-rose-200 bg-rose-50 text-rose-800"
                : "border-amber-200 bg-amber-50 text-amber-900"
            }`}
          >
            <p className="font-semibold">{lifecycle.label}</p>
            <p>{lifecycle.detail}</p>
            {organizationStatus === "onboarding" ? (
              <Link href="/setup" className="mt-1 inline-block font-medium underline underline-offset-2">
                Continue setup
              </Link>
            ) : null}
          </div>
        ) : null}

        <nav aria-label="Your business" className="mt-8 space-y-1">
          <p className="px-3.5 pb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
            Your business
          </p>
          {businessNavItems.map((item) => (
            <SidebarLink key={item.href} item={item} selected={pathname === item.href} />
          ))}
        </nav>

        <nav aria-label="Operations" className="mt-6 min-h-0 flex-1 space-y-1.5 overflow-y-auto pb-4">
          <p className="px-3.5 pb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
            Operations
          </p>
          {visibleNavItems.map((item) => {
            const selected = pathname === item.href;

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`group flex items-center gap-3 rounded-xl px-3.5 py-3 text-sm transition-all duration-300 ${
                  selected
                    ? "bg-gradient-to-r from-[#eef4ff] to-[#f5f1ff] text-[#4d49d8]"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
                }`}
              >
                <NavigationIcon
                  name={item.icon}
                  className={`h-[17px] w-[17px] ${
                    selected
                      ? "text-[#5f63ff]"
                      : "text-slate-500 group-hover:text-slate-800"
                  }`}
                  strokeWidth={1.7}
                />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-3 space-y-3 rounded-2xl border border-slate-200/80 bg-white/85 p-4 shadow-[0_10px_35px_rgba(79,90,130,0.05)]">
          {tenantContext.memberships.length > 1 ? (
            <OrganizationSwitcher
              activeOrganization={tenantContext.activeOrganization}
              memberships={tenantContext.memberships}
              onSwitch={switchOrganization}
            />
          ) : (
            <div className="flex items-center gap-2.5 rounded-xl border border-slate-200/80 bg-white/85 px-3 py-2.5">
              <Building2 className="h-4 w-4 text-violet-500" strokeWidth={1.7} />
              <div className="min-w-0 flex-1">
                <p className="text-[10px] text-slate-400">Organization</p>
                <p className="truncate text-xs font-medium text-slate-800">
                  {tenantContext.activeOrganization.name}
                </p>
              </div>
            </div>
          )}

          <div className="flex items-center gap-2.5">
            <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.7)]" />

            <div className="flex-1 min-w-0">
              <p className="text-[11px] text-slate-400 truncate">
                {session.email ?? session.displayName ?? "Signed in"}
              </p>

              <p className="text-xs font-medium text-slate-800 truncate">
                Control Center
              </p>
            </div>
          </div>

          <button
            className="w-full flex items-center gap-3 rounded-xl bg-slate-50 px-3.5 py-3 text-sm text-slate-600 transition hover:bg-slate-100 hover:text-slate-950"
            onClick={async () => {
              const { logout } = await import("@/lib/session/logout");
              await logout();
              router.push("/login");
            }}
          >
            <LogOut className="h-[17px] w-[17px] text-slate-400" strokeWidth={1.7} />
            Sign out
          </button>
        </div>
      </aside>

      {/* Mobile tenant / account bar */}
      <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-slate-200/80 bg-white/95 px-4 py-3 backdrop-blur-xl xl:hidden">
        {organizationStatus !== "active" ? (
          <p role="status" className="mx-auto mb-2 max-w-lg truncate text-xs text-amber-800">
            <span className="font-semibold">{lifecycle.label}.</span>{" "}
            {organizationStatus === "onboarding" ? (
              <Link href="/setup" className="underline underline-offset-2">
                Continue setup
              </Link>
            ) : (
              lifecycle.detail
            )}
          </p>
        ) : null}
        {/* Mobile navigation: the sidebar is desktop-only, and every page owns
            a fixed top header, so navigation lives in this bottom bar. */}
        <nav aria-label="Workspace" className="mx-auto mb-2 flex max-w-lg gap-1.5 overflow-x-auto [scrollbar-width:none]">
          {mobileNavItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={pathname === item.href ? "page" : undefined}
              className={`flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium ${
                pathname === item.href ? "bg-slate-950 text-white" : "bg-slate-100 text-slate-700"
              }`}
            >
              <NavigationIcon name={item.icon} className="h-3.5 w-3.5" strokeWidth={1.8} />
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="mx-auto flex max-w-lg items-center gap-3">
          {tenantContext.memberships.length > 1 ? (
            <div className="flex-1">
              <OrganizationSwitcher
                activeOrganization={tenantContext.activeOrganization}
                memberships={tenantContext.memberships}
                onSwitch={switchOrganization}
              />
            </div>
          ) : (
            <div className="flex flex-1 items-center gap-2.5 rounded-xl border border-slate-200/80 bg-slate-50 px-3 py-2">
              <Building2 className="h-4 w-4 text-violet-500" strokeWidth={1.7} />
              <div className="min-w-0 flex-1">
                <p className="text-[10px] text-slate-400">Organization</p>
                <p className="truncate text-xs font-medium text-slate-800">
                  {tenantContext.activeOrganization.name}
                </p>
              </div>
            </div>
          )}

          <button
            aria-label="Sign out"
            className="flex items-center justify-center rounded-xl bg-slate-50 p-2.5 text-slate-600 transition hover:bg-slate-100 hover:text-slate-950"
            onClick={async () => {
              const { logout } = await import("@/lib/session/logout");
              await logout();
              router.push("/login");
            }}
          >
            <LogOut className="h-[17px] w-[17px] text-slate-400" strokeWidth={1.7} />
          </button>
        </div>
      </div>

      <div className="pb-36 xl:pb-0 xl:pl-[230px]">
        {children}
      </div>
    </div>
  );
}

function SidebarLink({ item, selected }: { item: NavigationItem; selected: boolean }) {
  return (
    <Link
      href={item.href}
      aria-current={selected ? "page" : undefined}
      className={`group flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm transition-all duration-300 ${
        selected
          ? "bg-gradient-to-r from-[#eef4ff] to-[#f5f1ff] text-[#4d49d8]"
          : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
      }`}
    >
      <NavigationIcon
        name={item.icon}
        className={`h-[17px] w-[17px] ${
          selected ? "text-[#5f63ff]" : "text-slate-500 group-hover:text-slate-800"
        }`}
        strokeWidth={1.7}
      />
      {item.label}
    </Link>
  );
}
