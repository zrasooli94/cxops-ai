import type { MetadataRoute } from "next";

/**
 * Same origin resolution as robots.ts.
 *
 * Resolved at BUILD time: `next build` prerenders this route and inlines
 * process.env, so the value must be set on the build, not only at runtime.
 * See src/app/robots.ts for the full rationale.
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

export default function sitemap(): MetadataRoute.Sitemap {
  const origin = baseUrl();
  return [
    {
      url: `${origin}/`,
      changeFrequency: "weekly",
      priority: 1,
    },
    {
      url: `${origin}/platform`,
      changeFrequency: "weekly",
      priority: 0.95,
    },
    {
      url: `${origin}/platform/evaluating-ai-support-platforms`,
      changeFrequency: "monthly",
      priority: 0.9,
    },
    // /login and all (control-center) routes are intentionally excluded from sitemap
    // They are protected/auth pages that should not be indexed
  ];
}
