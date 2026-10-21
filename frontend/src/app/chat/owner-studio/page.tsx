import type { Metadata } from "next";
import { notFound } from "next/navigation";
import OwnerStudio from "./studio";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "CXOps AI · Local owner studio", robots: { index: false } };

export default async function Page({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  if (process.env.NODE_ENV !== "development") notFound();
  const view = (await searchParams).view;
  const initialView = view === "inbox" || view === "knowledge" ? view : "setup";
  return <OwnerStudio initialView={initialView} />;
}
