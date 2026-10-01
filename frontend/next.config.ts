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
 * are separate production deployments reached through their own domains, so
 * nothing here needs to know where the app is hosted at all. A wildcard over any
 * hosting provider's shared domain is deliberately absent -- every tenant
 * deployment gets a sibling subdomain of a shared parent, so such a wildcard
 * would let an unrelated tenant frame this widget and read its key out of the
 * URL. Add a host here only when a tenant's real domain is known.
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
 * different API without a rebuild.
 *
 * In production this is the public https origin of the Render API service
 * (scripts/start_render_api.sh). It is still only ever read server-side, so the
 * browser never learns it: the BFF calls it and returns the response, which is
 * what keeps the widget and Control Center same-origin with this deployment and
 * removes the need for CORS on the public chat route.
 *
 * The loopback default is a local-development convenience, not a production
 * topology. Left as a fallback it cannot fail loudly on a misconfigured deploy,
 * so production must set BACKEND_API_URL explicitly.
 */
const BACKEND_API_URL =
  process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

/**
 * Backend probes re-exported on this deployment's own origin.
 *
 * The frontend and the API are separate services here, so this deployment
 * publishes only the Next.js port. An operator (and the readiness script) still
 * needs to reach /health, /ready, and /version through the frontend origin.
 * These rewrites proxy them to the API the BFF already uses, so there is exactly
 * one reachable entry point per service and no second port to publish.
 *
 * `destination` is a full URL, so Next proxies server-side and the browser never
 * learns the API's address.
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
