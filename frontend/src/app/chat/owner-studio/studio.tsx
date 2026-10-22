"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PublicChatWidget } from "../embed/embed";
import { buildKnowledgeGapReview, evidenceCoverageLabel, refreshSelectedConversation, selectCurrentEvidence, staffReplyOutcomeCopy, summarizeOwnerStudio, type StaffReplyOutcome } from "@/lib/public-chat/owner-studio";

const api = "/api/local-demo/backend";
const widgetKey = "pk_local_aster_cycle_offline_demo";
type Settings = { display_name: string; welcome_message: string; theme_token: string | null; enabled: boolean };
type Faq = { title: string; content: string };
type Conversation = { id: number; subject: string | null; status: string; needs_response: boolean; latest_message?: { body: string } };
type Message = { id: number; direction: string; body: string; created_at: string; delivery_status: string | null };
type Handoff = { session_id: number; conversation_id: number; status: string; assigned_to_subject: string | null };
type LocalDocument = { document_id: number; title: string; filename: string; chunks_created: number };
type Evidence = { question: string; coverage: "missing" | "ambiguous" | "related"; items: { source_id: string; title: string; provenance: string; excerpt: string; lexical_score: number }[] };

class LocalRequestError extends Error {
  constructor(message: string, readonly ambiguous: boolean, readonly status: number | null = null) {
    super(message);
    this.name = "LocalRequestError";
  }
}

async function request(path: string, init?: RequestInit) {
  const headers = new Headers(init?.headers);
  headers.set("x-cxops-local-origin", window.location.origin);
  if (!(init?.body instanceof FormData)) headers.set("content-type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${api}${path}`, { cache: "no-store", ...init, headers });
  } catch (error) {
    throw new LocalRequestError(error instanceof Error ? error.message : "Request outcome is uncertain.", true);
  }
  const body = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new LocalRequestError(body.detail ?? `Request failed (${response.status})`, false, response.status);
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
  const selectedRef = useRef<number | null>(selected);
  const [loadedMessages, setMessages] = useState<Message[]>([]);
  const [loadedConversationId, setLoadedConversationId] = useState<number | null>(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const messageLoadGeneration = useRef(0);
  const [rawEvidence, setEvidence] = useState<Evidence | null>(null);
  const [evidenceState, setEvidenceState] = useState<"idle" | "loading" | "error">("idle");
  const [evidenceRetry, setEvidenceRetry] = useState(0);
  const [replyDrafts, setReplyDrafts] = useState<Record<number, string>>({});
  const [replyOutcomes, setReplyOutcomes] = useState<Record<number, StaffReplyOutcome>>({});
  const sendingConversationRef = useRef<number | null>(null);
  const [notice, setNotice] = useState("Loading local workspace…");
  const selectConversation = useCallback((conversationId: number) => {
    selectedRef.current = conversationId;
    setSelected(conversationId);
  }, []);
  const reply = selected === null ? "" : replyDrafts[selected] ?? "";
  const setReply = useCallback((value: string) => {
    const conversationId = selectedRef.current;
    if (conversationId === null) return;
    setReplyDrafts((current) => ({ ...current, [conversationId]: value }));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [config, knowledge, localDocuments, inbox, queue] = await Promise.all([
        request("/staff/tenant-config/public-chat"), request("/staff/tenant-config/offline-faqs"),
        request("/staff/tenant-config/offline-documents"),
        request("/conversations"), request("/staff/public-chat/handoff"),
      ]);
      setSettings(config); setFaqs(knowledge.items ?? []); setDocuments(localDocuments.items ?? []); setConversations(inbox.items ?? []); setHandoffs(queue.sessions ?? []);
      if (selectedRef.current === null && inbox.items?.[0]?.id) selectConversation(inbox.items[0].id);
      setNotice("Saved to the disposable tenant database.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to load workspace."); }
  }, [selectConversation]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLanguage(localStorage.getItem("cxops.owner.language") ?? "auto");
      void refresh();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    const generation = ++messageLoadGeneration.current;
    queueMicrotask(() => {
      if (active) { setMessagesLoading(true); setMessagesError(null); }
    });
    request(`/conversations/${selected}/messages`).then((items) => {
      if (active && generation === messageLoadGeneration.current) {
        setMessages(items); setLoadedConversationId(selected);
        setReplyOutcomes((current) => {
          if (current[selected] !== "saved_refresh_failed") return current;
          const next = { ...current }; delete next[selected]; return next;
        });
      }
    }).catch((error) => {
      if (active && generation === messageLoadGeneration.current) setMessagesError(error instanceof Error ? error.message : "Unable to load conversation.");
    }).finally(() => { if (active && generation === messageLoadGeneration.current) setMessagesLoading(false); });
    return () => { active = false; };
  }, [selected]);
  const messages = useMemo(
    () => loadedConversationId === selected ? loadedMessages : [],
    [loadedConversationId, loadedMessages, selected],
  );
  const selectedHandoff = useMemo(() => handoffs.find((item) => item.conversation_id === selected), [handoffs, selected]);
  const analytics = useMemo(() => summarizeOwnerStudio(conversations, handoffs), [conversations, handoffs]);
  const knowledgeCandidate = useMemo(() => buildKnowledgeGapReview(messages, [
    ...faqs,
    ...documents.map((document) => ({ title: document.title })),
  ], Boolean(selectedHandoff)), [documents, faqs, messages, selectedHandoff]);
  const evidence = selectCurrentEvidence(knowledgeCandidate?.question, rawEvidence);
  // Kept false while the compact inbox markup below is migrated; the owner-facing
  // candidate panel is rendered above it with neutral, evidence-accurate wording.
  const knowledgeGap = false ? knowledgeCandidate : null;
  useEffect(() => {
    if (!knowledgeCandidate) return;
    let active = true;
    const question = knowledgeCandidate.question;
    queueMicrotask(() => { if (active) { setEvidence(null); setEvidenceState("loading"); } });
    request(`/staff/tenant-config/offline-evidence?question=${encodeURIComponent(question)}`)
      .then((result) => { if (active && result.question === question) { setEvidence(result); setEvidenceState("idle"); } })
      .catch(() => { if (active) setEvidenceState("error"); });
    return () => { active = false; };
  }, [evidenceRetry, knowledgeCandidate]);
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
    if (!selected || !reply.trim() || sendingConversationRef.current !== null) return;
    const targetConversationId = selected;
    const generation = ++messageLoadGeneration.current;
    setReplyOutcomes((current) => { const next = { ...current }; delete next[targetConversationId]; return next; });
    sendingConversationRef.current = targetConversationId;
    try {
      await request(`/conversations/${targetConversationId}/replies`, { method: "POST", body: JSON.stringify({ body: reply.trim(), client_request_id: crypto.randomUUID() }) });
    } catch (error) {
      setReplyOutcomes((current) => ({ ...current, [targetConversationId]: error instanceof LocalRequestError && error.ambiguous ? "send_uncertain" : "send_failed" }));
      sendingConversationRef.current = null;
      return;
    }
    setReplyDrafts((current) => { const next = { ...current }; delete next[targetConversationId]; return next; });
    try {
      const applied = await refreshSelectedConversation<Message[]>(
        targetConversationId,
        (conversationId) => request(`/conversations/${conversationId}/messages`),
        () => selectedRef.current === targetConversationId && generation === messageLoadGeneration.current ? targetConversationId : null,
        (items, conversationId) => { if (generation === messageLoadGeneration.current) { setMessages(items); setLoadedConversationId(conversationId); } },
      );
      if (applied) {
        setReplyOutcomes((current) => { const next = { ...current }; delete next[targetConversationId]; return next; });
        setNotice("Staff reply persisted and delivered through the local CXOps conversation channel.");
      } else {
        setReplyOutcomes((current) => ({ ...current, [targetConversationId]: "saved_refresh_failed" }));
      }
    } catch {
      setReplyOutcomes((current) => ({ ...current, [targetConversationId]: "saved_refresh_failed" }));
    } finally {
      sendingConversationRef.current = null;
    }
  }
  async function refreshSavedReply() {
    const targetConversationId = selectedRef.current;
    if (!targetConversationId || replyOutcomes[targetConversationId] !== "saved_refresh_failed") return;
    const generation = ++messageLoadGeneration.current;
    setMessagesLoading(true); setMessagesError(null);
    try {
      const applied = await refreshSelectedConversation<Message[]>(
        targetConversationId,
        (conversationId) => request(`/conversations/${conversationId}/messages`),
        () => selectedRef.current === targetConversationId && generation === messageLoadGeneration.current ? targetConversationId : null,
        (items, conversationId) => { if (generation === messageLoadGeneration.current) { setMessages(items); setLoadedConversationId(conversationId); } },
      );
      if (applied) setReplyOutcomes((current) => { const next = { ...current }; delete next[targetConversationId]; return next; });
    } catch (error) {
      if (selectedRef.current === targetConversationId) setMessagesError(error instanceof Error ? error.message : "Unable to refresh conversation.");
    } finally {
      if (selectedRef.current === targetConversationId) setMessagesLoading(false);
    }
  }
  const selectedReplyOutcome = selected === null ? null : replyOutcomes[selected] ?? null;
  const selectedReplyOutcomeCopy = selectedReplyOutcome ? staffReplyOutcomeCopy(selectedReplyOutcome, language) : null;
  function draftFaqFromGap() {
    if (!knowledgeCandidate) return;
    setFaqs((current) => [...current, { title: knowledgeCandidate.question, content: "" }]);
    setTab("knowledge");
    setNotice(language === "fa" ? "پرسش به‌صورت پیش‌نویس افزوده شد. پاسخ تأییدشده را بنویسید و ذخیره کنید." : "Question added as a draft FAQ. Add an approved answer, then save it.");
  }
  const installSnippet = `<script src="https://YOUR_CXOPS_HOST/widget.js" data-widget-key="${widgetKey}" async></script>`;

  return <main className="min-h-screen overflow-x-hidden bg-[#f5f7f4] text-slate-900">
    <header className="border-b border-slate-200 bg-white px-6 py-4"><div className="mx-auto flex max-w-7xl items-center justify-between"><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-teal-700">Local synthetic tenant</p><h1 className="text-2xl font-semibold">Aster Cycle owner studio</h1></div><span className="rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-800">Offline responder · no paid AI</span></div></header>
    <div className="mx-auto grid max-w-7xl gap-6 p-3 sm:p-6 lg:grid-cols-[1.3fr_.7fr]">
      <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <nav className="flex overflow-x-auto border-b border-slate-200 p-2">{(["setup", "knowledge", "inbox"] as const).map((item) => <button key={item} onClick={() => setTab(item)} className={`shrink-0 rounded-xl px-4 py-2 text-sm font-medium capitalize ${tab === item ? "bg-slate-900 text-white" : "text-slate-600"}`}>{item}</button>)}</nav>
        {tab === "inbox" && knowledgeCandidate && <section aria-label="Retrieved knowledge evidence" dir={language === "fa" ? "rtl" : "ltr"} className="mx-4 mt-4 rounded-xl border border-teal-200 bg-teal-50 p-4 sm:mx-6"><h3 className="font-semibold text-teal-950">{language === "fa" ? "شواهد واژگانی دانش" : "Lexical knowledge evidence"}</h3><p className="mt-1 text-xs text-teal-800">{language === "fa" ? "گزیده‌های واقعی و محدود به همین مستأجر؛ نه جست‌وجوی معنایی و نه اثبات درستی." : "Actual tenant-scoped excerpts; not semantic search or proof of factual entailment."}</p>{evidenceState === "loading" && <p className="mt-3 text-sm text-teal-800">{language === "fa" ? "در حال یافتن گزیده‌ها…" : "Finding matching excerpts…"}</p>}{evidenceState === "error" && <div role="alert" className="mt-3 flex flex-wrap items-center gap-2 text-sm text-rose-800"><span>{language === "fa" ? "شواهد در دسترس نیست." : "Evidence is unavailable."}</span><button type="button" onClick={() => setEvidenceRetry((attempt) => attempt + 1)} className="rounded-md border border-rose-300 bg-white px-2 py-1 text-xs font-semibold">{language === "fa" ? "تلاش دوباره" : "Retry evidence"}</button></div>}{evidence && <><p className="mt-3 text-xs font-semibold uppercase tracking-wide text-teal-900">{evidenceCoverageLabel(evidence.coverage, language)}</p><div className="mt-2 space-y-2">{evidence.items.map((item) => <article key={item.source_id} className="rounded-lg border border-teal-100 bg-white p-3"><div className="flex flex-wrap items-center justify-between gap-2 text-xs"><strong>{item.title}</strong><span>{item.provenance === "document" ? (language === "fa" ? "سند" : "Document") : (language === "fa" ? "پرسش متداول" : "FAQ")}</span></div><p dir="auto" className="mt-2 line-clamp-4 whitespace-pre-wrap break-words text-start text-sm text-slate-700">{item.excerpt}</p></article>)}{evidence.items.length === 0 && <p className="rounded-lg bg-white p-3 text-sm text-teal-900">{language === "fa" ? "هیچ گزیده مشابهی یافت نشد." : "No matching excerpt was found."}</p>}</div></>}</section>}
        <div className="p-4 sm:p-6">
          {tab === "setup" && settings && <div className="space-y-5" dir={language === "fa" ? "rtl" : "ltr"}><div><h2 className="text-xl font-semibold">Assistant setup</h2><p className="text-sm text-slate-500">Identity, greeting, language preference and appearance.</p></div>
            <label className="block text-sm font-medium">Assistant name<input className="mt-2 w-full rounded-xl border p-3" value={settings.display_name} onChange={(e) => setSettings({ ...settings, display_name: e.target.value })}/></label>
            <label className="block text-sm font-medium">Greeting<textarea dir="auto" className="mt-2 w-full rounded-xl border p-3 text-start" rows={4} value={settings.welcome_message} onChange={(e) => setSettings({ ...settings, welcome_message: e.target.value })}/></label>
            <div className="grid gap-4 md:grid-cols-2"><label className="text-sm font-medium">Language<select className="mt-2 w-full rounded-xl border p-3" value={language} onChange={(e) => setLanguage(e.target.value)}><option value="auto">Automatic (English / فارسی)</option><option value="en">English</option><option value="fa">فارسی</option></select></label><label className="text-sm font-medium">Appearance<select className="mt-2 w-full rounded-xl border p-3" value={settings.theme_token ?? "default"} onChange={(e) => setSettings({ ...settings, theme_token: e.target.value })}><option value="default">Aster teal</option><option value="midnight">Midnight</option><option value="warm">Warm sand</option></select></label></div>
            <label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={settings.enabled} onChange={(e) => setSettings({ ...settings, enabled: e.target.checked })}/> Publish widget locally</label>
            <button onClick={() => void saveSettings()} className="rounded-xl bg-teal-700 px-5 py-3 text-sm font-semibold text-white">Save and preview</button>
            <div className="rounded-xl border bg-slate-50 p-4"><div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">Installation snippet preview</h3><p className="text-xs text-slate-500">Template only. Replace the host after an approved deployment.</p></div><button type="button" onClick={() => void navigator.clipboard.writeText(installSnippet).then(() => setNotice("Install snippet copied."))} className="rounded-lg border bg-white px-3 py-2 text-xs">Copy</button></div><code className="mt-3 block overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-emerald-100">{installSnippet}</code></div>
          </div>}
          {tab === "knowledge" && <div className="space-y-6" dir={language === "fa" ? "rtl" : "ltr"}><div className="space-y-4"><div className="flex items-start justify-between gap-3"><div><h2 className="text-xl font-semibold">{language === "fa" ? "پرسش‌های متداول کسب‌وکار" : "Business FAQs"}</h2><p className="text-sm text-slate-500">{language === "fa" ? "دانش آزمایشی و محدود به همین مستأجر برای پاسخ‌گوی آفلاین." : "Tenant-scoped synthetic knowledge used by the offline retrieval demo."}</p></div><button onClick={() => setFaqs([...faqs, { title: "New FAQ", content: "" }])} className="shrink-0 rounded-lg border px-3 py-2 text-sm">{language === "fa" ? "افزودن پرسش" : "Add FAQ"}</button></div>{faqs.map((faq, index) => <div key={index} data-faq-index={index} className="rounded-xl border bg-slate-50 p-4"><input aria-label={`FAQ question ${index + 1}`} dir="auto" className="w-full bg-transparent text-start font-semibold outline-none" value={faq.title} onChange={(e) => setFaqs(faqs.map((f, i) => i === index ? { ...f, title: e.target.value } : f))}/><textarea aria-label={`FAQ answer ${index + 1}`} dir="auto" className="mt-2 w-full rounded-lg border bg-white p-3 text-start text-sm" rows={3} value={faq.content} onChange={(e) => setFaqs(faqs.map((f, i) => i === index ? { ...f, content: e.target.value } : f))}/></div>)}<button onClick={() => void saveFaqs()} className="rounded-xl bg-teal-700 px-5 py-3 text-sm font-semibold text-white">{language === "fa" ? "ذخیره پرسش‌ها" : "Save FAQs"}</button></div><section className="border-t pt-6"><h2 className="text-lg font-semibold">{language === "fa" ? "اسناد محلی" : "Local documents"}</h2><p className="mt-1 text-sm text-slate-500">Upload synthetic TXT, Markdown, or text-based PDF files up to 512 KiB. Nothing is fetched from the web or embedded by a paid model.</p><div className="mt-4 flex flex-wrap gap-3"><input aria-label="Synthetic knowledge document" type="file" accept=".txt,.md,.markdown,.pdf" onChange={(event) => setDocumentFile(event.target.files?.[0] ?? null)} className="max-w-full rounded-lg border bg-white p-2 text-sm"/><button disabled={!documentFile} onClick={() => void uploadDocument()} className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">Upload document</button></div><div className="mt-4 space-y-2">{documents.length === 0 && <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-500">No local documents uploaded.</p>}{documents.map((document) => <div key={document.document_id} className="flex items-center justify-between gap-3 rounded-lg border p-3"><div><p className="text-sm font-semibold">{document.title}</p><p className="text-xs text-slate-500">{document.filename} · {document.chunks_created} chunk(s)</p></div><button onClick={() => void deleteDocument(document.document_id)} className="rounded-lg border px-3 py-2 text-xs text-rose-700">Remove</button></div>)}</div></section></div>}
          {tab === "inbox" && knowledgeCandidate && <section aria-label="Knowledge gap review" dir={language === "fa" ? "rtl" : "ltr"} className="mx-4 mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 sm:mx-6"><h3 className="font-semibold text-amber-950">{language === "fa" ? "پیشنهاد بهبود دانش" : "Knowledge improvement candidate"}</h3><p className="mt-1 text-xs text-amber-900">{language === "fa" ? "این پرسش پیش از پیام انتقال ثبت شده است. وضعیت انتقال به‌تنهایی دلیل انتقال را مشخص نمی‌کند؛ آن را فقط به‌عنوان پیشنهاد بازبینی کنید." : "This question appeared before the handoff message. Handoff status alone does not reveal why it happened; review this only as a possible knowledge improvement."}</p><blockquote dir="auto" className="mt-3 border-s-2 border-amber-400 ps-3 text-start text-sm font-medium">{knowledgeCandidate.question}</blockquote><p className="mt-3 text-xs font-semibold text-amber-950">{language === "fa" ? "منابع احتمالاً مرتبط" : "Possibly relevant existing sources"}</p><p className="text-[11px] text-amber-800">{language === "fa" ? "پیش‌نمایش واژگانی است؛ نه استناد و نه اثبات استفاده پاسخ‌گو از این منابع." : "Lexical preview only—not a citation or proof that the responder used these sources."}</p>{knowledgeCandidate.relevantSources.length ? <ul className="mt-2 space-y-1">{knowledgeCandidate.relevantSources.map((source) => <li key={source.title} className="rounded-md bg-white/80 px-2 py-1 text-xs">{source.title}</li>)}</ul> : <p className="mt-2 rounded-md bg-white/80 px-2 py-1 text-xs text-amber-900">{language === "fa" ? "منبع مشابهی یافت نشد." : "No lexically similar source found."}</p>}<button onClick={draftFaqFromGap} className="mt-3 rounded-lg bg-amber-900 px-3 py-2 text-xs font-semibold text-white">{language === "fa" ? "ساخت پیش‌نویس پرسش متداول" : "Draft FAQ from question"}</button></section>}
          {tab === "inbox" && selectedReplyOutcome && selectedReplyOutcomeCopy && <section aria-label="Staff reply recovery" role="status" dir={language === "fa" ? "rtl" : "ltr"} className={`mx-4 mt-4 rounded-xl p-4 text-sm sm:mx-6 ${selectedReplyOutcome === "saved_refresh_failed" ? "bg-amber-50 text-amber-900" : "bg-rose-50 text-rose-800"}`}><p>{selectedReplyOutcomeCopy.message}</p>{selectedReplyOutcomeCopy.action && <button type="button" onClick={() => void refreshSavedReply()} className="mt-2 rounded-md border border-amber-300 bg-white px-3 py-1 text-xs font-semibold">{selectedReplyOutcomeCopy.action}</button>}</section>}
          {tab === "inbox" && <div><div className="mb-5"><h2 className="text-xl font-semibold">Shared inbox</h2><p className="text-sm text-slate-500">Saved customer conversations and human takeover.</p></div><div aria-label="Conversation analytics" className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">{[["Conversations", analytics.conversations], ["Awaiting reply", analytics.awaitingReply], ["Active handoffs", analytics.activeHandoffs], ["Resolved", analytics.resolved]].map(([label, value]) => <div key={label} className="rounded-xl border bg-slate-50 p-3"><p className="text-2xl font-semibold">{value}</p><p className="text-xs text-slate-500">{label}</p></div>)}</div><div className="grid min-h-[500px] gap-4 md:grid-cols-[240px_1fr]"><aside className="space-y-2">{conversations.map((item) => <button key={item.id} onClick={() => selectConversation(item.id)} className={`w-full rounded-xl border p-3 text-left ${selected === item.id ? "border-teal-600 bg-teal-50" : "bg-white"}`}><div className="flex justify-between text-sm font-semibold"><span>Conversation #{item.id}</span>{item.needs_response && <span className="text-amber-700">New</span>}</div><p dir="auto" className="mt-1 truncate text-start text-xs text-slate-500">{item.latest_message?.body ?? item.subject ?? "No message"}</p></button>)}</aside><article className="flex min-w-0 flex-col rounded-xl border bg-slate-50 p-4"><div className="mb-3 flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">{selectedHandoff ? selectedHandoff.status.replaceAll("_", " ") : "Saved conversation"}</span>{selectedHandoff?.status === "human_requested" && <button onClick={() => void action("assign")} className="rounded-lg bg-slate-900 px-3 py-2 text-xs text-white">Claim & take over</button>}{selectedHandoff?.status === "human_assigned" && <><button onClick={() => void action("release")} className="rounded-lg border px-3 py-2 text-xs">Release</button><button onClick={() => void action("resolve")} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs text-white">Resolve</button></>}</div>{messagesLoading && <p className="mb-3 rounded-lg bg-white p-3 text-sm text-slate-500">{language === "fa" ? "در حال بارگیری گفتگو…" : "Loading conversation…"}</p>}{messagesError && <p role="alert" className="mb-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-800">{messagesError}</p>}{knowledgeGap && <section aria-label="Knowledge gap review" dir={language === "fa" ? "rtl" : "ltr"} className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4"><h3 className="font-semibold text-amber-950">{language === "fa" ? "مرور شکاف دانش" : "Knowledge gap review"}</h3><p className="mt-1 text-xs text-amber-900">{language === "fa" ? "پاسخ مستند منتشر نشد و گفتگو به کارشناس منتقل شد. این دلیل عمومی است، نه ردیابی مدل." : "No sufficiently supported answer was published, so the conversation moved to staff. This is a public outcome explanation, not a model trace."}</p><blockquote dir="auto" className="mt-3 border-s-2 border-amber-400 ps-3 text-start text-sm font-medium">{knowledgeGap.question}</blockquote><div className="mt-3"><p className="text-xs font-semibold text-amber-950">{language === "fa" ? "منابع احتمالاً مرتبط" : "Possibly relevant existing sources"}</p><p className="text-[11px] text-amber-800">{language === "fa" ? "بر پایه هم‌پوشانی واژه‌ها؛ اثبات نمی‌کند پاسخ‌گو از این منابع استفاده کرده است." : "Lexical preview only; this does not prove the responder used these sources."}</p>{knowledgeGap.relevantSources.length ? <ul className="mt-2 space-y-1">{knowledgeGap.relevantSources.map((source) => <li key={source.title} className="rounded-md bg-white/80 px-2 py-1 text-xs">{source.title}</li>)}</ul> : <p className="mt-2 rounded-md bg-white/80 px-2 py-1 text-xs text-amber-900">{language === "fa" ? "منبع مشابهی یافت نشد." : "No lexically similar source found."}</p>}</div><button onClick={draftFaqFromGap} className="mt-3 rounded-lg bg-amber-900 px-3 py-2 text-xs font-semibold text-white">{language === "fa" ? "ساخت پیش‌نویس پرسش متداول" : "Draft FAQ from question"}</button></section>}<div data-conversation-message-count={messages.length} className="flex-1 space-y-2 overflow-auto">{messages.map((message) => <div data-conversation-message key={message.id} dir="auto" className={`max-w-[85%] rounded-xl p-3 text-start text-sm ${message.direction === "inbound" ? "bg-white" : "ms-auto bg-teal-700 text-white"}`}>{message.body}<div className="mt-1 text-[10px] opacity-60">{message.direction} {message.delivery_status ?? ""}</div></div>)}</div>{!messagesLoading && !messagesError && messages.length === 0 && <p className="rounded-lg bg-white p-3 text-sm text-slate-500">{language === "fa" ? "هنوز پیامی وجود ندارد." : "No messages yet."}</p>}<div className="mt-3 flex gap-2"><input dir="auto" className="min-w-0 flex-1 rounded-xl border bg-white p-3 text-start text-sm" placeholder={selectedHandoff?.status === "human_assigned" ? "Reply as staff…" : "Claim the handoff to reply"} disabled={selectedHandoff?.status !== "human_assigned"} value={reply} onChange={(e) => setReply(e.target.value)}/><button disabled={selectedHandoff?.status !== "human_assigned"} onClick={() => void sendReply()} className="rounded-xl bg-teal-700 px-4 text-sm font-semibold text-white disabled:opacity-40">Send</button></div></article></div></div>}
        </div>
      </section>
      <aside><div className="sticky top-6 rounded-2xl border border-slate-200 bg-[#e8f5f2] p-5"><p className="text-xs font-semibold uppercase tracking-wider text-teal-800">Live customer preview</p><h2 className="mt-1 font-semibold">Production widget contract</h2><p className="mt-2 text-sm text-slate-600">Open the bubble below. New messages use the real local API, persisted sessions and saved FAQs.</p><div className="mt-80 text-xs text-slate-500">Language: {language === "fa" ? "فارسی" : language === "en" ? "English" : "Automatic bilingual"}</div></div></aside>
    </div>
    <div className="fixed bottom-3 left-1/2 z-50 -translate-x-1/2 rounded-full bg-slate-900 px-4 py-2 text-xs text-white shadow-lg">{notice}</div>
    <PublicChatWidget widgetKey={widgetKey} sessionStorageKey="cxops.chat.owner-studio.preview.v1" transparentFrame={false} initialPollDelayMs={800} fetcher={localPreviewFetch}/>
  </main>;
}
