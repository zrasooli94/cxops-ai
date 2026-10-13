import { BookOpenCheck, CheckCircle2, Hand, MessageCircle, UserRound } from "lucide-react";

// Illustrative product preview (Phase 1Q). Pure HTML/CSS - no screenshots of a
// real customer, no real data. The caption says it is illustrative.

export function ProductPreview() {
  return (
    <figure className="relative mx-auto w-full max-w-[520px]">
      <div
        aria-hidden="true"
        className="absolute -inset-6 rounded-[2rem] bg-[radial-gradient(circle_at_30%_20%,rgba(115,87,255,0.18),transparent_55%),radial-gradient(circle_at_80%_80%,rgba(66,165,255,0.16),transparent_55%)]"
      />
      <div className="relative grid gap-4 sm:grid-cols-[minmax(0,1.25fr)_minmax(0,0.85fr)]">
        {/* Visitor-facing widget */}
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_24px_60px_rgba(15,23,42,0.12)]">
          <div className="flex items-center gap-2.5 bg-[#0f766e] px-4 py-3 text-white">
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-white/15">
              <MessageCircle className="h-4 w-4" strokeWidth={1.8} aria-hidden="true" />
            </span>
            <div>
              <p className="text-sm font-semibold">Riverside Auto Care</p>
              <p className="text-[11px] text-teal-100">AI assistant · team available</p>
            </div>
          </div>
          <div className="space-y-3 px-4 py-4 text-[13px] leading-5">
            <p className="max-w-[85%] rounded-2xl rounded-tl-sm bg-slate-100 px-3 py-2 text-slate-800">
              Hi! Ask me about bookings, opening hours or our services.
            </p>
            <p className="ml-auto max-w-[80%] rounded-2xl rounded-tr-sm bg-[#0f766e] px-3 py-2 text-white">
              Can I bring my car in on Saturday for a service?
            </p>
            <div className="max-w-[88%] rounded-2xl rounded-tl-sm bg-slate-100 px-3 py-2 text-slate-800">
              We take service bookings Monday to Saturday, 8am-1pm on Saturdays.
              <span className="mt-1.5 flex items-center gap-1 text-[11px] text-slate-500">
                <BookOpenCheck className="h-3 w-3" aria-hidden="true" /> From: Opening hours
              </span>
            </div>
            <p className="ml-auto max-w-[80%] rounded-2xl rounded-tr-sm bg-[#0f766e] px-3 py-2 text-white">
              Great - can someone confirm a time for me?
            </p>
            <p className="flex items-center gap-1.5 text-[11px] font-medium text-amber-700">
              <Hand className="h-3.5 w-3.5" aria-hidden="true" /> Connecting you with our team…
            </p>
          </div>
          <div className="border-t border-slate-100 px-4 py-3">
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[12px] text-slate-400">
              Type your message…
            </div>
          </div>
        </div>

        {/* Staff-facing handoff queue */}
        <div className="space-y-3 sm:pt-10">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_16px_40px_rgba(15,23,42,0.10)]">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              Handoff queue
            </p>
            <div className="mt-3 flex items-start gap-3 rounded-xl bg-amber-50 p-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-700">
                <UserRound className="h-4 w-4" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p className="text-[13px] font-semibold text-slate-900">Website visitor</p>
                <p className="truncate text-[12px] text-slate-600">Wants a Saturday service slot</p>
                <p className="mt-1 text-[11px] font-medium text-amber-700">Waiting for a person</p>
              </div>
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_16px_40px_rgba(15,23,42,0.08)]">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              This month
            </p>
            <div className="mt-3 space-y-2.5">
              <PreviewMeter label="AI conversations" value="Within allowance" share={38} />
              <PreviewMeter label="Estimated AI cost" value="Under limit" share={22} />
            </div>
            <p className="mt-3 flex items-center gap-1.5 text-[11px] text-emerald-700">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> Chatbot live on 2 approved domains
            </p>
          </div>
        </div>
      </div>
      <figcaption className="relative mt-5 text-center text-xs text-slate-400">
        Illustrative preview. Business name and conversation are examples.
      </figcaption>
    </figure>
  );
}

function PreviewMeter({ label, value, share }: { label: string; value: string; share: number }) {
  return (
    <div>
      <div className="flex justify-between text-[12px]">
        <span className="text-slate-600">{label}</span>
        <span className="font-medium text-slate-900">{value}</span>
      </div>
      <div className="mt-1.5 h-1.5 rounded-full bg-slate-100">
        <div
          className="h-1.5 rounded-full bg-gradient-to-r from-[#7357ff] to-[#42a5ff]"
          style={{ width: `${share}%` }}
        />
      </div>
    </div>
  );
}
