import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "AI Operations Observability",

  description:
    "Explore CXOps AI operational observability for agent runs, approvals, model activity, RAG measurements, and execution outcomes.",

  alternates: {
    canonical:
      "/observability",
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
