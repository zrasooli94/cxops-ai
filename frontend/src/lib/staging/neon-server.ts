import "server-only";
import { createNeonAuth } from "@neondatabase/auth/next/server";
import { neonStagingEnabled, STAGING_AUTH_URL } from "./neon-policy";

/** Lazy: a production build never constructs the staging identity client. */
export function stagingAuth() {
  if (!neonStagingEnabled(process.env)) throw new Error("Isolated staging is not enabled");
  return createNeonAuth({
    baseUrl: STAGING_AUTH_URL,
    cookies: { secret: process.env.NEON_AUTH_COOKIE_SECRET! },
    logLevel: "silent",
  });
}
