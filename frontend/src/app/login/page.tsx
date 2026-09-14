import { Metadata } from "next";
import { redirect } from "next/navigation";
import { LoginForm } from "./LoginForm";
import { getControlCenterSession } from "@/lib/auth/control-center";

export const metadata: Metadata = {
  title: "Sign in | CXOps AI",
  description: "Sign in to CXOps AI Control Center",
  robots: {
    index: false,
    follow: false,
  },
};

export default async function LoginPage() {
  const session = await getControlCenterSession();

  if (session) {
    redirect("/dashboard");
  }

  return <LoginForm />;
}