import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: {
    absolute: "Create Test Support Case | CXOps AI",
  },

  description:
    "Create a demonstration support case for testing the CXOps AI RAG, risk-control, AI decision, and approval workflow.",

  alternates: {
    canonical:
      "/tickets/new",
  },

  robots: {
    index: false,
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
