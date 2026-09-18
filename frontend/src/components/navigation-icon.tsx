import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Bot,
  BrainCircuit,
  Gauge,
  ShieldCheck,
  Ticket,
  Workflow,
} from "lucide-react";

import type { NavigationIconName } from "@/lib/authorization/navigation";

const ICONS: Record<NavigationIconName, LucideIcon> = {
  gauge: Gauge,
  ticket: Ticket,
  bot: Bot,
  shield: ShieldCheck,
  brain: BrainCircuit,
  workflow: Workflow,
  activity: Activity,
};

export default function NavigationIcon({
  name,
  className,
  strokeWidth = 1.7,
}: {
  name: NavigationIconName;
  className?: string;
  strokeWidth?: number;
}) {
  const Icon = ICONS[name];
  return <Icon className={className} strokeWidth={strokeWidth} />;
}
