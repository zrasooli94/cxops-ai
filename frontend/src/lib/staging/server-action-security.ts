/** Explicit defense in depth around staging Server Actions. Next.js also
 * validates Server Action origins, but these sensitive actions fail closed
 * before reading credentials when the browser origin is absent or foreign.
 */
export function stagingServerActionOriginAllowed(
  origin: string | null,
  host: string | null,
): boolean {
  if (!origin || !host || host.length > 255) return false;
  try {
    const parsed = new URL(origin);
    return parsed.protocol === "https:" && parsed.host === host &&
      parsed.username === "" && parsed.password === "" &&
      parsed.pathname === "/" && parsed.search === "" && parsed.hash === "";
  } catch {
    return false;
  }
}
