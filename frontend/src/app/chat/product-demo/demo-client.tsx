"use client";

import { useEffect, useState } from "react";
import { PublicChatWidget } from "../embed/embed";
import {
  DemoChatTransport,
  DEMO_WIDGET_KEY,
  DEMO_WIDGET_SESSION_KEY,
  type DemoLanguage,
  type DemoSnapshot,
  type DemoTopic,
} from "@/lib/public-chat/demo";

type Panel = "business" | "knowledge" | "inbox";

const KNOWLEDGE_TOPICS: readonly { id: DemoTopic; label: string }[] = [
  { id: "services", label: "Products & services" },
  { id: "hours", label: "Opening hours" },
  { id: "prices", label: "Pricing" },
  { id: "returns", label: "Returns" },
  { id: "warranty", label: "Warranty" },
  { id: "location", label: "Location" },
];

const INSTALL_EXAMPLE =
  '<script src="https://YOUR-CXOPS-HOST/embed/loader.js"\n' +
  '  data-widget-key="YOUR_PUBLISHED_WIDGET_KEY" async></script>';

export function DemoClient() {
  const [transport] = useState(() => new DemoChatTransport());
  const [snapshot, setSnapshot] = useState<DemoSnapshot>(() => transport.snapshot());
  const [panel, setPanel] = useState<Panel>("business");
  const [knowledgeLanguage, setKnowledgeLanguage] = useState<DemoLanguage>("en");
  const [staffDraft, setStaffDraft] = useState("");
  const [ready, setReady] = useState(false);
  const [revision, setRevision] = useState(0);
  const [copyStatus, setCopyStatus] = useState("");

  useEffect(() => {
    const unsubscribe = transport.subscribe(() => setSnapshot(transport.snapshot()));
    try { transport.attachStorage(window.sessionStorage); }
    catch { /* Optional browser storage: this page still works in memory. */ }
    setSnapshot(transport.snapshot());
    setReady(true);
    return unsubscribe;
  }, [transport]);

  const restart = () => {
    transport.resetConversation();
    try { window.sessionStorage.removeItem(DEMO_WIDGET_SESSION_KEY); }
    catch { /* private browsing is fine */ }
    setRevision((value) => value + 1);
    setStaffDraft("");
  };

  const openVisitorChat = () => {
    document.querySelector<HTMLButtonElement>(
      'button[aria-label="Open support chat"]',
    )?.click();
  };

  const copyExample = async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_EXAMPLE);
      setCopyStatus("Example copied. It is not a live installation.");
    } catch {
      setCopyStatus("Clipboard not available. Select and copy the example above.");
    }
  };

  const submitStaffReply = () => {
    if (transport.staffReply(staffDraft)) {
      setStaffDraft("");
    }
  };

  return (
    <main className="cx-demo">
      <nav className="cx-demo-nav" aria-label="Demo navigation">
        <div className="cx-demo-mark">
          <span className="cx-demo-logo" aria-hidden="true">CX</span>
          <span>CXOps AI <span style={{ fontWeight: 450, color: "#90a9b4" }}>/ Product Studio</span></span>
        </div>
        <div className="cx-demo-navright">
          <span className="cx-demo-pill">LOCAL · MOCK DATA</span>
          <span>No real AI, customers or human agents</span>
        </div>
      </nav>

      <div className="cx-demo-layout">
        <aside className="cx-demo-sidebar" aria-label="Business configuration">
          <div className="cx-demo-kicker">Chatbot product milestone 01</div>
          <h1>Make support<br />feel effortless.</h1>
          <p className="cx-demo-lede">
            Configure a fictional shop, edit the answers it knows, then test
            the <strong>real CXOps visitor widget</strong> and simulate a human takeover.
          </p>

          <div className="cx-demo-tabs" role="tablist" aria-label="Demo workspaces">
            <button type="button" role="tab" aria-selected={panel === "business"}
              onClick={() => setPanel("business")}>Business</button>
            <button type="button" role="tab" aria-selected={panel === "knowledge"}
              onClick={() => setPanel("knowledge")}>Knowledge</button>
            <button type="button" role="tab" aria-selected={panel === "inbox"}
              onClick={() => setPanel("inbox")}>
              Staff inbox {snapshot.session?.status === "human_requested" ? "●" : ""}
            </button>
          </div>

          {panel === "business" && (
            <section className="cx-demo-panel" aria-label="Business setup">
              <h2>Assistant settings</h2>
              <label className="cx-demo-field">
                <span>Assistant / business name</span>
                <input maxLength={80} value={snapshot.settings.name}
                  onChange={(event) => transport.configure({ name: event.target.value })} />
              </label>
              <label className="cx-demo-field">
                <span>Welcome message</span>
                <textarea maxLength={350} value={snapshot.settings.greeting}
                  onChange={(event) => transport.configure({ greeting: event.target.value })} />
              </label>
              <label className="cx-demo-field">
                <span>Chat widget theme</span>
                <select value={snapshot.settings.theme}
                  onChange={(event) => transport.configure({
                    theme: event.target.value as DemoSnapshot["settings"]["theme"],
                  })}>
                  <option value="midnight">Midnight · dark</option>
                  <option value="default">Classic · teal</option>
                  <option value="ocean">Ocean · blue</option>
                  <option value="forest">Forest · green</option>
                </select>
              </label>
              <p className="cx-demo-small">Changes to a conversation&apos;s welcome message,
                name and theme take effect when a new demo session starts.
                Knowledge edits take effect immediately on the next question.</p>
              <button type="button" className="cx-demo-primary" onClick={restart}>
                Apply settings &amp; start fresh chat
              </button>
              <div className="cx-demo-note">
                These are browser-only settings, not a real tenant or published
                chatbot. This preview is deliberately disabled outside local development.
              </div>
            </section>
          )}

          {panel === "knowledge" && (
            <section className="cx-demo-panel" aria-label="Edit fictional knowledge">
              <h2>What your assistant knows</h2>
              <p className="cx-demo-small">
                Edit the fictional knowledge below. The deterministic demo answers
                only from these authored entries, with source labels. This does not
                test document ingestion or real AI retrieval.
              </p>
              <label className="cx-demo-field">
                <span>Knowledge language</span>
                <select value={knowledgeLanguage}
                  onChange={(event) => setKnowledgeLanguage(event.target.value as DemoLanguage)}>
                  <option value="en">English</option>
                  <option value="fa">فارسی · Persian</option>
                </select>
              </label>
              <div className="cx-demo-knowledge">
                {KNOWLEDGE_TOPICS.map((topic) => (
                  <article key={topic.id}>
                    <h3>{topic.label}</h3>
                    <label className="cx-demo-field">
                      <span className="cx-demo-small">Answer shown when a question matches this topic</span>
                      <textarea dir={knowledgeLanguage === "fa" ? "rtl" : "ltr"}
                        maxLength={1000}
                        value={snapshot.knowledge[topic.id][knowledgeLanguage]}
                        onChange={(event) =>
                          transport.setKnowledge(topic.id, knowledgeLanguage, event.target.value)}
                      />
                    </label>
                  </article>
                ))}
              </div>
              <div className="cx-demo-note">Tip: clear an answer, then ask about that
                topic. The simulation must hand off rather than fabricate an answer.</div>
            </section>
          )}

          {panel === "inbox" && (
            <section className="cx-demo-panel" aria-label="Simulated staff inbox">
              <h2>Simulated staff inbox</h2>
              <p className="cx-demo-small">
                This represents a staff handoff workflow. Claiming or replying
                here does not assign a real employee or contact a customer.
              </p>
              <div className="cx-demo-inbox">
                {!snapshot.session ? (
                  <p className="cx-demo-small">No demo conversation yet. Open the
                    visitor chat and ask a question or request a human.</p>
                ) : (
                  <>
                    <h3>Visitor conversation</h3>
                    <p className="cx-demo-status" aria-live="polite">
                      {snapshot.session.status.replaceAll("_", " ")}
                    </p>
                    <div className="cx-demo-transcript" aria-label="Simulated conversation history">
                      {snapshot.session.messages.length === 0 && (
                        <p className="cx-demo-small">No visitor messages yet.</p>
                      )}
                      {snapshot.session.messages.map((message) => (
                        <div key={message.id} dir="auto">
                          <strong>{message.direction === "inbound" ? "Visitor" : "Support / simulation"}</strong>
                          {message.body}
                        </div>
                      ))}
                    </div>
                    {snapshot.session.status === "human_requested" && (
                      <button type="button" className="cx-demo-primary" onClick={() => transport.claimHuman()}>
                        Simulate staff claiming conversation
                      </button>
                    )}
                    {snapshot.session.status === "human_assigned" && (
                      <>
                        <label className="cx-demo-field">
                          <span>Simulated staff reply</span>
                          <textarea maxLength={600} value={staffDraft}
                            onChange={(event) => setStaffDraft(event.target.value)}
                            placeholder="Type what a human support agent might say…" />
                        </label>
                        <button type="button" className="cx-demo-primary" disabled={!staffDraft.trim()}
                          onClick={submitStaffReply}>Send simulated staff reply</button>
                      </>
                    )}
                    {snapshot.session.status === "ai_active" && (
                      <p className="cx-demo-small">No handoff yet. The visitor
                        can select “Chat with a human” or ask an unsupported question.</p>
                    )}
                  </>
                )}
              </div>
              <button type="button" className="cx-demo-secondary" onClick={restart}>
                Reset visitor conversation
              </button>
            </section>
          )}
        </aside>

        <div className="cx-demo-workspace">
          <div className="cx-demo-workhead">
            <div>
              <div className="cx-demo-kicker">Visitor experience · actual React widget</div>
              <h2>Live-in-browser website preview</h2>
              <p>Visit the fictional storefront and open the support bubble
                in the bottom-right corner. No chat message reaches a CXOps API or paid provider.</p>
            </div>
            <button type="button" className="cx-demo-primary" onClick={openVisitorChat}>
              Open visitor chat ↗
            </button>
          </div>

          <section className="cx-demo-browser" aria-label="Fictional storefront preview">
            <div className="cx-demo-browserbar">
              <div className="cx-demo-dotset" aria-hidden="true"><i /><i /><i /></div>
              <div className="cx-demo-address">fictional-aster.example · storefront preview only</div>
            </div>
            <div className="cx-demo-store">
              <header className="cx-demo-storehead">
                <div className="cx-demo-storemark"><span aria-hidden="true">✦</span>
                  {snapshot.settings.name || "Aster Cycle Studio"}
                </div>
                <small>Ride further. We&apos;ll handle the details.</small>
              </header>
              <div className="cx-demo-hero">
                <div className="cx-demo-hero-inner">
                  <span className="eyebrow">Fictional independent bicycle workshop</span>
                  <h2>Every great ride<br />starts with care.</h2>
                  <p>Precision tune-ups, repairs and advice from people
                    who love riding as much as you do.</p>
                </div>
              </div>
              <div className="cx-demo-shop-grid">
                <div className="cx-demo-shop-tile">🛠️<b>Workshop services</b><small>Maintenance, repairs, inspections</small></div>
                <div className="cx-demo-shop-tile">🚲<b>Everyday cycling</b><small>Practical support for your bicycle</small></div>
                <div className="cx-demo-shop-tile">✦<b>Ask our assistant</b><small>Click the support bubble at bottom right</small></div>
              </div>
            </div>
          </section>

          <div className="cx-demo-bottom">
            <div>
              <h3>Try these five scenarios</h3>
              <ol className="cx-demo-steps">
                <li>Ask “What services do you offer?”</li>
                <li>Ask “What are your hours?” then “What about that?”</li>
                <li>Ask in Persian: «ساعات کاری شما چیست؟»</li>
                <li>Ask an unsupported question to trigger human handoff.</li>
                <li>Use the Staff inbox to claim and answer the conversation.</li>
              </ol>
            </div>
            <div>
              <h3>Real installer format · not activated</h3>
              <pre className="cx-demo-code">{INSTALL_EXAMPLE}</pre>
              <p className="cx-demo-small">This is the existing loader&apos;s format,
                not an active install. Actual widget keys, origin approval,
                backend access and real knowledge onboarding are separate milestones.</p>
              <button type="button" className="cx-demo-secondary" onClick={() => void copyExample()}>
                Copy placeholder snippet
              </button>
              {copyStatus && <p role="status" className="cx-demo-small">{copyStatus}</p>}
            </div>
          </div>
        </div>
      </div>

      {ready && (
        <PublicChatWidget key={revision} widgetKey={DEMO_WIDGET_KEY}
          fetcher={transport.fetcher} sessionStorageKey={DEMO_WIDGET_SESSION_KEY}
          transparentFrame={false} />
      )}
    </main>
  );
}
