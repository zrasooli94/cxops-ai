"use client";

import { Building2, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import NavigationIcon from "@/components/navigation-icon";
import OrganizationSwitcher from "@/components/organization-switcher";
import { useAuthorization } from "@/lib/authorization/context";
import {
  filterNavigationByCapabilities,
  PRIMARY_NAVIGATION,
} from "@/lib/authorization/navigation";
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
  const { can } = useAuthorization();
  const visibleNavItems = filterNavigationByCapabilities(PRIMARY_NAVIGATION, can);

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

        <nav className="mt-12 space-y-1.5">
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

        <div className="mt-auto space-y-3 rounded-2xl border border-slate-200/80 bg-white/85 p-4 shadow-[0_10px_35px_rgba(79,90,130,0.05)]">
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

      <div className="pb-20 xl:pb-0 xl:pl-[230px]">
        {children}
      </div>
    </div>
  );
}
