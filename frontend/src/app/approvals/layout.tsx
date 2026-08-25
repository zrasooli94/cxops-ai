import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Human Approval Queue",

  description:
    "Explore the CXOps AI human approval workflow for reviewing AI-assisted support actions before controlled execution.",

  alternates: {
    canonical:
      "/approvals",
  },

  robots: {
    index: true,
    follow: true,
  },
};

export default function RouteLayout({
  children,
}: {
  children: ReactNode;
}) {
  return <>{children}</>;
}
