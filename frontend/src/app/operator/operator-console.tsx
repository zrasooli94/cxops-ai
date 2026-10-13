"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { LoaderCircle, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { BrandMark } from "@/components/marketing/site";
import {
  Card,
  CopyField,
  LoadingBlock,
  Meter,
  Notice,
  StatusPill,
  buttonPrimary,
  buttonSecondary,
  formatDate,
  inputClass,
  type Tone,
} from "@/components/workspace/ui";
import { CommercialApiError, requestJson } from "@/lib/commercial/client";
import { LEAD_INDUSTRIES } from "@/lib/commercial/leads";
import { formatUsd, lifecycleInfo, usageShare } from "@/lib/commercial/onboarding";
import { invitationLink } from "@/lib/commercial/team";

interface Lead {
  id: number;
  company_name: string;
  contact_name: string;
  email: string;
  website: string | null;
  industry: string;
  inquiry: string | null;
  status: string;
  allowed_transitions: string[];
  submission_count: number;
  operator_notes: string | null;
  provisioned_organization_id: number | null;
  created_at: string | null;
  consented_at: string;
}

interface Tenant {
  id: number;
  name: string;
  industry: string | null;
  website_url: string | null;
  lifecycle_status: string;
  plan_code: string | null;
  created_at: string | null;
  submitted_for_review_at: string | null;
  members: number;
  widget: null | { enabled: boolean; origins: number; allowance_override: boolean };
  usage: {
    turns: number;
    turn_limit: number;
    estimated_cost_usd: number;
    estimated_outstanding_usd: number;
    budget_usd: number;
  };
  allowed_actions: string[];
}

interface PlatformUsage {
  period_start: string;
  estimated_cost_usd: number;
  estimated_outstanding_usd: number;
  platform_ceiling_usd: number;
  turns: number;
  by_tenant: { organization_id: number; name: string | null; turns: number; estimated_cost_usd: number }[];
  cost_basis_note: string;
}

const LEAD_TONE: Record<string, Tone> = {
  new: "info",
  reviewing: "warn",
  contacted: "warn",
  approved: "ok",
  declined: "muted",
};

const ACTION_LABELS: Record<string, string> = {
  approve: "Approve",
  request_changes: "Request changes",
  suspend: "Suspend",
  reinstate: "Reinstate",
};

const industryLabel = (value: string) =>
  LEAD_INDUSTRIES.find((item) => item.value === value)?.label ?? value;

function errorText(exc: unknown): string {
  return exc instanceof CommercialApiError ? exc.message : "Something went wrong.";
}

type Tab = "leads" | "tenants" | "usage";

export function OperatorConsole() {
  const router = useRouter();
  const [access, setAccess] = useState<"checking" | "granted" | "denied" | "error">("checking");
  const [tab, setTab] = useState<Tab>("leads");

  useEffect(() => {
    requestJson<{ platform_operator: boolean }>("/platform-admin/me")
      .then(() => setAccess("granted"))
      .catch((exc) => {
        if (exc instanceof CommercialApiError && exc.status === 401) {
          router.push("/login");
          return;
        }
        setAccess(exc instanceof CommercialApiError && exc.status === 403 ? "denied" : "error");
      });
  }, [router]);

  return (
    <div className="min-h-screen bg-[#f7f8fc]">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-5">
          <span className="flex items-center gap-2.5">
            <BrandMark className="h-6 w-6" />
            <span className="text-sm font-semibold text-slate-950">CXOps operator console</span>
          </span>
          <Link href="/" className="text-xs text-slate-500 hover:text-slate-900">
            Public site
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-6 px-5 py-8">
        {access === "checking" ? <LoadingBlock label="Checking operator access…" /> : null}
        {access === "denied" ? (
          <Card>
            <div className="flex gap-3">
              <ShieldAlert className="h-5 w-5 shrink-0 text-slate-400" aria-hidden="true" />
              <div className="text-sm leading-6 text-slate-600">
                <p className="font-semibold text-slate-900">This area is for CXOps platform operators.</p>
                <p className="mt-1">
                  Your account does not have operator access. Business owners manage their
                  workspace from the{" "}
                  <Link href="/workspace" className="font-medium text-violet-700 underline underline-offset-4">
                    control center
                  </Link>
                  .
                </p>
              </div>
            </div>
          </Card>
        ) : null}
        {access === "error" ? <Notice tone="bad">The operator service is unavailable. Try again shortly.</Notice> : null}

        {access === "granted" ? (
          <>
            <div role="tablist" aria-label="Operator sections" className="flex gap-2">
              {(["leads", "tenants", "usage"] as Tab[]).map((name) => (
                <button
                  key={name}
                  role="tab"
                  aria-selected={tab === name}
                  onClick={() => setTab(name)}
                  className={`rounded-full px-4 py-1.5 text-sm font-medium ${
                    tab === name ? "bg-slate-950 text-white" : "bg-white text-slate-700 ring-1 ring-slate-200"
                  }`}
                >
                  {name === "leads" ? "Leads" : name === "tenants" ? "Tenants" : "AI cost"}
                </button>
              ))}
            </div>
            {tab === "leads" ? <LeadsPanel /> : null}
            {tab === "tenants" ? <TenantsPanel /> : null}
            {tab === "usage" ? <UsagePanel /> : null}
          </>
        ) : null}
      </main>
    </div>
  );
}

function LeadsPanel() {
  const [filter, setFilter] = useState("new");
  const [data, setData] = useState<{ leads: Lead[]; counts: Record<string, number> } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const query = filter === "all" ? "" : `?status=${filter}`;
      setData(await requestJson(`/platform-admin/leads${query}`));
      setError(null);
    } catch (exc) {
      setError(errorText(exc));
    }
  }, [filter]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount writes the result of an external request (same pattern as the knowledge page)
    void load();
  }, [load]);

  const lead = data?.leads.find((item) => item.id === selected) ?? null;

  return (
    <div className="space-y-5">
    <RecordLeadForm
      onCreated={(id) => {
        setFilter("reviewing");
        setSelected(id);
      }}
    />
    <div className="grid gap-5 lg:grid-cols-[1fr_1.1fr]">
      <Card title="Early-access requests">
        <div className="mb-4 flex flex-wrap gap-1.5">
          {["new", "reviewing", "contacted", "approved", "declined", "all"].map((state) => (
            <button
              key={state}
              onClick={() => {
                setFilter(state);
                setSelected(null);
              }}
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                filter === state ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"
              }`}
            >
              {state}
              {data && state !== "all" ? ` (${data.counts[state] ?? 0})` : ""}
            </button>
          ))}
        </div>
        {error ? <Notice tone="bad">{error}</Notice> : null}
        {!data ? (
          <LoadingBlock />
        ) : data.leads.length === 0 ? (
          <p className="text-sm text-slate-500">No requests in this state.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {data.leads.map((item) => (
              <li key={item.id}>
                <button
                  onClick={() => setSelected(item.id)}
                  className={`flex w-full items-center justify-between gap-3 py-2.5 text-left text-sm ${
                    selected === item.id ? "text-violet-700" : "text-slate-800"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{item.company_name}</span>
                    <span className="block truncate text-xs text-slate-500">
                      {industryLabel(item.industry)} · {formatDate(item.created_at)}
                    </span>
                  </span>
                  <StatusPill tone={LEAD_TONE[item.status] ?? "muted"}>{item.status}</StatusPill>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {lead ? (
        <LeadDetail
          key={lead.id}
          lead={lead}
          onChanged={load}
          onMoved={(status) => {
            // Follow the lead to its new state so the operator keeps context.
            setFilter(status);
          }}
        />
      ) : (
        <Card>
          <p className="text-sm text-slate-500">Select a request to review it.</p>
        </Card>
      )}
    </div>
    </div>
  );
}

function RecordLeadForm({ onCreated }: { onCreated: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    company_name: "",
    contact_name: "",
    email: "",
    website: "",
    industry: "other",
    inquiry: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof form, value: string) =>
    setForm((current) => ({ ...current, [key]: value }));

  if (!open) {
    return (
      <button className={buttonSecondary} onClick={() => setOpen(true)}>
        Record a request from a call or email
      </button>
    );
  }
  return (
    <Card title="Record a request">
      <p className="mb-4 text-xs leading-5 text-slate-500">
        For prospects who contacted CXOps directly. Only record people who agreed to be
        contacted about their request; the record is marked as operator-entered.
      </p>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError(null);
          try {
            const created = await requestJson<{ id: number }>("/platform-admin/leads", {
              method: "POST",
              body: {
                ...form,
                website: form.website.trim() || null,
                inquiry: form.inquiry.trim() || null,
              },
            });
            setOpen(false);
            onCreated(created.id);
          } catch (exc) {
            setError(errorText(exc));
          } finally {
            setBusy(false);
          }
        }}
      >
        {(
          [
            ["company_name", "Business name", "text"],
            ["contact_name", "Contact name", "text"],
            ["email", "Email", "email"],
            ["website", "Website (optional)", "text"],
          ] as const
        ).map(([key, label, type]) => (
          <label key={key} className="block text-xs font-medium text-slate-700">
            {label}
            <input
              className={inputClass}
              type={type}
              required={key !== "website"}
              value={form[key]}
              onChange={(e) => set(key, e.target.value)}
            />
          </label>
        ))}
        <label className="block text-xs font-medium text-slate-700">
          Type of business
          <select className={inputClass} value={form.industry} onChange={(e) => set("industry", e.target.value)}>
            {LEAD_INDUSTRIES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs font-medium text-slate-700 sm:col-span-2">
          Notes from the prospect (optional)
          <textarea className={inputClass} rows={2} maxLength={2000} value={form.inquiry} onChange={(e) => set("inquiry", e.target.value)} />
        </label>
        {error ? (
          <div className="sm:col-span-2">
            <Notice tone="bad">{error}</Notice>
          </div>
        ) : null}
        <div className="flex gap-2 sm:col-span-2">
          <button type="submit" className={buttonPrimary} disabled={busy}>
            Save request
          </button>
          <button type="button" className={buttonSecondary} onClick={() => setOpen(false)}>
            Cancel
          </button>
        </div>
      </form>
    </Card>
  );
}

function LeadDetail({
  lead,
  onChanged,
  onMoved,
}: {
  lead: Lead;
  onChanged: () => Promise<void>;
  onMoved: (status: string) => void;
}) {
  const [notes, setNotes] = useState(lead.operator_notes ?? "");
  const [orgName, setOrgName] = useState(lead.company_name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [provisioned, setProvisioned] = useState<{ link: string; email: string; orgId: number } | null>(null);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={lead.company_name} action={<StatusPill tone={LEAD_TONE[lead.status] ?? "muted"}>{lead.status}</StatusPill>}>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <Detail label="Contact">{lead.contact_name}</Detail>
        <Detail label="Email">{lead.email}</Detail>
        <Detail label="Website">{lead.website ?? "—"}</Detail>
        <Detail label="Industry">{industryLabel(lead.industry)}</Detail>
        <Detail label="Submissions">{lead.submission_count}</Detail>
        <Detail label="Consent recorded">{formatDate(lead.consented_at)}</Detail>
      </dl>
      {lead.inquiry ? (
        <p className="mt-4 whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-sm text-slate-700">{lead.inquiry}</p>
      ) : null}

      <label className="mt-5 block">
        <span className="text-sm font-medium text-slate-800">Operator notes</span>
        <textarea className={inputClass} rows={3} maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          className={buttonSecondary}
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await requestJson(`/platform-admin/leads/${lead.id}`, { method: "PATCH", body: { operator_notes: notes } });
              await onChanged();
            })
          }
        >
          Save notes
        </button>
        {lead.allowed_transitions.map((next) => (
          <button
            key={next}
            className={next === "approved" ? buttonPrimary : buttonSecondary}
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await requestJson(`/platform-admin/leads/${lead.id}`, { method: "PATCH", body: { status: next } });
                onMoved(next);
              })
            }
          >
            Mark {next}
          </button>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
        <button
          className={buttonSecondary}
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const data = await requestJson<unknown>(`/platform-admin/leads/${lead.id}/export`);
              const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
              const url = URL.createObjectURL(blob);
              const link = document.createElement("a");
              link.href = url;
              link.download = `lead-${lead.id}.json`;
              link.click();
              URL.revokeObjectURL(url);
            })
          }
        >
          Export data
        </button>
        <button
          className={`${buttonSecondary} text-rose-700`}
          disabled={busy}
          onClick={() => {
            if (
              window.confirm(
                "Permanently delete this request and its personal data? A workspace created from it is not affected. This cannot be undone.",
              )
            ) {
              void run(async () => {
                await requestJson(`/platform-admin/leads/${lead.id}`, { method: "DELETE" });
                await onChanged();
              });
            }
          }}
        >
          Delete data
        </button>
      </div>

      {lead.status === "approved" && lead.provisioned_organization_id === null && !provisioned ? (
        <div className="mt-6 space-y-3 rounded-xl border border-violet-200 bg-violet-50/50 p-4">
          <p className="text-sm font-medium text-slate-900">Provision a workspace</p>
          <p className="text-xs leading-5 text-slate-600">
            Creates the organization in <em>setting up</em> state and a one-time owner invitation for{" "}
            {lead.email}. Nothing goes live and no AI spending is possible until you approve the tenant.
          </p>
          <label className="block">
            <span className="text-xs font-medium text-slate-700">Workspace name</span>
            <input className={inputClass} value={orgName} maxLength={255} onChange={(e) => setOrgName(e.target.value)} />
          </label>
          <button
            className={buttonPrimary}
            disabled={busy || !orgName.trim()}
            onClick={() =>
              void run(async () => {
                const result = await requestJson<{
                  organization_id: number;
                  owner_invitation: { token: string; email: string };
                }>(`/platform-admin/leads/${lead.id}/provision`, {
                  method: "POST",
                  body: { organization_name: orgName.trim() },
                });
                setProvisioned({
                  link: invitationLink(window.location.origin, result.owner_invitation.token),
                  email: result.owner_invitation.email,
                  orgId: result.organization_id,
                });
                await onChanged();
              })
            }
          >
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            Create workspace and owner invitation
          </button>
        </div>
      ) : null}
      {provisioned ? (
        <div className="mt-6 space-y-3">
          <Notice tone="warn">
            Workspace #{provisioned.orgId} created. Email delivery is not configured - send this link
            to {provisioned.email} yourself. It is shown only once.
          </Notice>
          <CopyField label="Owner invitation link" value={provisioned.link} />
        </div>
      ) : null}
      {lead.provisioned_organization_id !== null && !provisioned ? (
        <p className="mt-5 text-sm text-slate-600">Provisioned as workspace #{lead.provisioned_organization_id}.</p>
      ) : null}
      {error ? <div className="mt-4"><Notice tone="bad">{error}</Notice></div> : null}
    </Card>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="truncate text-slate-900">{children}</dd>
    </div>
  );
}

function TenantsPanel() {
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await requestJson<{ organizations: Tenant[] }>("/platform-admin/organizations");
      setTenants(data.organizations);
      setError(null);
    } catch (exc) {
      setError(errorText(exc));
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount writes the result of an external request (same pattern as the knowledge page)
    void load();
  }, [load]);

  const act = async (id: number, action: string) => {
    if (action === "suspend" && !window.confirm("Suspend this tenant? Its chatbot stops answering immediately.")) {
      return;
    }
    setError(null);
    try {
      await requestJson(`/platform-admin/organizations/${id}/lifecycle`, { method: "POST", body: { action } });
      await load();
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  if (!tenants) {
    return error ? <Notice tone="bad">{error}</Notice> : <LoadingBlock />;
  }
  const order = ["pending_review", "onboarding", "active", "suspended"];
  const sorted = [...tenants].sort(
    (a, b) => order.indexOf(a.lifecycle_status) - order.indexOf(b.lifecycle_status) || a.id - b.id,
  );

  return (
    <div className="space-y-4">
      {error ? <Notice tone="bad">{error}</Notice> : null}
      {sorted.map((tenant) => {
        const lifecycle = lifecycleInfo(tenant.lifecycle_status);
        return (
          <Card key={tenant.id}>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 font-semibold text-slate-950">
                  #{tenant.id} {tenant.name}
                  <StatusPill tone={lifecycle.tone}>{lifecycle.label}</StatusPill>
                  {tenant.plan_code ? <StatusPill tone="muted">plan: {tenant.plan_code}</StatusPill> : null}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {tenant.members} members · {tenant.website_url ?? "no website"} ·{" "}
                  {tenant.widget
                    ? `widget ${tenant.widget.enabled ? "on" : "off"}, ${tenant.widget.origins} domains`
                    : "no widget"}
                  {tenant.submitted_for_review_at ? ` · submitted ${formatDate(tenant.submitted_for_review_at)}` : ""}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {tenant.allowed_actions.map((action) => (
                  <button
                    key={action}
                    className={action === "approve" ? buttonPrimary : buttonSecondary}
                    onClick={() => void act(tenant.id, action)}
                  >
                    {ACTION_LABELS[action] ?? action}
                  </button>
                ))}
                <button className={buttonSecondary} onClick={() => setEditing(editing === tenant.id ? null : tenant.id)}>
                  Entitlements
                </button>
              </div>
            </div>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Meter
                label="AI conversations (month)"
                value={`${tenant.usage.turns} / ${tenant.usage.turn_limit}`}
                share={usageShare(tenant.usage.turns, tenant.usage.turn_limit)}
              />
              <Meter
                label="Estimated AI cost (month)"
                value={`${formatUsd(tenant.usage.estimated_cost_usd)} / ${formatUsd(tenant.usage.budget_usd)}`}
                share={usageShare(tenant.usage.estimated_cost_usd, tenant.usage.budget_usd)}
              />
            </div>
            {editing === tenant.id ? <EntitlementsForm tenant={tenant} onSaved={load} /> : null}
          </Card>
        );
      })}
    </div>
  );
}

function EntitlementsForm({ tenant, onSaved }: { tenant: Tenant; onSaved: () => Promise<void> }) {
  const [plan, setPlan] = useState(tenant.plan_code ?? "");
  const [turns, setTurns] = useState(String(tenant.usage.turn_limit));
  const [budget, setBudget] = useState(String(tenant.usage.budget_usd));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { plan_code: plan.trim() || null };
      if (tenant.widget) {
        // An empty or invalid field would serialise as null, which the API
        // treats as "clear the override" - refuse it instead of guessing.
        const turnLimit = Number(turns);
        const budgetUsd = Number(budget);
        if (turns.trim() === "" || !Number.isInteger(turnLimit) || turnLimit < 0) {
          throw new CommercialApiError(400, "Enter a whole number of AI conversations (0 or more).");
        }
        if (budget.trim() === "" || !Number.isFinite(budgetUsd) || budgetUsd < 0) {
          throw new CommercialApiError(400, "Enter a budget in USD (0 or more).");
        }
        body.monthly_ai_turn_limit = turnLimit;
        body.monthly_ai_budget_usd = budgetUsd;
      }
      await requestJson(`/platform-admin/organizations/${tenant.id}/entitlements`, { method: "PATCH", body });
      await onSaved();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-5 space-y-3 rounded-xl bg-slate-50 p-4">
      <p className="text-xs leading-5 text-slate-600">
        Internal entitlement metadata only - no prices, no charges. Allowances are estimates and
        must stay within the provider-side hard limit.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block text-xs font-medium text-slate-700">
          Plan code
          <input className={inputClass} value={plan} maxLength={40} onChange={(e) => setPlan(e.target.value)} placeholder="pilot" />
        </label>
        <label className="block text-xs font-medium text-slate-700">
          Monthly AI conversations
          <input className={inputClass} type="number" min={0} max={1000000} value={turns} disabled={!tenant.widget} onChange={(e) => setTurns(e.target.value)} />
        </label>
        <label className="block text-xs font-medium text-slate-700">
          Monthly AI budget (USD, est.)
          <input className={inputClass} type="number" min={0} max={10000} step="0.01" value={budget} disabled={!tenant.widget} onChange={(e) => setBudget(e.target.value)} />
        </label>
      </div>
      {!tenant.widget ? <p className="text-xs text-slate-500">Allowances can be set once the tenant has a chatbot.</p> : null}
      {error ? <Notice tone="bad">{error}</Notice> : null}
      <button className={buttonPrimary} onClick={() => void save()} disabled={busy}>
        Save entitlements
      </button>
    </div>
  );
}

function UsagePanel() {
  const [usage, setUsage] = useState<PlatformUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    requestJson<PlatformUsage>("/platform-admin/usage")
      .then(setUsage)
      .catch((exc) => setError(errorText(exc)));
  }, []);
  if (error) {
    return <Notice tone="bad">{error}</Notice>;
  }
  if (!usage) {
    return <LoadingBlock />;
  }
  const used = usage.estimated_cost_usd + usage.estimated_outstanding_usd;
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_1.3fr]">
      <Card title="Platform AI cost this month">
        <div className="space-y-4">
          <Meter
            label="Estimated spend vs. platform ceiling"
            value={`${formatUsd(used)} / ${formatUsd(usage.platform_ceiling_usd)}`}
            share={usageShare(used, usage.platform_ceiling_usd)}
            tone={usageShare(used, usage.platform_ceiling_usd) > 80 ? "warn" : "ok"}
          />
          <p className="text-sm text-slate-600">
            {usage.turns.toLocaleString()} AI conversations since {new Date(usage.period_start).toLocaleDateString()}.
          </p>
          <Notice tone="info">{usage.cost_basis_note}</Notice>
        </div>
      </Card>
      <Card title="By tenant">
        {usage.by_tenant.length === 0 ? (
          <p className="text-sm text-slate-500">No AI usage recorded this month.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500">
                <th className="pb-2 font-medium">Tenant</th>
                <th className="pb-2 text-right font-medium">Conversations</th>
                <th className="pb-2 text-right font-medium">Est. cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {usage.by_tenant.map((row) => (
                <tr key={row.organization_id}>
                  <td className="py-2 text-slate-800">
                    #{row.organization_id} {row.name}
                  </td>
                  <td className="py-2 text-right text-slate-700">{row.turns}</td>
                  <td className="py-2 text-right text-slate-700">{formatUsd(row.estimated_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
