import type { NextConfig } from "next";

/**
 * Origins permitted to embed the public chat widget in an iframe.
 *
 * The widget is framed by the tenant's own site, so `frame-ancestors` has to
 * allow them -- but it must be an explicit allowlist. Without the header, any
 * site can frame our widget, and because the widget carries a tenant's public
 * key, a hostile parent could overlay it to phish a visitor of that tenant.
 *
 * These are the tenants' *custom* domains, which is what matters: A1 and RISPU
 * are separate Replit deployments reached through their own domains, so nothing
 * here needs to know about Replit at all. A `*.replit.app` wildcard is
 * deliberately absent -- every Replit deployment gets a sibling subdomain of a
 * shared parent, so such a wildcard would let any Replit user on earth frame a
 * tenant's widget and read its key out of the URL. Add a host here only when a
 * tenant's real domain is known.
 */
const WIDGET_FRAME_ANCESTORS = [
  "'self'",
  "https://a1cashforcars.com.au",
  "https://www.a1cashforcars.com.au",
];

/**
 * Internal API location, read by the server-side BFF and Control Center
 * modules. It is a runtime value on purpose: Next.js server code reads
 * `process.env` at request time, so the same built image can be pointed at a
 * different API without a rebuild. It never leaves the machine on the Replit
 * topology (see scripts/start_replit_web.sh), which is why it is not required
 * to be https.
 */
const BACKEND_API_URL =
  process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

/**
 * Backend probes re-exported on the single published port.
 *
 * The frontend and the API share one host, and only one port is published, so
 * the API is bound to loopback. An operator (and the readiness script) still
 * needs to reach /health, /ready, and /version through the public origin. These
 * rewrites proxy them to the same API the BFF uses, so there is exactly one
 * reachable entry point and no reason to publish a second.
 *
 * `destination` is a full URL, so Next proxies server-side and the browser never
 * learns the loopback address.
 */
const BACKEND_PROBES = ["/health", "/ready", "/version"] as const;

function rewrites() {
  return BACKEND_PROBES.map((path) => ({
    source: path,
    destination: `${BACKEND_API_URL}${path}`,
  }));
}

function contentSecurityPolicy(frameAncestors: string): string {
  return [
    "default-src 'self'",
    // Next.js injects inline bootstrap scripts for hydration; a nonce would be
    // stricter but requires per-request rendering of the document, which this
    // static headers() API cannot provide.
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    // The widget only talks to its own origin; everything it needs is
    // same-origin or a relative URL.
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors " + frameAncestors,
  ].join("; ");
}

const nextConfig: NextConfig = {
  // "standalone" is what makes a container deployable at all: it emits a
  // self-contained server with its own node_modules subtree, so the runtime
  // needs no `next` install and no devDependencies. Every deployment target in
  // this repository runs the app as a server, so it is the default rather than
  // something a vendor name switches on.
  //
  // The one exception is a platform that injects its own routing in front of a
  // `next start` process, which cannot use a standalone server. Set
  // NEXT_STANDALONE_OUTPUT=off to fall back to the default output. The name
  // describes the capability being turned off, not a hosting provider, so the
  // config stays vendor-neutral.
  output: process.env.NEXT_STANDALONE_OUTPUT === "off" ? undefined : "standalone",

  async rewrites() {
    return rewrites();
  },

  async headers() {
    return [
      {
        // Baseline for every response. Applied first so the more specific
        // /chat/embed rule below can override frame-ancestors.
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          {
            key: "Referrer-Policy",
            // The widget key travels in the query string, so a full referrer
            // would hand it to every outbound link on a framed page.
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), payment=()",
          },
          {
            key: "Content-Security-Policy",
            value: contentSecurityPolicy("'self'"),
          },
        ],
      },
      {
        // Staff surfaces carry session cookies and tenant data, so they must
        // never be framed. `frame-ancestors 'none'` is honoured where
        // X-Frame-Options is not.
        source: "/:path((?!chat/embed).*)",
        headers: [
            { key: "X-Frame-Options", value: "DENY" },
            {
            key: "Content-Security-Policy",
            value: contentSecurityPolicy("'none'"),
          },
        ],
      },
      {
        // The widget is the one route meant to be framed, by the tenant's site.
        source: "/chat/embed",
        headers: [
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          {
            key: "Content-Security-Policy",
            value: contentSecurityPolicy(WIDGET_FRAME_ANCESTORS.join(" ")),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
