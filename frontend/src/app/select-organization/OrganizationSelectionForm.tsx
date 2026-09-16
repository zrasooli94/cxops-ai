"use client";

import { useActionState } from "react";
import { Building2 } from "lucide-react";
import { selectOrganization } from "@/lib/tenant/actions";

interface OrganizationSelectionFormProps {
  memberships: { id: number; name: string; industry: string | null }[];
}

interface FormState {
  ok: boolean;
  error?: string;
}

export default function OrganizationSelectionForm({
  memberships,
}: OrganizationSelectionFormProps) {
  async function submitAction(
    _prevState: FormState,
    formData: FormData,
  ): Promise<FormState> {
    const raw = formData.get("organizationId");
    const organizationId = raw ? Number(raw) : NaN;
    if (!Number.isInteger(organizationId) || organizationId <= 0) {
      return { ok: false, error: "Please select a valid organization." };
    }
    return selectOrganization(organizationId);
  }

  const [state, formAction, isPending] = useActionState<FormState, FormData>(
    submitAction,
    { ok: true },
  );

  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-8 shadow-[0_25px_60px_rgba(79,90,130,0.08)]">
      <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50 text-violet-600">
        <Building2 className="h-6 w-6" strokeWidth={1.7} />
      </div>

      <h1 className="text-2xl font-light tracking-[-0.03em] text-slate-950">
        Select organization
      </h1>
      <p className="mt-2 text-sm text-slate-500">
        Choose the organization workspace you want to open.
      </p>

      {state.error && (
        <div className="mt-5 rounded-xl border border-rose-200 bg-rose-50/80 p-4 text-sm text-rose-700">
          {state.error}
        </div>
      )}

      <form action={formAction} className="mt-6 space-y-3">
        <fieldset className="space-y-2" role="radiogroup" aria-label="Organizations">
          <legend className="sr-only">Choose an organization</legend>
          {memberships.map((org) => (
            <label
              key={org.id}
              className="group flex cursor-pointer items-center gap-3 rounded-xl border border-slate-200 bg-slate-50/50 p-4 transition hover:border-violet-300 hover:bg-white"
            >
              <input
                type="radio"
                name="organizationId"
                value={org.id}
                required
                className="h-4 w-4 accent-violet-600"
              />
              <div className="min-w-0 flex-1">
                <p className="font-medium text-slate-900">{org.name}</p>
                {org.industry && (
                  <p className="text-xs text-slate-500">{org.industry}</p>
                )}
              </div>
            </label>
          ))}
        </fieldset>

        <button
          type="submit"
          disabled={isPending}
          className="w-full rounded-full bg-[#111827] px-5 py-3.5 text-sm font-medium text-white shadow-[0_12px_30px_rgba(17,24,39,0.15)] transition hover:-translate-y-0.5 hover:bg-gradient-to-r hover:from-[#765cff] hover:to-[#508cff] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isPending ? "Continuing..." : "Continue"}
        </button>
      </form>
    </div>
  );
}
