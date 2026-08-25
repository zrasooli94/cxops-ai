import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Knowledge & RAG",

  description:
    "Explore CXOps AI knowledge retrieval and RAG workflows for grounding customer-support agent decisions in supplied knowledge.",

  alternates: {
    canonical:
      "/knowledge",
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
