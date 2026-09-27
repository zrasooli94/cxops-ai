import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Settings",
  description:
    "Tenant configuration for the public chat widget and business integrations.",
};

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
