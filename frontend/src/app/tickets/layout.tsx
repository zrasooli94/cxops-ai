import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Customer Support Ticket Workspace",

  description:
    "Explore the CXOps AI customer-support ticket workspace for RAG evidence, AI agent decisions, approvals, and controlled workflows.",

  alternates: {
    canonical:
      "/tickets",
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
