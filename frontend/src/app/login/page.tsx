import { Metadata } from "next";
import { redirect } from "next/navigation";
import { LoginForm } from "./LoginForm";
import { readControlCenterSession } from "@/lib/auth/control-center";
import { loginPageDecision } from "@/lib/session/read";

export const metadata: Metadata = {
  title: "Sign in | CXOps AI",
  description: "Sign in to CXOps AI Control Center",
  robots: {
    index: false,
    follow: false,
  },
};

export default async function LoginPage() {
  const { state } = await readControlCenterSession();
  const decision = loginPageDecision(state);

  // A valid session is forwarded through the landing Route Handler, which owns
  // the post-auth destination decision (memberships / active organization).
  if (decision === "forward") {
    redirect("/api/auth/landing");
  }

  // A stale (expired) session must be recovered through a MUTABLE context
  // (Route Handler): refreshing or clearing cookies is not supported during
  // Server Component rendering and would crash the page.
  if (decision === "recover") {
    redirect("/api/auth/session");
  }

  return <LoginForm />;
}