import { Metadata } from "next";
import { requireControlCenterAuth } from "@/lib/auth/control-center";
import ControlCenterShell from "./shell";

export const metadata: Metadata = {
  robots: {
    index: false,
    follow: false,
  },
};

export default async function ControlCenterLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await requireControlCenterAuth();

  return <ControlCenterShell session={session}>{children}</ControlCenterShell>;
}