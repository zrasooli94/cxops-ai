"use server";

import { redirect } from "next/navigation";
import { stagingAuth } from "@/lib/staging/neon-server";
import { neonStagingEnabled } from "@/lib/staging/neon-policy";

export async function stagingSignIn(_previous: string | null, form: FormData): Promise<string | null> {
  if (!neonStagingEnabled(process.env)) return "Staging sign-in is awaiting isolated backend verification.";
  const email = String(form.get("email") ?? "").trim().toLowerCase();
  const password = String(form.get("password") ?? "");
  if (!email || !password || email.length > 254 || password.length > 1024) return "Enter your staging email and password.";
  try {
    const result = await stagingAuth().signIn.email({ email, password });
    if (result.error) return "Unable to sign in. Check your staging credentials.";
  } catch {
    return "Staging sign-in is temporarily unavailable. Please try again.";
  }
  redirect("/staging");
}

export async function stagingSignOut(): Promise<void> {
  if (!neonStagingEnabled(process.env)) redirect("/staging");
  const result = await stagingAuth().signOut();
  if (result.error) throw new Error("Staging sign-out is temporarily unavailable");
  redirect("/staging/login");
}

/** Read-only synthetic viewer only; credentials never leave the server. */
export async function stagingDemoSignIn(_previous: string | null, _form: FormData): Promise<string | null> {
  if (!neonStagingEnabled(process.env)) return "Staging is not enabled.";
  const email = process.env.CXOPS_STAGING_DEMO_EMAIL;
  const password = process.env.CXOPS_STAGING_DEMO_PASSWORD;
  if (email !== "phase1w-alpha@example.test" || !password) return "Synthetic access is not configured.";
  const form = new FormData();
  form.set("email", email);
  form.set("password", password);
  return stagingSignIn(null, form);
}
