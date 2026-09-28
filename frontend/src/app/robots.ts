import type { MetadataRoute } from "next";

/**
 * Canonical origin for generated metadata.
 *
 * Read from the deployment environment rather than hardcoded, so a preview or
 * self-hosted deployment does not emit `Host:` and sitemap entries pointing at
 * someone else's domain.
 *
 * Resolved at BUILD time: this route is prerendered by `next build`, which
 * inlines process.env. Setting CXOPS_PUBLIC_SITE_URL on the running server has
 * no effect -- it has to be present during the build.
 */
function baseUrl(): string {
  const configured = process.env.CXOPS_PUBLIC_SITE_URL;
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  if (process.env.VERCEL_PROJECT_PRODUCTION_URL) {
    return `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`;
  }
  if (process.env.VERCEL_URL) {
    return `https://${process.env.VERCEL_URL}`;
  }
  return "http://localhost:" + (process.env.PORT ?? "3000");
}

export default function robots(): MetadataRoute.Robots {
  const origin = baseUrl();
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/login", "/chat/embed"],
    },
    sitemap: `${origin}/sitemap.xml`,
    host: origin,
  };
}
