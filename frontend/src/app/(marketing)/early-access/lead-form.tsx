"use client";

import { CheckCircle2, LoaderCircle } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import {
  DEFAULT_CONSENT_TEXT,
  LEAD_INDUSTRIES,
  parseAvailability,
  type LeadAvailability,
  LEAD_LIMITS,
  interpretLeadResponse,
  toLeadPayload,
  validateLeadForm,
  type LeadField,
  type LeadFormInput,
} from "@/lib/commercial/leads";

const EMPTY: LeadFormInput = {
  companyName: "",
  contactName: "",
  email: "",
  website: "",
  industry: "",
  inquiry: "",
  consent: false,
  companyFax: "",
};

const inputClass =
  "mt-1.5 block w-full rounded-xl border bg-white px-3.5 py-2.5 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-violet-500 focus:ring-4 focus:ring-violet-100";

type Phase =
  | { kind: "editing" }
  | { kind: "submitting" }
  | { kind: "done"; message: string }
  | { kind: "error"; message: string };

export function LeadForm() {
  const id = useId();
  const [values, setValues] = useState<LeadFormInput>(EMPTY);
  const [errors, setErrors] = useState<Partial<Record<LeadField, string>>>({});
  const [phase, setPhase] = useState<Phase>({ kind: "editing" });
  // null = still checking. The backend flag is off until the release of lead
  // capture is approved; visitors should learn that before filling the form.
  const [availability, setAvailability] = useState<LeadAvailability>({
    accepting: null,
    consentText: DEFAULT_CONSENT_TEXT,
    contactEmail: null,
  });
  const accepting = availability.accepting;
  const confirmationRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // The confirmation is much shorter than the form; bring it into view and
    // move focus to it so screen-reader and mobile users notice the outcome.
    if (phase.kind === "done") {
      confirmationRef.current?.scrollIntoView({ block: "center" });
      confirmationRef.current?.focus();
    }
  }, [phase.kind]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/public/leads", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((body: unknown) => {
        if (!cancelled) {
          setAvailability(parseAvailability(body));
        }
      })
      .catch(() => {
        // Unknown -> keep defaults; the submit path reports closure.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function update<K extends keyof LeadFormInput>(key: K, value: LeadFormInput[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    if (key in errors) {
      setErrors((current) => {
        const next = { ...current };
        delete next[key as LeadField];
        return next;
      });
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (phase.kind === "submitting") {
      return;
    }
    const validation = validateLeadForm(values);
    setErrors(validation.errors);
    if (!validation.ok) {
      const first = Object.keys(validation.errors)[0];
      document.getElementById(`${id}-${first}`)?.focus();
      return;
    }
    setPhase({ kind: "submitting" });
    let response: Response;
    try {
      response = await fetch("/api/public/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toLeadPayload(values)),
      });
    } catch {
      setPhase({ kind: "error", message: "We could not reach our servers. Check your connection and try again." });
      return;
    }
    const outcome = await interpretLeadResponse(response);
    switch (outcome.kind) {
      case "received":
        setPhase({ kind: "done", message: outcome.message });
        return;
      case "closed":
        // Show the closed state (with the contact fallback, if any).
        setAvailability((current) => ({ ...current, accepting: false }));
        setPhase({ kind: "editing" });
        return;
      case "rate_limited":
        setPhase({
          kind: "error",
          message: "We have received several requests recently. Please try again later.",
        });
        return;
      case "invalid":
        setErrors(outcome.fieldErrors);
        setPhase({ kind: "error", message: outcome.message });
        return;
      default:
        setPhase({ kind: "error", message: "Something went wrong on our side. Please try again in a moment." });
    }
  }

  if (phase.kind === "done") {
    return (
      <div
        ref={confirmationRef}
        tabIndex={-1}
        role="status"
        className="rounded-3xl border border-emerald-200 bg-emerald-50 p-8 text-center outline-none"
      >
        <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" aria-hidden="true" />
        <h2 className="mt-4 text-xl font-semibold text-slate-950">Request received</h2>
        <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-slate-600">{phase.message}</p>
        <p className="mx-auto mt-3 max-w-md text-xs leading-5 text-slate-500">
          Submitting a request does not create an account or start any charges.
        </p>
      </div>
    );
  }

  if (accepting === false) {
    return (
      <div role="status" className="rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-sm">
        <h2 className="text-xl font-semibold text-slate-950">Early-access requests open soon</h2>
        <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-slate-600">
          We are preparing to welcome our first businesses and are not accepting online
          requests just yet.
        </p>
        <ContactFallback email={availability.contactEmail} />
      </div>
    );
  }

  const field = (key: LeadField) => ({
    id: `${id}-${key}`,
    "aria-invalid": errors[key] ? true : undefined,
    "aria-describedby": errors[key] ? `${id}-${key}-error` : undefined,
  });
  const border = (key: LeadField) => (errors[key] ? "border-rose-400" : "border-slate-300");
  const error = (key: LeadField) =>
    errors[key] ? (
      <p id={`${id}-${key}-error`} className="mt-1.5 text-xs text-rose-600">
        {errors[key]}
      </p>
    ) : null;

  return (
    <form
      noValidate
      onSubmit={onSubmit}
      className="relative rounded-3xl border border-slate-200 bg-white p-6 shadow-[0_20px_60px_rgba(15,23,42,0.08)] sm:p-8"
      aria-describedby={phase.kind === "error" ? `${id}-form-error` : undefined}
    >
      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <label htmlFor={`${id}-companyName`} className="text-sm font-medium text-slate-800">
            Business name
          </label>
          <input
            {...field("companyName")}
            className={`${inputClass} ${border("companyName")}`}
            autoComplete="organization"
            maxLength={LEAD_LIMITS.companyName}
            value={values.companyName}
            onChange={(event) => update("companyName", event.target.value)}
          />
          {error("companyName")}
        </div>
        <div>
          <label htmlFor={`${id}-contactName`} className="text-sm font-medium text-slate-800">
            Your name
          </label>
          <input
            {...field("contactName")}
            className={`${inputClass} ${border("contactName")}`}
            autoComplete="name"
            maxLength={LEAD_LIMITS.contactName}
            value={values.contactName}
            onChange={(event) => update("contactName", event.target.value)}
          />
          {error("contactName")}
        </div>
        <div>
          <label htmlFor={`${id}-email`} className="text-sm font-medium text-slate-800">
            Work email
          </label>
          <input
            {...field("email")}
            type="email"
            inputMode="email"
            className={`${inputClass} ${border("email")}`}
            autoComplete="email"
            maxLength={LEAD_LIMITS.email}
            value={values.email}
            onChange={(event) => update("email", event.target.value)}
          />
          {error("email")}
        </div>
        <div>
          <label htmlFor={`${id}-website`} className="text-sm font-medium text-slate-800">
            Website <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <input
            {...field("website")}
            inputMode="url"
            placeholder="yourbusiness.com"
            className={`${inputClass} ${border("website")}`}
            autoComplete="url"
            maxLength={LEAD_LIMITS.website}
            value={values.website}
            onChange={(event) => update("website", event.target.value)}
          />
          {error("website")}
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={`${id}-industry`} className="text-sm font-medium text-slate-800">
            Type of business
          </label>
          <select
            {...field("industry")}
            className={`${inputClass} ${border("industry")}`}
            value={values.industry}
            onChange={(event) => update("industry", event.target.value)}
          >
            <option value="">Choose one…</option>
            {LEAD_INDUSTRIES.map((industry) => (
              <option key={industry.value} value={industry.value}>
                {industry.label}
              </option>
            ))}
          </select>
          {error("industry")}
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={`${id}-inquiry`} className="text-sm font-medium text-slate-800">
            What would you like the assistant to help with?{" "}
            <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <textarea
            {...field("inquiry")}
            rows={4}
            className={`${inputClass} ${border("inquiry")}`}
            maxLength={LEAD_LIMITS.inquiry}
            value={values.inquiry}
            onChange={(event) => update("inquiry", event.target.value)}
          />
          <p className="mt-1 text-right text-xs text-slate-400">
            {values.inquiry.length}/{LEAD_LIMITS.inquiry}
          </p>
          {error("inquiry")}
        </div>

        {/* Honeypot: hidden from people and assistive tech. */}
        <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
          <label htmlFor={`${id}-fax`}>Company fax</label>
          <input
            id={`${id}-fax`}
            tabIndex={-1}
            autoComplete="off"
            value={values.companyFax}
            onChange={(event) => update("companyFax", event.target.value)}
          />
        </div>

        <div className="sm:col-span-2">
          <div className="flex items-start gap-3">
            <input
              {...field("consent")}
              type="checkbox"
              className="mt-1 h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-violet-500"
              checked={values.consent}
              onChange={(event) => update("consent", event.target.checked)}
            />
            <label htmlFor={`${id}-consent`} className="text-sm leading-6 text-slate-600">
              {availability.consentText}
            </label>
          </div>
          {error("consent")}
        </div>
      </div>

      {phase.kind === "error" ? (
        <p
          id={`${id}-form-error`}
          role="alert"
          className="mt-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
        >
          {phase.message}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={phase.kind === "submitting"}
        className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-500"
      >
        {phase.kind === "submitting" ? (
          <>
            <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> Sending…
          </>
        ) : (
          "Request a demo"
        )}
      </button>
      <p className="mt-3 text-center text-xs text-slate-400">
        A request does not create an account or start any charges.
      </p>
    </form>
  );
}

function ContactFallback({ email }: { email: string | null }) {
  if (!email) {
    return (
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-600">
        Please check back shortly.
      </p>
    );
  }
  return (
    <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-600">
      In the meantime you can reach us at{" "}
      <a href={`mailto:${email}`} className="font-medium text-violet-700 underline underline-offset-4">
        {email}
      </a>
      .
    </p>
  );
}
