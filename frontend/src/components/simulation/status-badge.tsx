import { formatStatusLabel } from "@/lib/simulation/simulation";

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "violet";

const VARIANT_STYLES: Record<BadgeVariant, string> = {
  default: "border-slate-200 bg-slate-50 text-slate-600",
  success: "border-emerald-200 bg-emerald-50 text-emerald-700",
  warning: "border-amber-200 bg-amber-50 text-amber-700",
  danger: "border-rose-200 bg-rose-50 text-rose-700",
  info: "border-blue-200 bg-blue-50 text-blue-700",
  violet: "border-violet-200 bg-violet-50 text-violet-700",
};

export function Badge({
  children,
  variant = "default",
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium ${VARIANT_STYLES[variant]}`}
    >
      {children}
    </span>
  );
}

const SIMULATION_STATUS_VARIANTS: Record<string, BadgeVariant> = {
  draft: "warning",
  evaluated: "success",
  archived: "default",
};

export function SimulationStatusBadge({ status }: { status: string }) {
  return (
    <Badge variant={SIMULATION_STATUS_VARIANTS[status] ?? "warning"}>
      {formatStatusLabel(status)}
    </Badge>
  );
}