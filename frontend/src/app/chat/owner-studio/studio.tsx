"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { PublicChatWidget } from "../embed/embed";

const api = "/api/local-demo/backend";
const widgetKey = "pk_local_aster_cycle_offline_demo";
type Settings = { display_name: string; welcome_message: string; theme_token: string | null; enabled: boolean };
type Faq = { title: string; content: string };
type Conversation = { id: number; subject: string | null; status: string; needs_response: boolean; latest_message?: { body: string } };
type Message = { id: number; direction: string; body: string; created_at: string; delivery_status: string | null };
type Handoff = { session_id: number; conversation_id: number; status: string; assigned_to_subject: string | null };
type LocalDocument = { document_id: number; title: string; filename: string; chunks_created: number };

async function request(path: string, init?: RequestInit) {
  const headers = new Headers(init?.headers);
  headers.set("x-cxops-local-origin", window.location.origin);
  if (!(init?.body instanceof FormData)) headers.set("content-type", "application/json");
  const response = await fetch(`${api}${path}`, { cache: "no-store", ...init, headers });
  const body = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `Request failed (${response.status})`);
  return body;
}

async function localPreviewFetch(input: RequestInfo | URL, init?: RequestInit) {
  const headers = new Headers(init?.headers);
  headers.set("X-Embedding-Origin", window.location.origin);
  return fetch(input, { ...init, headers });
}

export default function OwnerStudio({ initialView }: { initialView: "setup" | "knowledge" | "inbox" }) {
  const [tab, setTab] = useState<"setup" | "knowledge" | "inbox">(initialView);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [faqs, setFaqs] = useState<Faq[]>([]);
  const [documents, setDocuments] = useState<LocalDocument[]>([]);
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("auto");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [handoffs, setHandoffs] = useState<Handoff[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [reply, setReply] = useState("");
  const [notice, setNotice] = useState("Loading local workspace…");

  const refresh = useCallback(async () => {
    try {
      const [config, knowledge, localDocuments, inbox, queue] = await Promise.all([
        request("/staff/tenant-config/public-chat"), request("/staff/tenant-config/offline-faqs"),
        request("/staff/tenant-config/offline-documents"),
        request("/conversations"), request("/staff/public-chat/handoff"),
      ]);
      setSettings(config); setFaqs(knowledge.items ?? []); setDocuments(localDocuments.items ?? []); setConversations(inbox.items ?? []); setHandoffs(queue.sessions ?? []);
      setSelected((current) => current ?? inbox.items?.[0]?.id ?? null);
      setNotice("Saved to the disposable tenant database.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to load workspace."); }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLanguage(localStorage.getItem("cxops.owner.language") ?? "auto");
      void refresh();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  useEffect(() => { if (selected) request(`/conversations/${selected}/messages`).then(setMessages).catch((e) => setNotice(e.message)); }, [selected]);
  const selectedHandoff = useMemo(() => handoffs.find((item) => item.conversation_id === selected), [handoffs, selected]);
  async function saveSettings() {
    if (!settings) return;
    const saved = await request("/staff/tenant-config/public-chat", {
      method: "PATCH",
      body: JSON.stringify({
        display_name: settings.display_name,
        welcome_message: settings.welcome_message,
        theme_token: settings.theme_token,
        enabled: settings.enabled,
      }),
    });
    setSettings(saved); localStorage.setItem("cxops.owner.language", language); setNotice("Assistant settings saved. Preview reloads with the persisted greeting and theme.");
  }
  async function saveFaqs() {
    const saved = await request("/staff/tenant-config/offline-faqs", { method: "PUT", body: JSON.stringify({ items: faqs }) });
    setFaqs(saved.items); setNotice("FAQ knowledge saved. New visitor turns retrieve this tenant-scoped content.");
  }
  async function uploadDocument() {
    if (!documentFile) return;
    const form = new FormData(); form.set("file", documentFile);
    const saved = await request("/staff/tenant-config/offline-documents", { method: "POST", body: form });
    setDocumentFile(null); setDocuments((await request("/staff/tenant-config/offline-documents")).items ?? []);
    setNotice(saved.duplicate ? "This synthetic document was already present." : `Document uploaded as ${saved.chunks_created} grounded chunk(s).`);
  }
  async function deleteDocument(documentId: number) {
    await request(`/staff/tenant-config/offline-documents/${documentId}`, { method: "DELETE" });
    setDocuments((current) => current.filter((item) => item.document_id !== documentId));
    setNotice("Local synthetic document removed.");
  }
  async function action(name: "assign" | "release" | "resolve") {
    if (!selectedHandoff) return;
    await request(`/staff/public-chat/sessions/${selectedHandoff.session_id}/${name}`, { method: "POST" });
    await refresh(); setNotice(name === "assign" ? "Conversation claimed. Automated replies remain paused." : `Conversation ${name}d.`);
  }
  async function sendReply() {
    if (!selected || !reply.trim()) return;
    await request(`/conversations/${selected}/replies`, { method: "POST", body: JSON.stringify({ body: reply.trim(), client_request_id: crypto.randomUUID() }) });
    setReply(""); setMessages(await request(`/conversations/${selected}/messages`)); setNotice("Staff reply persisted and delivered through the local CXOps conversation channel.");
  }
  const installSnippet = `<script src="https://YOUR_CXOPS_HOST/widget.js" data-widget-key="${widgetKey}" async></script>`;

  return <main className="min-h-screen bg-[#f5f7f4] text-slate-900">
    <header className="border-b border-slate-200 bg-white px-6 py-4"><div className="mx-auto flex max-w-7xl items-center justify-between"><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-teal-700">Local synthetic tenant</p><h1 className="text-2xl font-semibold">Aster Cycle owner studio</h1></div><span className="rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-800">Offline responder · no paid AI</span></div></header>
    <div className="mx-auto grid max-w-7xl gap-6 p-6 lg:grid-cols-[1.3fr_.7fr]">
      <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <nav className="flex border-b border-slate-200 p-2">{(["setup", "knowledge", "inbox"] as const).map((item) => <button key={item} onClick={() => setTab(item)} className={`rounded-xl px-4 py-2 text-sm font-medium capitalize ${tab === item ? "bg-slate-900 text-white" : "text-slate-600"}`}>{item}</button>)}</nav>
        <div className="p-6">
          {tab === "setup" && settings && <div className="space-y-5"><div><h2 className="text-xl font-semibold">Assistant setup</h2><p className="text-sm text-slate-500">Identity, greeting, language preference and appearance.</p></div>
            <label className="block text-sm font-medium">Assistant name<input className="mt-2 w-full rounded-xl border p-3" value={settings.display_name} onChange={(e) => setSettings({ ...settings, display_name: e.target.value })}/></label>
            <label className="block text-sm font-medium">Greeting<textarea className="mt-2 w-full rounded-xl border p-3" rows={4} value={settings.welcome_message} onChange={(e) => setSettings({ ...settings, welcome_message: e.target.value })}/></label>
            <div className="grid gap-4 md:grid-cols-2"><label className="text-sm font-medium">Language<select className="mt-2 w-full rounded-xl border p-3" value={language} onChange={(e) => setLanguage(e.target.value)}><option value="auto">Automatic (English / فارسی)</option><option value="en">English</option><option value="fa">فارسی</option></select></label><label className="text-sm font-medium">Appearance<select className="mt-2 w-full rounded-xl border p-3" value={settings.theme_token ?? "default"} onChange={(e) => setSettings({ ...settings, theme_token: e.target.value })}><option value="default">Aster teal</option><option value="midnight">Midnight</option><option value="warm">Warm sand</option></select></label></div>
            <label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={settings.enabled} onChange={(e) => setSettings({ ...settings, enabled: e.target.checked })}/> Publish widget locally</label>
            <button onClick={() => void saveSettings()} className="rounded-xl bg-teal-700 px-5 py-3 text-sm font-semibold text-white">Save and preview</button>
            <div className="rounded-xl border bg-slate-50 p-4"><div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">Installation snippet preview</h3><p className="text-xs text-slate-500">Template only. Replace the host after an approved deployment.</p></div><button type="button" onClick={() => void navigator.clipboard.writeText(installSnippet).then(() => setNotice("Install snippet copied."))} className="rounded-lg border bg-white px-3 py-2 text-xs">Copy</button></div><code className="mt-3 block overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-emerald-100">{installSnippet}</code></div>
          </div>}
          {tab === "knowledge" && <div className="space-y-6"><div className="space-y-4"><div className="flex items-start justify-between"><div><h2 className="text-xl font-semibold">Business FAQs</h2><p className="text-sm text-slate-500">Tenant-scoped synthetic knowledge used by the offline retrieval demo.</p></div><button onClick={() => setFaqs([...faqs, { title: "New FAQ", content: "" }])} className="rounded-lg border px-3 py-2 text-sm">Add FAQ</button></div>{faqs.map((faq, index) => <div key={index} className="rounded-xl border bg-slate-50 p-4"><input className="w-full bg-transparent font-semibold outline-none" value={faq.title} onChange={(e) => setFaqs(faqs.map((f, i) => i === index ? { ...f, title: e.target.value } : f))}/><textarea className="mt-2 w-full rounded-lg border bg-white p-3 text-sm" rows={3} value={faq.content} onChange={(e) => setFaqs(faqs.map((f, i) => i === index ? { ...f, content: e.target.value } : f))}/></div>)}<button onClick={() => void saveFaqs()} className="rounded-xl bg-teal-700 px-5 py-3 text-sm font-semibold text-white">Save FAQs</button></div><section className="border-t pt-6"><h2 className="text-lg font-semibold">Local documents</h2><p className="mt-1 text-sm text-slate-500">Upload synthetic TXT, Markdown, or text-based PDF files up to 512 KiB. Nothing is fetched from the web or embedded by a paid model.</p><div className="mt-4 flex flex-wrap gap-3"><input aria-label="Synthetic knowledge document" type="file" accept=".txt,.md,.markdown,.pdf" onChange={(event) => setDocumentFile(event.target.files?.[0] ?? null)} className="max-w-full rounded-lg border bg-white p-2 text-sm"/><button disabled={!documentFile} onClick={() => void uploadDocument()} className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">Upload document</button></div><div className="mt-4 space-y-2">{documents.length === 0 && <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-500">No local documents uploaded.</p>}{documents.map((document) => <div key={document.document_id} className="flex items-center justify-between gap-3 rounded-lg border p-3"><div><p className="text-sm font-semibold">{document.title}</p><p className="text-xs text-slate-500">{document.filename} · {document.chunks_created} chunk(s)</p></div><button onClick={() => void deleteDocument(document.document_id)} className="rounded-lg border px-3 py-2 text-xs text-rose-700">Remove</button></div>)}</div></section></div>}
          {tab === "inbox" && <div><div className="mb-5"><h2 className="text-xl font-semibold">Shared inbox</h2><p className="text-sm text-slate-500">Saved customer conversations and human takeover.</p></div><div className="grid min-h-[500px] gap-4 md:grid-cols-[240px_1fr]"><aside className="space-y-2">{conversations.map((item) => <button key={item.id} onClick={() => setSelected(item.id)} className={`w-full rounded-xl border p-3 text-left ${selected === item.id ? "border-teal-600 bg-teal-50" : "bg-white"}`}><div className="flex justify-between text-sm font-semibold"><span>Conversation #{item.id}</span>{item.needs_response && <span className="text-amber-700">New</span>}</div><p className="mt-1 truncate text-xs text-slate-500">{item.latest_message?.body ?? item.subject ?? "No message"}</p></button>)}</aside><article className="flex flex-col rounded-xl border bg-slate-50 p-4"><div className="mb-3 flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">{selectedHandoff ? selectedHandoff.status.replaceAll("_", " ") : "Saved conversation"}</span>{selectedHandoff?.status === "human_requested" && <button onClick={() => void action("assign")} className="rounded-lg bg-slate-900 px-3 py-2 text-xs text-white">Claim & take over</button>}{selectedHandoff?.status === "human_assigned" && <><button onClick={() => void action("release")} className="rounded-lg border px-3 py-2 text-xs">Release</button><button onClick={() => void action("resolve")} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs text-white">Resolve</button></>}</div><div className="flex-1 space-y-2 overflow-auto">{messages.map((message) => <div key={message.id} className={`max-w-[85%] rounded-xl p-3 text-sm ${message.direction === "inbound" ? "bg-white" : "ml-auto bg-teal-700 text-white"}`}>{message.body}<div className="mt-1 text-[10px] opacity-60">{message.direction} {message.delivery_status ?? ""}</div></div>)}</div><div className="mt-3 flex gap-2"><input className="flex-1 rounded-xl border bg-white p-3 text-sm" placeholder={selectedHandoff?.status === "human_assigned" ? "Reply as staff…" : "Claim the handoff to reply"} disabled={selectedHandoff?.status !== "human_assigned"} value={reply} onChange={(e) => setReply(e.target.value)}/><button disabled={selectedHandoff?.status !== "human_assigned"} onClick={() => void sendReply()} className="rounded-xl bg-teal-700 px-4 text-sm font-semibold text-white disabled:opacity-40">Send</button></div></article></div></div>}
        </div>
      </section>
      <aside><div className="sticky top-6 rounded-2xl border border-slate-200 bg-[#e8f5f2] p-5"><p className="text-xs font-semibold uppercase tracking-wider text-teal-800">Live customer preview</p><h2 className="mt-1 font-semibold">Production widget contract</h2><p className="mt-2 text-sm text-slate-600">Open the bubble below. New messages use the real local API, persisted sessions and saved FAQs.</p><div className="mt-80 text-xs text-slate-500">Language: {language === "fa" ? "فارسی" : language === "en" ? "English" : "Automatic bilingual"}</div></div></aside>
    </div>
    <div className="fixed bottom-3 left-1/2 z-50 -translate-x-1/2 rounded-full bg-slate-900 px-4 py-2 text-xs text-white shadow-lg">{notice}</div>
    <PublicChatWidget widgetKey={widgetKey} sessionStorageKey="cxops.chat.owner-studio.preview.v1" transparentFrame={false} initialPollDelayMs={800} fetcher={localPreviewFetch}/>
  </main>;
}
