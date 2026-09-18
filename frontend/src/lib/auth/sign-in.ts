"use server";

import { createNhostServerClient } from "@/lib/nhost/server";
import { redirect } from "next/navigation";
import { FetchError } from "@nhost/nhost-js/fetch";
import { NhostConfigurationError, structuredAuthLog } from "@/lib/auth/diagnostics";
import { POST_SIGN_IN_DESTINATION } from "@/lib/session/read";

export interface SignInResult {
  ok: boolean;
  error?: string;
}

function classifySignInError(error: unknown): string {
  if (error instanceof FetchError) {
    const status = error.status;
    if (status === 400 || status === 401 || status === 403) {
      return "Invalid email or password.";
    }
    if (status >= 500) {
      return "Sign-in service is temporarily unavailable.";
    }
    return "Sign-in service is temporarily unavailable.";
  }
  return "Sign-in service is temporarily unavailable.";
}

function logSignInDiagnostic(error: unknown): void {
  if (error instanceof FetchError) {
    structuredAuthLog(
      error.status === 400 || error.status === 401 || error.status === 403
        ? "auth_invalid_credentials"
        : "auth_nhost_service_failure",
      { status: error.status },
    );
    return;
  }
  if (error instanceof NhostConfigurationError) {
    return;
  }
  structuredAuthLog("auth_nhost_service_failure", {
    name: error instanceof Error ? error.name : "unknown",
  });
}

export async function signIn(formData: FormData): Promise<{ ok: boolean; error?: string }> {
  const email = formData.get("email")?.toString().trim().toLowerCase() ?? "";
  const password = formData.get("password")?.toString() ?? "";

  if (!email || !password) {
    return { ok: false, error: "Email and password are required." };
  }

  if (!email.includes("@")) {
    return { ok: false, error: "Invalid email or password." };
  }

  try {
    const nhost = await createNhostServerClient();
    const result = await nhost.auth.signInEmailPassword({
      email,
      password,
    });

    if (result.status >= 400 || result.body?.mfa) {
      structuredAuthLog("auth_invalid_credentials", { status: result.status });
      return { ok: false, error: "Invalid email or password." };
    }

    if (!result.body?.session) {
      structuredAuthLog("auth_invalid_credentials", { status: result.status });
      return { ok: false, error: "Invalid email or password." };
    }
  } catch (error) {
    logSignInDiagnostic(error);
    return { ok: false, error: classifySignInError(error) };
  }

  // Successful sign-in enters the canonical post-auth bootstrap: the landing
  // Route Handler resolves memberships, initializes the active-organization
  // cookie, and routes to /dashboard or /select-organization or
  // /no-organization. Jumping straight to /dashboard would render before an org
  // cookie exists on a first login.
  redirect(POST_SIGN_IN_DESTINATION);
}
