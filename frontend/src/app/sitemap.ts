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
  const pages: [string, number, "weekly" | "monthly"][] = [
    ["/", 1, "weekly"],
    ["/features", 0.95, "weekly"],
    ["/how-it-works", 0.9, "monthly"],
    ["/solutions", 0.9, "monthly"],
    ["/early-access", 0.9, "monthly"],
    ["/faq", 0.8, "monthly"],
    ["/platform/evaluating-ai-support-platforms", 0.7, "monthly"],
  ];
  // /login, /invite, /operator and all (control-center) routes are excluded:
  // they are authenticated pages that should not be indexed.
  return pages.map(([path, priority, changeFrequency]) => ({
    url: `${origin}${path}`,
    changeFrequency,
    priority,
  }));
}
