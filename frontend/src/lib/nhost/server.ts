"use server";

import { cookies } from "next/headers";
import { createServerClient, type NhostClient } from "@nhost/nhost-js";
import { type SessionStorageBackend, type StoredSession } from "@nhost/nhost-js/session";
import { NhostConfigurationError, structuredAuthLog } from "@/lib/auth/diagnostics";

const COOKIE_NAME = process.env.NHOST_SESSION_COOKIE ?? "nhostSession";
const SUBDOMAIN = process.env.NEXT_PUBLIC_NHOST_SUBDOMAIN ?? "";
const REGION = process.env.NEXT_PUBLIC_NHOST_REGION ?? "";
const IS_PRODUCTION = process.env.NODE_ENV === "production";

async function getCookieStore() {
  return await cookies();
}

function createCookieStorage(cookieStore: Awaited<ReturnType<typeof cookies>>): SessionStorageBackend {
  return {
    get(): StoredSession | null {
      const raw = cookieStore.get(COOKIE_NAME)?.value ?? null;
      if (!raw) {
        return null;
      }
      try {
        return JSON.parse(raw) as StoredSession;
      } catch {
        return null;
      }
    },
    set(value: StoredSession): void {
      cookieStore.set({
        name: COOKIE_NAME,
        value: JSON.stringify(value),
        path: "/",
        httpOnly: true,
        secure: IS_PRODUCTION,
        sameSite: "lax",
        maxAge: 60 * 60 * 24 * 30,
      });
    },
    remove(): void {
      cookieStore.delete(COOKIE_NAME);
    },
  };
}

export async function createNhostServerClient(): Promise<NhostClient> {
  if (!SUBDOMAIN || !REGION) {
    structuredAuthLog("auth_nhost_config_missing", {
      subdomainConfigured: SUBDOMAIN !== "",
      regionConfigured: REGION !== "",
    });
    throw new NhostConfigurationError();
  }
  const cookieStore = await getCookieStore();
  return createServerClient({
    subdomain: SUBDOMAIN,
    region: REGION,
    storage: createCookieStorage(cookieStore),
  });
}