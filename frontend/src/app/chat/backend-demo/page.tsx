import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PublicChatWidget } from "../embed/embed";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "CXOps AI · Real local backend demo",
  robots: { index: false, follow: false },
};

const LOCAL_WIDGET_KEY = "pk_local_aster_cycle_offline_demo";

export default function BackendDemoPage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return (
    <main style={{ minHeight: "100vh", padding: "48px", background: "#eef7f7", color: "#123" }}>
      <section style={{ maxWidth: "760px", margin: "0 auto" }}>
        <p style={{ textTransform: "uppercase", letterSpacing: "0.12em", fontSize: "12px" }}>
          Local integration · synthetic tenant
        </p>
        <h1>Real CXOps backend conversation</h1>
        <p>
          This page uses the production visitor widget contract, a disposable local PostgreSQL
          database, tenant-scoped knowledge, persisted conversation history and the real human
          handoff lifecycle. The responder is an offline deterministic provider—not a genuine LLM
          and not a paid model call.
        </p>
        <ul>
          <li>Try: “What are your workshop opening hours?”</li>
          <li>Follow with: “What about that?”</li>
          <li>Try Persian: «ساعات کاری تعمیرگاه چیست؟»</li>
          <li>Ask about snowboards or request a booking to trigger handoff.</li>
        </ul>
      </section>
      <PublicChatWidget
        widgetKey={LOCAL_WIDGET_KEY}
        sessionStorageKey="cxops.chat.backend-demo.session.v1"
        transparentFrame={false}
        initialPollDelayMs={1000}
      />
    </main>
  );
}
