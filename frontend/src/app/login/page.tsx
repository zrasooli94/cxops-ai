import { Metadata } from "next";
import { redirect } from "next/navigation";
import { LoginForm } from "./LoginForm";
import { getControlCenterSession } from "@/lib/auth/control-center";
import {
  getActiveOrganizationId,
  setActiveOrganizationId,
} from "@/lib/tenant/cookie";
import { getMyOrganizations } from "@/lib/tenant/backend-client";

export const metadata: Metadata = {
  title: "Sign in | CXOps AI",
  description: "Sign in to CXOps AI Control Center",
  robots: {
    index: false,
    follow: false,
  },
};

async function resolvePostAuthRedirect(): Promise<string> {
  const memberships = await getMyOrganizations();

  if (memberships.length === 0) {
    return "/no-organization";
  }

  if (memberships.length === 1) {
    await setActiveOrganizationId(memberships[0].id);
    return "/dashboard";
  }

  const activeOrganizationId = await getActiveOrganizationId();
  const isValidSelection = memberships.some(
    (m) => m.id === activeOrganizationId,
  );

  return isValidSelection ? "/dashboard" : "/select-organization";
}

export default async function LoginPage() {
  const session = await getControlCenterSession();

  if (session) {
    const destination = await resolvePostAuthRedirect();
    redirect(destination);
  }

  return <LoginForm />;
}
