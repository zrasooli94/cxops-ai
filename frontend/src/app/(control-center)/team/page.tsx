"use client";

import { LoaderCircle, MailPlus, UserMinus } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

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
import {
  CommercialApiError,
  commercialApi,
  type TeamInvitation,
  type TeamMember,
} from "@/lib/commercial/client";
import {
  INVITATION_STATUS_LABELS,
  ROLE_DESCRIPTIONS,
  invitationLink,
  roleLabel,
} from "@/lib/commercial/team";

interface TeamState {
  members: TeamMember[];
  canManage: boolean;
  assignable: string[];
  yourRole: string;
  invitations: TeamInvitation[];
}

function errorText(exc: unknown): string {
  return exc instanceof CommercialApiError ? exc.message : "Something went wrong. Please try again.";
}

const STATUS_TONE = {
  pending: "info",
  expired: "muted",
  accepted: "ok",
  revoked: "muted",
} as const;

export default function TeamPage() {
  const { refresh } = useAuthorization();
  const [team, setTeam] = useState<TeamState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [issued, setIssued] = useState<{ email: string; link: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const members = await commercialApi.members();
      const invitations = members.can_manage ? (await commercialApi.invitations()).invitations : [];
      setTeam({
        members: members.members,
        canManage: members.can_manage,
        assignable: members.assignable_roles,
        yourRole: members.your_role,
        invitations,
      });
      setError(null);
    } catch (exc) {
      setError(errorText(exc));
    }
  }, []);

  useEffect(() => {
    // Organization switches remount this page (keyed provider): a one-time
    // invitation link from another workspace can never be shown here.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount writes the result of an external request (same pattern as the knowledge page)
    void load();
  }, [load]);

  const act = async (action: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await action();
      await load();
    } catch (exc) {
      setActionError(errorText(exc));
    }
  };

  if (error && !team) {
    return (
      <main className="px-5 py-8 sm:px-8">
        <Notice tone="bad">{error}</Notice>
      </main>
    );
  }
  if (!team) {
    return (
      <main className="px-5 py-8 sm:px-8">
        <LoadingBlock label="Loading your team…" />
      </main>
    );
  }

  return (
    <main className="space-y-6 px-5 py-8 sm:px-8 lg:py-10">
      <PageHeader
        eyebrow="Team"
        title="People and access"
        description={`You are ${roleLabel(team.yourRole).toLowerCase()} in this workspace. Each person sees only the businesses they belong to.`}
      />

      {actionError ? <Notice tone="bad">{actionError}</Notice> : null}

      <Card title={`Members (${team.members.length})`}>
        <ul className="divide-y divide-slate-100">
          {team.members.map((member) => (
            <li key={member.id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-900">
                  {member.email ?? "Member (no email on record)"}
                  {member.is_you ? <span className="ml-2 text-xs font-normal text-slate-500">(you)</span> : null}
                </p>
                <p className="text-xs text-slate-500">Joined {formatDate(member.joined_at)}</p>
              </div>
              <div className="flex items-center gap-2">
                {member.can_manage && team.canManage ? (
                  <>
                    <label className="sr-only" htmlFor={`role-${member.id}`}>
                      Role for {member.email ?? `member ${member.id}`}
                    </label>
                    <select
                      id={`role-${member.id}`}
                      className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm"
                      value={member.role}
                      onChange={(event) =>
                        void act(() => commercialApi.changeRole(member.id, event.target.value))
                      }
                    >
                      {Array.from(new Set([member.role, ...team.assignable])).map((role) => (
                        <option key={role} value={role} disabled={!team.assignable.includes(role)}>
                          {roleLabel(role)}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                      aria-label={`Remove ${member.email ?? `member ${member.id}`}`}
                      onClick={() => {
                        if (window.confirm("Remove this person from the workspace? They lose access immediately.")) {
                          void act(() => commercialApi.removeMember(member.id));
                        }
                      }}
                    >
                      <UserMinus className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </>
                ) : (
                  <StatusPill tone="muted">{roleLabel(member.role)}</StatusPill>
                )}
                {member.is_you && team.yourRole !== "owner" ? (
                  <button
                    type="button"
                    className="text-xs font-medium text-slate-500 underline-offset-4 hover:text-rose-600 hover:underline"
                    onClick={() => {
                      if (window.confirm("Leave this workspace? You will need a new invitation to return.")) {
                        void act(async () => {
                          await commercialApi.removeMember(member.id);
                          // Full navigation: the landing Route Handler rewrites the
                          // organization cookie, which a client transition cannot.
                          // eslint-disable-next-line @next/next/no-location-assign-relative-destination
                          window.location.href = "/api/auth/landing";
                        });
                      }
                    }}
                  >
                    Leave
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      </Card>

      {team.canManage ? (
        <div className="grid gap-5 lg:grid-cols-[1fr_1.2fr]">
          <InviteForm
            assignable={team.assignable}
            onIssued={async (email, token) => {
              setIssued({ email, link: invitationLink(window.location.origin, token) });
              await load();
              refresh();
            }}
          />
          <Card title="Invitations">
            {issued ? (
              <div className="mb-5 space-y-3">
                <Notice tone="warn">
                  Invitation created for {issued.email}. Email delivery is not set up yet - send this
                  link yourself. It is shown only once and expires in 72 hours.
                </Notice>
                <CopyField label="Invitation link" value={issued.link} />
              </div>
            ) : null}
            {team.invitations.length === 0 ? (
              <p className="text-sm text-slate-500">No invitations yet.</p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {team.invitations.map((invitation) => (
                  <li key={invitation.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                    <div className="min-w-0">
                      <p className="truncate font-medium text-slate-900">{invitation.email}</p>
                      <p className="text-xs text-slate-500">
                        {roleLabel(invitation.role)} · expires {formatDate(invitation.expires_at)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusPill tone={STATUS_TONE[invitation.status]}>
                        {INVITATION_STATUS_LABELS[invitation.status]}
                      </StatusPill>
                      {invitation.status === "pending" ? (
                        <button
                          type="button"
                          className="text-xs font-medium text-rose-600 hover:underline"
                          onClick={() => void act(() => commercialApi.revokeInvitation(invitation.id))}
                        >
                          Revoke
                        </button>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      ) : (
        <Notice tone="info">Only owners and administrators can invite people or change roles.</Notice>
      )}
    </main>
  );
}

function InviteForm({
  assignable,
  onIssued,
}: {
  assignable: string[];
  onIssued: (email: string, token: string) => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState(assignable.includes("agent") ? "agent" : assignable[assignable.length - 1] ?? "viewer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await commercialApi.invite(email.trim(), role);
      setEmail("");
      await onIssued(created.email, created.token);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Invite someone" icon={<MailPlus className="h-4 w-4 text-violet-600" aria-hidden="true" />}>
      <form onSubmit={submit} className="space-y-4">
        <label className="block">
          <span className="text-sm font-medium text-slate-800">Email address</span>
          <input type="email" required maxLength={320} className={inputClass} value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <fieldset>
          <legend className="text-sm font-medium text-slate-800">Role</legend>
          <div className="mt-2 space-y-2">
            {assignable.map((option) => (
              <label key={option} className={`flex cursor-pointer gap-3 rounded-lg border px-3 py-2 ${role === option ? "border-violet-400 bg-violet-50/50" : "border-slate-200"}`}>
                <input type="radio" name="invite-role" value={option} checked={role === option} onChange={() => setRole(option)} className="mt-1" />
                <span>
                  <span className="block text-sm font-medium text-slate-900">{roleLabel(option)}</span>
                  <span className="block text-xs text-slate-500">{ROLE_DESCRIPTIONS[option]}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {error ? <Notice tone="bad">{error}</Notice> : null}
        <div className="flex gap-2">
          <button type="submit" className={buttonPrimary} disabled={busy || !email.trim()}>
            {busy ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            Create invitation
          </button>
          <button type="reset" className={buttonSecondary} onClick={() => setEmail("")}>
            Clear
          </button>
        </div>
      </form>
    </Card>
  );
}
