// Website installation helpers (Phase 1Q).
//
// The embed is the existing loader (public/embed/loader.js), which frames
// /chat/embed from the same origin that served the script. The widget key is
// public by design, but it is shown to staff only once (creation/rotation), so
// a snippet built later carries a placeholder the owner replaces.

export const WIDGET_KEY_PLACEHOLDER = "YOUR_WIDGET_KEY";

const KEY_PATTERN = /^pk_[a-z]+_[A-Za-z0-9]{16,}$/;

export function isWidgetKey(value: string): boolean {
  return KEY_PATTERN.test(value);
}

/** The one-line snippet a business pastes before </body>. */
export function buildInstallSnippet(appOrigin: string, widgetKey: string | null): string {
  const origin = appOrigin.replace(/\/+$/, "");
  const key = widgetKey && isWidgetKey(widgetKey) ? widgetKey : WIDGET_KEY_PLACEHOLDER;
  return `<script src="${origin}/embed/loader.js" data-widget-key="${key}" async></script>`;
}

/**
 * Exact-match origin check used by the setup wizard before the backend sees
 * the value: https only (http is allowed for local test sites on loopback),
 * no path, no wildcard, no credentials, no trailing slash.
 */
export function validateAllowedOrigin(value: string): string | null {
  const candidate = value.trim();
  if (!candidate) {
    return "Enter a website address.";
  }
  if (candidate.includes("*")) {
    return "Wildcards are not supported. List each domain separately.";
  }
  if (!candidate.includes("://")) {
    return "Include the scheme, for example https://www.yourbusiness.com.";
  }
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return "Enter a valid website address.";
  }
  if (url.username || url.password) {
    return "Remove the user name or password from the address.";
  }
  if (candidate.endsWith("/")) {
    return "Remove the trailing slash.";
  }
  if ((url.pathname && url.pathname !== "/") || url.search || url.hash) {
    return "Use only the domain, without a page path.";
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    return "Use https:// (http is only allowed for a local test site).";
  }
  return null;
}

/** Suggest the apex and www origins for a business website. */
export function suggestOrigins(website: string | null | undefined): string[] {
  if (!website) {
    return [];
  }
  let url: URL;
  try {
    url = new URL(website.includes("://") ? website : `https://${website}`);
  } catch {
    return [];
  }
  const host = url.hostname.toLowerCase();
  const apex = host.startsWith("www.") ? host.slice(4) : host;
  if (!apex.includes(".")) {
    return [];
  }
  return [`https://${apex}`, `https://www.${apex}`];
}
