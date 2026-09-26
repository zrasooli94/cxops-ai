import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Bot,
  BrainCircuit,
  Car,
  ClipboardCheck,
  FlaskConical,
  Gauge,
  Inbox,
  Layers,
  Route,
  ShieldCheck,
  SlidersHorizontal,
  Ticket,
  Users,
  Workflow,
} from "lucide-react";

import type { NavigationIconName } from "@/lib/authorization/navigation";

const ICONS: Record<NavigationIconName, LucideIcon> = {
  gauge: Gauge,
  ticket: Ticket,
  inbox: Inbox,
  bot: Bot,
  shield: ShieldCheck,
  brain: BrainCircuit,
  workflow: Workflow,
  activity: Activity,
  users: Users,
  layers: Layers,
  "clipboard-check": ClipboardCheck,
  route: Route,
  car: Car,
  "sliders-horizontal": SlidersHorizontal,
  flask: FlaskConical,
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
