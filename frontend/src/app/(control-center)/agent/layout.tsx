import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "AI Agent Console",

  description:
    "Explore the CXOps AI agent workflow for RAG-assisted support reasoning, risk controls, human approvals, and auditable execution.",

  alternates: {
    canonical:
      "/agent",
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
