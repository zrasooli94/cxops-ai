"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { Building2, Check, ChevronsUpDown } from "lucide-react";

interface Organization {
  id: number;
  name: string;
  industry: string | null;
}

interface OrganizationSwitcherProps {
  activeOrganization: Organization;
  memberships: Organization[];
  onSwitch: (organizationId: number) => Promise<unknown>;
}

export default function OrganizationSwitcher({
  activeOrganization,
  memberships,
  onSwitch,
}: OrganizationSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [isPending, startTransition] = useTransition();
  const containerRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const focusIndexRef = useRef(0);

  const handleSwitch = useCallback(
    (id: number) => {
      if (id === activeOrganization.id || isPending) {
        setOpen(false);
        return;
      }
      startTransition(async () => {
        setOpen(false);
        await onSwitch(id);
      });
    },
    [activeOrganization.id, isPending, onSwitch],
  );

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (!open) return;

      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }

      if (memberships.length === 0) return;

      if (event.key === "ArrowDown") {
        event.preventDefault();
        const next =
          focusIndexRef.current >= memberships.length - 1
            ? 0
            : focusIndexRef.current + 1;
        focusIndexRef.current = next;
        itemRefs.current[next]?.focus();
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        const next =
          focusIndexRef.current <= 0
            ? memberships.length - 1
            : focusIndexRef.current - 1;
        focusIndexRef.current = next;
        itemRefs.current[next]?.focus();
        return;
      }

      if (event.key === "Home") {
        event.preventDefault();
        focusIndexRef.current = 0;
        itemRefs.current[0]?.focus();
        return;
      }

      if (event.key === "End") {
        event.preventDefault();
        const last = memberships.length - 1;
        focusIndexRef.current = last;
        itemRefs.current[last]?.focus();
      }
    }

    function handleClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    }

    if (open) {
      document.addEventListener("keydown", handleKeyDown);
      document.addEventListener("mousedown", handleClickOutside);
      return () => {
        document.removeEventListener("keydown", handleKeyDown);
        document.removeEventListener("mousedown", handleClickOutside);
      };
    }
  }, [open, memberships.length]);

  useEffect(() => {
    if (open) {
      const activeIndex = memberships.findIndex(
        (m) => m.id === activeOrganization.id,
      );
      const initialIndex = activeIndex >= 0 ? activeIndex : 0;
      focusIndexRef.current = initialIndex;
      itemRefs.current[initialIndex]?.focus();
    }
  }, [open, memberships, activeOrganization.id]);

  const activeLabel = activeOrganization.name;

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Current organization: ${activeLabel}. Switch organization`}
        className="w-full flex items-center gap-2.5 rounded-xl border border-slate-200/80 bg-white/85 px-3 py-2.5 text-left shadow-[0_10px_35px_rgba(79,90,130,0.05)] transition hover:bg-white"
      >
        <Building2 className="h-4 w-4 shrink-0 text-violet-500" strokeWidth={1.7} />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] text-slate-400">Organization</p>
          <p className="truncate text-xs font-medium text-slate-800">
            {activeLabel}
          </p>
        </div>
        <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Switch organization"
          className="absolute bottom-full left-0 z-50 mb-2 w-full overflow-hidden rounded-2xl border border-slate-200/90 bg-white p-1.5 shadow-[0_20px_50px_rgba(79,90,130,0.12)]"
        >
          <p className="px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.13em] text-slate-400">
            Switch organization
          </p>
          <ul className="space-y-0.5">
            {memberships.map((org, index) => {
              const selected = org.id === activeOrganization.id;
              return (
                <li key={org.id} role="none">
                  <button
                    type="button"
                    role="menuitem"
                    ref={(el) => {
                      itemRefs.current[index] = el;
                    }}
                    tabIndex={-1}
                    disabled={isPending || selected}
                    onClick={() => handleSwitch(org.id)}
                    className={`flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm transition ${
                      selected
                        ? "bg-violet-50 text-violet-700"
                        : "text-slate-700 hover:bg-slate-50"
                    } disabled:opacity-60`}
                  >
                    <span className="min-w-0 flex-1 truncate">{org.name}</span>
                    {selected && (
                      <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
