"use client";

import Link from "next/link";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Circle,
  KeyRound,
  LoaderCircle,
  Plus,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  Card,
  CopyField,
  LoadingBlock,
  Notice,
  PageHeader,
  StatusPill,
  buttonPrimary,
  buttonSecondary,
  formatDate,
  inputClass,
} from "@/components/workspace/ui";
import { useAuthorization } from "@/lib/authorization/context";
import { CommercialApiError, commercialApi, type OnboardingState } from "@/lib/commercial/client";
import {
  buildInstallSnippet,
  suggestOrigins,
  validateAllowedOrigin,
} from "@/lib/commercial/install";
import {
  STEP_DESCRIPTIONS,
  firstOpenStep,
  lifecycleInfo,
  progressPercent,
} from "@/lib/commercial/onboarding";
import { resolveTheme } from "@/lib/public-chat/theme";

const THEME_LABELS: Record<string, string> = {
  default: "Teal",
  midnight: "Midnight",
  ocean: "Ocean",
  forest: "Forest",
};

// Domains entered before the chatbot exists are only a draft (the widget row
// does not exist yet). Keep them for this tab so an interrupted setup resumes
// with them. Origins are not secret; nothing else is stored.
const DRAFT_ORIGINS_KEY = "cxops.setup.draftOrigins";

function readDraftOrigins(organizationId: number): string[] {
  try {
    const raw = window.sessionStorage.getItem(`${DRAFT_ORIGINS_KEY}.${organizationId}`);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string").slice(0, 20)
      : [];
  } catch {
    return [];
  }
}

function writeDraftOrigins(organizationId: number, origins: string[] | null) {
  try {
    const key = `${DRAFT_ORIGINS_KEY}.${organizationId}`;
    if (origins === null) {
      window.sessionStorage.removeItem(key);
    } else {
      window.sessionStorage.setItem(key, JSON.stringify(origins));
    }
  } catch {
    // Storage blocked: the draft simply lives in memory.
  }
}

function errorText(exc: unknown): string {
  return exc instanceof CommercialApiError ? exc.message : "Something went wrong. Please try again.";
}

export default function SetupPage() {
  const { organizationId } = useAuthorization();
  const [state, setState] = useState<OnboardingState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  // The widget key exists only in memory, only right after creation. It is
  // never written to storage: the server cannot show it again either.
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [draftOrigins, setDraftOrigins] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const next = await commercialApi.onboardingState();
      setState(next);
      setLoadError(null);
      return next;
    } catch (exc) {
      setLoadError(errorText(exc));
      return null;
    }
  }, []);

  useEffect(() => {
    // Organization switches remount this page (keyed provider), so there is no
    // previous tenant's state - or widget key - to clear here.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount writes the result of an external request (same pattern as the knowledge page)
    void load().then((next) => {
      if (next) {
        setActive(firstOpenStep(next.steps));
        setDraftOrigins(
          next.widget ? next.widget.allowed_origins : readDraftOrigins(next.organization.id),
        );
      }
    });
  }, [load]);

  useEffect(() => {
    // Keep the active step visible in the horizontal (phone) stepper.
    document
      .querySelector('[aria-label="Setup steps"] [aria-current="step"]')
      ?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [active]);

  if (loadError && !state) {
    return (
      <main className="px-5 py-8 sm:px-8">
        <Notice tone="bad">{loadError}</Notice>
      </main>
    );
  }
  if (!state || state.organization.id !== organizationId || active === null) {
    return (
      <main className="px-5 py-8 sm:px-8">
        <LoadingBlock label="Loading your setup…" />
      </main>
    );
  }

  const lifecycle = lifecycleInfo(state.organization.lifecycle_status);
  const percent = progressPercent(state.steps);
  const readOnly = !state.can_edit;

  return (
    <main className="space-y-6 px-5 py-8 sm:px-8 lg:py-10">
      <PageHeader
        eyebrow="Setup"
        title="Get your assistant ready"
        description="Work through the steps in any order. Your progress is saved as you go, so you can leave and come back."
        actions={<StatusPill tone={lifecycle.tone}>{lifecycle.label}</StatusPill>}
      />

      {readOnly ? (
        <Notice tone="info">
          You can view setup progress. Only owners and administrators can change these settings.
        </Notice>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        <nav aria-label="Setup steps" className="min-w-0 space-y-3">
          <div>
            <div className="flex justify-between text-xs text-slate-500">
              <span>Progress</span>
              <span>{percent}%</span>
            </div>
            <div className="mt-1.5 h-1.5 rounded-full bg-slate-100">
              <div
                className="h-1.5 rounded-full bg-gradient-to-r from-[#7357ff] to-[#42a5ff]"
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
          {/* Phones: a compact horizontal stepper so the active step's form
              stays above the fold; desktop: the full vertical list. */}
          <ol className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 [scrollbar-width:none] lg:mx-0 lg:block lg:space-y-1 lg:overflow-visible lg:px-0">
            {state.steps.map((step, index) => (
              <li key={step.key} className="shrink-0 lg:shrink">
                <button
                  type="button"
                  onClick={() => {
                    setActive(step.key);
                    if (window.matchMedia("(max-width: 1023px)").matches) {
                      document.getElementById("setup-step-panel")?.scrollIntoView({ block: "start" });
                    }
                  }}
                  aria-current={active === step.key ? "step" : undefined}
                  aria-label={`${index + 1}. ${step.title}: ${
                    step.complete ? "complete" : step.required ? "required" : "recommended"
                  }`}
                  className={`flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition ${
                    active === step.key ? "bg-white shadow-sm ring-1 ring-slate-200" : "hover:bg-white/70"
                  }`}
                >
                  {step.complete ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
                  ) : (
                    <Circle className="mt-0.5 h-4 w-4 shrink-0 text-slate-300" aria-hidden="true" />
                  )}
                  <span aria-hidden="true">
                    <span className="block whitespace-nowrap font-medium text-slate-900">
                      {index + 1}. {step.title}
                    </span>
                    <span className="hidden text-xs text-slate-500 lg:block">
                      {step.complete ? "Complete" : step.required ? "Required" : "Recommended"}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <div id="setup-step-panel" className="min-w-0 scroll-mt-4 space-y-5">
          {state.warnings.length > 0 ? (
            <div className="space-y-2">
              {state.warnings.map((warning) => (
                <Notice key={warning} tone="warn">
                  <span className="flex gap-2">
                    <AlertTriangle className="mt-1 h-4 w-4 shrink-0" aria-hidden="true" />
                    {warning}
                  </span>
                </Notice>
              ))}
            </div>
          ) : null}

          <StepPanel
            step={active}
            state={state}
            readOnly={readOnly}
            freshKey={freshKey}
            draftOrigins={draftOrigins}
            setDraftOrigins={(origins) => {
              setDraftOrigins(origins);
              if (!state.widget) {
                writeDraftOrigins(state.organization.id, origins);
              }
            }}
            onKeyIssued={setFreshKey}
            onSaved={async (goTo) => {
              const next = await load();
              if (next && goTo) {
                setActive(goTo);
              }
            }}
          />
        </div>
      </div>
    </main>
  );
}

interface StepProps {
  step: string;
  state: OnboardingState;
  readOnly: boolean;
  freshKey: string | null;
  draftOrigins: string[];
  setDraftOrigins: (origins: string[]) => void;
  onKeyIssued: (key: string) => void;
  onSaved: (goTo?: string) => Promise<void>;
}

function StepPanel(props: StepProps) {
  const title = props.state.steps.find((step) => step.key === props.step)?.title ?? "";
  return (
    <Card>
      <h2 className="text-lg font-semibold tracking-[-0.02em] text-slate-950">{title}</h2>
      <p className="mt-1 text-sm text-slate-500">{STEP_DESCRIPTIONS[props.step]}</p>
      <div className="mt-6">
        {props.step === "business" ? <BusinessStep {...props} /> : null}
        {props.step === "workspace" ? <WorkspaceStep {...props} /> : null}
        {props.step === "domain" ? <DomainStep {...props} /> : null}
        {props.step === "chatbot" ? <ChatbotStep {...props} /> : null}
        {props.step === "knowledge" ? <KnowledgeStep {...props} /> : null}
        {props.step === "install" ? <InstallStep {...props} /> : null}
        {props.step === "preview" ? <PreviewStep {...props} /> : null}
        {props.step === "review" ? <ReviewStep {...props} /> : null}
      </div>
    </Card>
  );
}

function useSubmit() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await action();
      setSaved(true);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, saved, run };
}

function SubmitRow({ busy, saved, label = "Save", disabled }: { busy: boolean; saved: boolean; label?: string; disabled?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <button type="submit" className={buttonPrimary} disabled={busy || disabled}>
        {busy ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
        {label}
      </button>
      {saved ? (
        <span role="status" className="flex items-center gap-1 text-sm text-emerald-700">
          <Check className="h-4 w-4" aria-hidden="true" /> Saved
        </span>
      ) : null}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-800">{label}</span>
      {hint ? <span className="mt-0.5 block text-xs text-slate-500">{hint}</span> : null}
      {children}
    </label>
  );
}

function BusinessStep({ state, readOnly, onSaved }: StepProps) {
  const [name, setName] = useState(state.organization.name);
  const [industry, setIndustry] = useState(state.organization.industry ?? "");
  const [website, setWebsite] = useState(state.organization.website_url ?? "");
  const { busy, error, saved, run } = useSubmit();
  const locked = state.organization.lifecycle_status === "pending_review";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await commercialApi.updateBusiness({ name, industry, website_url: website || null });
      await onSaved("domain");
    });
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {locked ? <Notice tone="info">Business details are locked while CXOps reviews your workspace.</Notice> : null}
      <Field label="Business name">
        <input className={inputClass} value={name} maxLength={255} disabled={readOnly || locked} onChange={(e) => setName(e.target.value)} required />
      </Field>
      <Field label="Industry" hint="For example: automotive services, dental clinic, online store.">
        <input className={inputClass} value={industry} maxLength={120} disabled={readOnly || locked} onChange={(e) => setIndustry(e.target.value)} />
      </Field>
      <Field label="Business website" hint="Your main website address.">
        <input className={inputClass} value={website} maxLength={255} inputMode="url" placeholder="yourbusiness.com" disabled={readOnly || locked} onChange={(e) => setWebsite(e.target.value)} />
      </Field>
      {error ? <Notice tone="bad">{error}</Notice> : null}
      {!readOnly && !locked ? <SubmitRow busy={busy} saved={saved} label="Save and continue" /> : null}
    </form>
  );
}

function WorkspaceStep({ state }: StepProps) {
  return (
    <div className="space-y-4 text-sm text-slate-600">
      <p>
        Your workspace has <strong className="text-slate-900">{state.team.members}</strong>{" "}
        {state.team.members === 1 ? "member" : "members"} and{" "}
        <strong className="text-slate-900">{state.team.pending_invitations}</strong> pending{" "}
        {state.team.pending_invitations === 1 ? "invitation" : "invitations"}.
      </p>
      <p>
        Invite the people who will answer customers when the assistant hands a conversation
        over. Support agents can reply; supervisors can also review AI actions; viewers can only
        read.
      </p>
      <Link href="/team" className={buttonSecondary}>
        Manage team
      </Link>
    </div>
  );
}

function OriginEditor({
  origins,
  setOrigins,
  website,
  readOnly,
}: {
  origins: string[];
  setOrigins: (origins: string[]) => void;
  website: string | null;
  readOnly: boolean;
}) {
  const [candidate, setCandidate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const suggestions = suggestOrigins(website).filter((origin) => !origins.includes(origin));

  const add = (value: string) => {
    const problem = validateAllowedOrigin(value);
    if (problem) {
      setError(problem);
      return;
    }
    if (origins.includes(value.trim())) {
      setError("That address is already listed.");
      return;
    }
    if (origins.length >= 20) {
      setError("You can list up to 20 addresses.");
      return;
    }
    setOrigins([...origins, value.trim()]);
    setCandidate("");
    setError(null);
  };

  return (
    <div className="space-y-3">
      <ul className="space-y-2">
        {origins.length === 0 ? (
          <li className="rounded-lg border border-dashed border-slate-300 px-3 py-3 text-sm text-slate-500">
            No addresses yet. The chatbot will not start on any website until you add one.
          </li>
        ) : null}
        {origins.map((origin) => (
          <li key={origin} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2">
            <span className="truncate font-mono text-sm text-slate-800">{origin}</span>
            {!readOnly ? (
              <button
                type="button"
                className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-600"
                aria-label={`Remove ${origin}`}
                onClick={() => setOrigins(origins.filter((item) => item !== origin))}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {!readOnly ? (
        <>
          <div className="flex gap-2">
            <label className="sr-only" htmlFor="origin-input">
              Website address
            </label>
            <input
              id="origin-input"
              className={`${inputClass} mt-0`}
              placeholder="https://www.yourbusiness.com"
              value={candidate}
              onChange={(e) => {
                setCandidate(e.target.value);
                setError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  add(candidate);
                }
              }}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "origin-error" : undefined}
            />
            <button type="button" className={buttonSecondary} onClick={() => add(candidate)}>
              <Plus className="h-4 w-4" aria-hidden="true" /> Add
            </button>
          </div>
          {error ? (
            <p id="origin-error" className="text-xs text-rose-600">
              {error}
            </p>
          ) : null}
          {suggestions.length > 0 ? (
            <p className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              Suggested:
              {suggestions.map((origin) => (
                <button key={origin} type="button" onClick={() => add(origin)} className="rounded-full border border-slate-200 px-2.5 py-1 font-mono text-slate-700 hover:border-violet-300">
                  + {origin}
                </button>
              ))}
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function DomainStep({ state, readOnly, draftOrigins, setDraftOrigins, onSaved }: StepProps) {
  const { busy, error, saved, run } = useSubmit();
  const hasWidget = state.widget !== null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!hasWidget) {
      void onSaved("chatbot");
      return;
    }
    void run(async () => {
      await commercialApi.updateWidget({ allowed_origins: draftOrigins });
      await onSaved("chatbot");
    });
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-sm leading-6 text-slate-600">
        List every exact address where the chat should appear, including both{" "}
        <code className="font-mono text-xs">https://yourbusiness.com</code> and{" "}
        <code className="font-mono text-xs">https://www.yourbusiness.com</code> if you use both.
        Wildcards are not allowed. For a local test page, <code className="font-mono text-xs">http://localhost:PORT</code> works too.
      </p>
      <OriginEditor origins={draftOrigins} setOrigins={setDraftOrigins} website={state.organization.website_url} readOnly={readOnly} />
      {!hasWidget ? (
        <Notice tone="info">These addresses are saved when you create your chatbot in the next step.</Notice>
      ) : null}
      {error ? <Notice tone="bad">{error}</Notice> : null}
      {!readOnly ? <SubmitRow busy={busy} saved={saved} label={hasWidget ? "Save domains" : "Continue"} /> : null}
    </form>
  );
}

function ThemePicker({ value, options, onChange, disabled }: { value: string; options: string[]; onChange: (v: string) => void; disabled: boolean }) {
  return (
    <fieldset>
      <legend className="text-sm font-medium text-slate-800">Colour theme</legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((option) => {
          const theme = resolveTheme(option);
          return (
            <label
              key={option}
              className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-violet-500 ${
                value === option ? "border-violet-500 ring-2 ring-violet-100" : "border-slate-200"
              }`}
            >
              <input type="radio" name="theme" value={option} checked={value === option} disabled={disabled} onChange={() => onChange(option)} className="sr-only" />
              <span aria-hidden="true" className="h-4 w-4 rounded-full" style={{ background: theme.header.background }} />
              {THEME_LABELS[option] ?? option}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function ChatbotStep({ state, readOnly, draftOrigins, onKeyIssued, onSaved }: StepProps) {
  const widget = state.widget;
  const [displayName, setDisplayName] = useState(widget?.display_name ?? state.organization.name);
  const [welcome, setWelcome] = useState(
    widget?.welcome_message ?? `Hi! I'm the ${state.organization.name} assistant. How can I help?`,
  );
  const [theme, setTheme] = useState(widget?.theme_token ?? "default");
  const { busy, error, saved, run } = useSubmit();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (widget) {
        // Only send the theme when it changed: an operator-assigned theme
        // (not self-selectable) must not block editing the other fields.
        await commercialApi.updateWidget({
          display_name: displayName,
          welcome_message: welcome,
          ...(theme !== widget.theme_token ? { theme_token: theme } : {}),
        });
        await onSaved("knowledge");
      } else {
        const created = await commercialApi.createWidget({
          display_name: displayName,
          welcome_message: welcome,
          theme_token: theme,
          allowed_origins: draftOrigins,
        });
        onKeyIssued(created.public_widget_key);
        writeDraftOrigins(state.organization.id, null);
        await onSaved("install");
      }
    });
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Assistant name" hint="Shown in the chat header, e.g. your business name.">
        <input className={inputClass} value={displayName} maxLength={100} required disabled={readOnly} onChange={(e) => setDisplayName(e.target.value)} />
      </Field>
      <Field label="Welcome message" hint="The first message visitors see.">
        <textarea className={inputClass} rows={3} value={welcome} maxLength={500} required disabled={readOnly} onChange={(e) => setWelcome(e.target.value)} />
      </Field>
      <ThemePicker value={theme} options={state.theme_options} onChange={setTheme} disabled={readOnly} />
      {!widget ? (
        <Notice tone="info">
          Creating the chatbot issues its website key once. Copy the installation snippet on the
          next screen - the key cannot be shown again. The chatbot stays off until your
          workspace is approved.
        </Notice>
      ) : null}
      {error ? <Notice tone="bad">{error}</Notice> : null}
      {!readOnly ? <SubmitRow busy={busy} saved={saved} label={widget ? "Save chatbot" : "Create chatbot"} /> : null}
    </form>
  );
}

function KnowledgeStep({ state }: StepProps) {
  return (
    <div className="space-y-4 text-sm leading-6 text-slate-600">
      <p>
        Your knowledge base has <strong className="text-slate-900">{state.knowledge.documents}</strong>{" "}
        {state.knowledge.documents === 1 ? "document" : "documents"}.
      </p>
      <div className="rounded-xl bg-slate-50 p-4">
        <p className="font-medium text-slate-900">What to prepare</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>Opening hours, locations and contact details</li>
          <li>Services or products, and what is included</li>
          <li>Prices you already publish, booking and cancellation rules</li>
          <li>Returns, warranties and other policies</li>
          <li>Your ten most common customer questions, with your answers</li>
        </ul>
        <p className="mt-3 text-xs text-slate-500">
          Only add information you are happy for the assistant to share with the public. Do not
          add personal data about customers or staff.
        </p>
      </div>
      {state.knowledge.upload_available ? (
        <Link href="/knowledge" className={buttonSecondary}>
          Add knowledge
        </Link>
      ) : (
        <Notice tone="info">
          Uploading knowledge uses paid AI processing, so it unlocks once CXOps approves your
          workspace. Prepare your documents now; you can add them right after approval.
        </Notice>
      )}
    </div>
  );
}

function InstallStep({ state, freshKey }: StepProps) {
  const [origin, setOrigin] = useState("");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the app origin exists only in the browser; reading it after mount avoids a hydration mismatch
    setOrigin(window.location.origin);
  }, []);
  if (!state.widget) {
    return <Notice tone="info">Create your chatbot first; the installation snippet appears here.</Notice>;
  }
  const snippet = buildInstallSnippet(origin || "https://your-cxops-address", freshKey);
  return (
    <div className="space-y-5 text-sm leading-6 text-slate-600">
      {freshKey ? (
        <Notice tone="warn">
          <span className="flex gap-2">
            <KeyRound className="mt-1 h-4 w-4 shrink-0" aria-hidden="true" />
            This snippet contains your website key. Copy it now - it will not be shown again. If
            you lose it, an administrator can rotate the key in chatbot settings.
          </span>
        </Notice>
      ) : (
        <Notice tone="info">
          Your website key was shown when the chatbot was created. Replace{" "}
          <code className="font-mono text-xs">YOUR_WIDGET_KEY</code> with it, or rotate the key in{" "}
          <Link href="/settings/public-chat" className="font-medium underline underline-offset-4">
            chatbot settings
          </Link>{" "}
          to get a new one (after approval).
        </Notice>
      )}
      <CopyField label="Installation snippet" value={snippet} />
      <ol className="list-decimal space-y-1 pl-5">
        <li>Open your website editor or theme template.</li>
        <li>Paste the snippet just before the closing <code className="font-mono text-xs">&lt;/body&gt;</code> tag on every page.</li>
        <li>Publish. The chat button appears in the bottom-right corner on approved domains once the chatbot is switched on.</li>
      </ol>
      <p>
        Installation status:{" "}
        {state.widget.last_session_at ? (
          <StatusPill tone="ok">Detected - last chat {formatDate(state.widget.last_session_at)}</StatusPill>
        ) : (
          <StatusPill tone="muted">Not detected yet</StatusPill>
        )}
      </p>
    </div>
  );
}

function PreviewStep({ state }: StepProps) {
  if (!state.widget) {
    return <Notice tone="info">Create your chatbot to see a preview.</Notice>;
  }
  const theme = resolveTheme(state.widget.theme_token);
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        A static preview of what visitors will see. It does not send messages or use AI.
      </p>
      <div
        className="mx-auto max-w-sm overflow-hidden border"
        style={{
          background: theme.panel.background,
          borderColor: theme.panel.border,
          borderRadius: theme.panel.radius,
          boxShadow: theme.panel.shadow,
        }}
        aria-label="Chatbot preview"
      >
        <div className="px-4 py-3" style={{ background: theme.header.background, color: theme.header.foreground }}>
          <p className="text-sm font-semibold">{state.widget.display_name}</p>
        </div>
        <div className="space-y-3 p-4 text-sm">
          <p className="max-w-[85%] px-3 py-2" style={{ background: theme.bubble.inboundBackground, color: theme.bubble.inboundForeground, borderRadius: theme.bubble.radius }}>
            {state.widget.welcome_message}
          </p>
          <p className="ml-auto max-w-[75%] px-3 py-2" style={{ background: theme.bubble.outboundBackground, color: theme.bubble.outboundForeground, borderRadius: theme.bubble.radius }}>
            What are your opening hours?
          </p>
        </div>
        <div className="border-t px-4 py-3" style={{ borderColor: theme.panel.border }}>
          <div className="rounded-lg border px-3 py-2 text-xs" style={{ background: theme.composer.background, borderColor: theme.composer.border, color: theme.composer.placeholder }}>
            Type your message…
          </div>
        </div>
      </div>
    </div>
  );
}

function ReviewStep({ state, readOnly, onSaved }: StepProps) {
  const { busy, error, run } = useSubmit();
  // The lifecycle status shown in the shell comes from server-loaded
  // authorization; refresh it after a status change.
  const { refresh } = useAuthorization();
  const status = state.organization.lifecycle_status;
  return (
    <div className="space-y-5">
      <ul className="space-y-2">
        {state.steps
          .filter((step) => step.key !== "review")
          .map((step) => (
            <li key={step.key} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-sm">
              <span className="flex items-center gap-2 text-slate-800">
                {step.complete ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
                ) : (
                  <Circle className="h-4 w-4 text-slate-300" aria-hidden="true" />
                )}
                {step.title}
              </span>
              <StatusPill tone={step.complete ? "ok" : step.required ? "warn" : "muted"}>
                {step.complete ? "Done" : step.required ? "Required" : "Recommended"}
              </StatusPill>
            </li>
          ))}
      </ul>

      {status === "onboarding" ? (
        state.can_submit ? (
          <Notice tone="info">
            Everything required is in place. Submitting sends your workspace to CXOps for
            review. Business details are locked until the review is finished.
          </Notice>
        ) : (
          <Notice tone="warn">Finish these steps first: {state.missing_required.join(", ")}.</Notice>
        )
      ) : null}
      {status === "pending_review" ? (
        <Notice tone="info">
          Submitted {formatDate(state.organization.submitted_for_review_at)}. We will let you know
          when your workspace is approved.
        </Notice>
      ) : null}
      {status === "active" ? (
        <Notice tone="ok">
          Approved. Switch the chatbot on in{" "}
          <Link href="/settings/public-chat" className="font-medium underline underline-offset-4">
            chatbot settings
          </Link>{" "}
          when you are ready to go live.
        </Notice>
      ) : null}
      {error ? <Notice tone="bad">{error}</Notice> : null}
      {!readOnly && status === "onboarding" ? (
        <button
          type="button"
          className={buttonPrimary}
          disabled={!state.can_submit || busy}
          onClick={() =>
            void run(async () => {
              await commercialApi.submitForReview();
              await onSaved("review");
              refresh();
            })
          }
        >
          {busy ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
          Submit for approval
        </button>
      ) : null}
    </div>
  );
}
