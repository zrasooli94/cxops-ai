import type { NextConfig } from "next";

/**
 * Origins permitted to embed the public chat widget in an iframe.
 *
 * The widget is framed by the tenant's own site, so `frame-ancestors` has to
 * allow them -- but it must be an explicit allowlist. Without the header, any
 * site can frame our widget, and because the widget carries a tenant's public
 * key, a hostile parent could overlay it to phish a visitor of that tenant.
 */
const WIDGET_FRAME_ANCESTORS = [
  "'self'",
  "https://a1cashforcars.com.au",
  "https://www.a1cashforcars.com.au",
];

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
  output: process.env.VERCEL ? undefined : "standalone",

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
