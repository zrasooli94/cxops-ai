import type { ReactNode } from "react";

import {
  type SLAState,
} from "./types.ts";

export type BadgeVariant = "default" | "success" | "warning" | "danger" | "info";

export function Badge({
  children,
  variant = "default",
}: {
  children: ReactNode;
  variant?: BadgeVariant;
}) {
  const styles: Record<BadgeVariant, string> = {
    default: "border-slate-200 bg-slate-50 text-slate-600",
    success: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning: "border-amber-200 bg-amber-50 text-amber-700",
    danger: "border-rose-200 bg-rose-50 text-rose-700",
    info: "border-blue-200 bg-blue-50 text-blue-700",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${styles[variant]}`}
    >
      {children}
    </span>
  );
}

export function slaBadge(state: SLAState) {
  switch (state) {
    case "breached":
      return <Badge variant="danger">Breached</Badge>;
    case "due_soon":
      return <Badge variant="warning">Due soon</Badge>;
    case "met":
      return <Badge variant="success">Met</Badge>;
    case "on_track":
      return <Badge variant="success">On track</Badge>;
    default:
      return <Badge>Not configured</Badge>;
  }
}

export function priorityVariant(priority: string): BadgeVariant {
  switch (priority.toLowerCase()) {
    case "urgent":
      return "danger";
    case "high":
      return "warning";
    case "low":
      return "info";
    default:
      return "default";
  }
}

export function formatAge(createdAt: string) {
  const minutes = Math.floor(
    (Date.now() - new Date(createdAt).getTime()) / 60000
  );
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}
