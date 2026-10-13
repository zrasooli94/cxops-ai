"use client";

import Link from "next/link";
import { CheckCircle2, LoaderCircle, MailOpen } from "lucide-react";
import { useEffect, useState } from "react";

import { CommercialApiError, commercialApi } from "@/lib/commercial/client";
import {
  PENDING_INVITATION_STORAGE_KEY,
  parseInvitationHash,
  roleLabel,
} from "@/lib/commercial/team";

type View =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "signin" }
  | { kind: "invalid"; message: string }
  | { kind: "ready"; token: string; organization: string; role: string; email: string; expires: string }
  | { kind: "accepting" }
  | { kind: "accepted"; organization: string; role: string }
  | { kind: "error"; message: string }
  // The invitation is still valid; the signed-in account is the problem
  // (wrong address, unverified email) or acceptance is not available yet.
  | { kind: "retry"; token: string; message: string; unavailable: boolean };

function readStoredToken(): string | null {
  try {
    return window.sessionStorage.getItem(PENDING_INVITATION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeToken(token: string | null) {
  try {
    if (token) {
      window.sessionStorage.setItem(PENDING_INVITATION_STORAGE_KEY, token);
    } else {
      window.sessionStorage.removeItem(PENDING_INVITATION_STORAGE_KEY);
    }
  } catch {
    // Storage blocked: the visitor can reopen the link after signing in.
  }
}

export function InviteClient() {
  const [view, setView] = useState<View>({ kind: "loading" });

  useEffect(() => {
    const fromHash = parseInvitationHash(window.location.hash);
    if (fromHash) {
      // Keep the token for the sign-in round trip, then drop it from the
      // address bar so it does not linger in history or screenshots.
      storeToken(fromHash);
      window.history.replaceState(null, "", window.location.pathname);
    }
    const token = fromHash ?? readStoredToken();
    if (!token) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the token lives in the URL fragment / session storage, which exist only in the browser
      setView({ kind: "missing" });
      return;
    }
    commercialApi
      .previewInvitation(token)
      .then((preview) =>
        setView({
          kind: "ready",
          token,
          organization: preview.organization_name,
          role: preview.role,
          email: preview.email,
          expires: preview.expires_at,
        }),
      )
      .catch((exc) => {
        if (exc instanceof CommercialApiError && exc.status === 401) {
          setView({ kind: "signin" });
          return;
        }
        storeToken(null);
        setView({
          kind: "invalid",
          message:
            exc instanceof CommercialApiError
              ? exc.message
              : "We could not check this invitation. Please try again.",
        });
      });
  }, []);

  const accept = async (token: string) => {
    setView({ kind: "accepting" });
    try {
      const result = await commercialApi.acceptInvitation(token);
      storeToken(null);
      setView({ kind: "accepted", organization: result.organization_name, role: result.role });
    } catch (exc) {
      if (exc instanceof CommercialApiError && (exc.status === 403 || exc.status === 503)) {
        setView({ kind: "retry", token, message: exc.message, unavailable: exc.status === 503 });
        return;
      }
      if (exc instanceof CommercialApiError && exc.status === 404) {
        storeToken(null);
      }
      setView({
        kind: "error",
        message: exc instanceof CommercialApiError ? exc.message : "Something went wrong. Please try again.",
      });
    }
  };

  return (
    <div className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-[0_20px_60px_rgba(15,23,42,0.08)]">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50 text-violet-600">
        <MailOpen className="h-6 w-6" strokeWidth={1.7} aria-hidden="true" />
      </div>

      {view.kind === "loading" || view.kind === "accepting" ? (
        <p role="status" className="mt-6 flex items-center justify-center gap-2 text-sm text-slate-500">
          <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
          {view.kind === "loading" ? "Checking your invitation…" : "Joining the workspace…"}
        </p>
      ) : null}

      {view.kind === "missing" ? (
        <>
          <h1 className="mt-6 text-xl font-semibold text-slate-950">No invitation found</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            Open the invitation link you received exactly as it was sent. Links work once and
            expire after a few days.
          </p>
        </>
      ) : null}

      {view.kind === "signin" ? (
        <>
          <h1 className="mt-6 text-xl font-semibold text-slate-950">Sign in to accept</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            Sign in with the account for the email address that was invited. You will come back
            to this invitation afterwards.
          </p>
          <Link href="/login" className="mt-6 inline-flex rounded-xl bg-slate-950 px-5 py-3 text-sm font-medium text-white">
            Sign in
          </Link>
          <p className="mt-4 text-xs text-slate-500">
            No account yet? Your CXOps contact will set one up for the invited address.
          </p>
        </>
      ) : null}

      {view.kind === "ready" ? (
        <>
          <h1 className="mt-6 text-xl font-semibold text-slate-950">Join {view.organization}</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            You have been invited as <strong>{roleLabel(view.role).toLowerCase()}</strong> ({view.email}).
            The invitation expires {new Date(view.expires).toLocaleString()}.
          </p>
          <button
            type="button"
            onClick={() => void accept(view.token)}
            className="mt-6 inline-flex rounded-xl bg-slate-950 px-5 py-3 text-sm font-medium text-white hover:bg-slate-800"
          >
            Accept invitation
          </button>
        </>
      ) : null}

      {view.kind === "accepted" ? (
        <>
          <CheckCircle2 className="mx-auto mt-6 h-8 w-8 text-emerald-600" aria-hidden="true" />
          <h1 className="mt-3 text-xl font-semibold text-slate-950">Welcome to {view.organization}</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            You joined as {roleLabel(view.role).toLowerCase()}.
          </p>
          <a href="/api/auth/landing" className="mt-6 inline-flex rounded-xl bg-slate-950 px-5 py-3 text-sm font-medium text-white">
            Open the workspace
          </a>
        </>
      ) : null}

      {view.kind === "retry" ? (
        <>
          <h1 className="mt-6 text-xl font-semibold text-slate-950">
            {view.unavailable ? "Not available yet" : "Check the account you are signed in with"}
          </h1>
          <p role="alert" className="mt-3 text-sm leading-6 text-slate-600">
            {view.message}
          </p>
          <p className="mt-3 text-xs leading-5 text-slate-500">
            {view.unavailable
              ? "Your invitation is still valid. Try again later."
              : "Your invitation is still valid. Sign in with the invited, verified email address and open the link again."}
          </p>
          <button
            type="button"
            onClick={() => void accept(view.token)}
            className="mt-5 inline-flex rounded-xl border border-slate-300 px-5 py-2.5 text-sm font-medium text-slate-800 hover:bg-slate-50"
          >
            Try again
          </button>
        </>
      ) : null}

      {view.kind === "invalid" || view.kind === "error" ? (
        <>
          <h1 className="mt-6 text-xl font-semibold text-slate-950">This invitation cannot be used</h1>
          <p role="alert" className="mt-3 text-sm leading-6 text-slate-600">
            {view.message}
          </p>
          <p className="mt-3 text-xs text-slate-500">Ask the person who invited you to send a new invitation.</p>
        </>
      ) : null}
    </div>
  );
}
