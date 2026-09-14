"use server";

import { createNhostServerClient } from "@/lib/nhost/server";
import { redirect } from "next/navigation";
import { FetchError } from "@nhost/nhost-js/fetch";

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
      return { ok: false, error: "Invalid email or password." };
    }

    if (!result.body?.session) {
      return { ok: false, error: "Invalid email or password." };
    }
  } catch (error) {
    return { ok: false, error: classifySignInError(error) };
  }

  redirect("/dashboard");
}