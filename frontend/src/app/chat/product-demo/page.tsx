import type { Metadata } from "next";
import { notFound } from "next/navigation";
import "./demo.css";
import { DemoClient } from "./demo-client";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "CXOps AI · Local chatbot demonstration",
  robots: { index: false, follow: false },
};

/**
 * Development-only, source-level preview.
 * No backend, model, credentials, production sessions or hosting integration.
 * Crucially this does NOT become a public /chat/demo endpoint in any
 * production build (including a Vercel deployment preview).
 */
export default function ChatProductDemoPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }
  return <DemoClient />;
}
