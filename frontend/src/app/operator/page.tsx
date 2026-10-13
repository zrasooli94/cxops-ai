import type { Metadata } from "next";

import { requireControlCenterAuth } from "@/lib/auth/control-center";

import { OperatorConsole } from "./operator-console";

export const metadata: Metadata = {
  title: "Operator console",
  robots: { index: false, follow: false },
};

// Phase 1Q platform-operator console. Outside the tenant control center on
// purpose: operator authority is not a tenant role, and an operator need not
// belong to any organization. The page requires a session; every action is
// authorized by the backend against the platform_operators table.
export default async function OperatorPage() {
  await requireControlCenterAuth();
  return <OperatorConsole />;
}
