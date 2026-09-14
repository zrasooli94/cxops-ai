import type { MetadataRoute } from "next";

const baseUrl =
  "https" + "://" + "cxops-ai.vercel.app";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: `${baseUrl}/`,
      changeFrequency: "weekly",
      priority: 1,
    },
    {
      url: `${baseUrl}/platform`,
      changeFrequency: "weekly",
      priority: 0.95,
    },
    {
      url: `${baseUrl}/platform/evaluating-ai-support-platforms`,
      changeFrequency: "monthly",
      priority: 0.9,
    },
    // /login and all (control-center) routes are intentionally excluded from sitemap
    // They are protected/auth pages that should not be indexed
  ];
}