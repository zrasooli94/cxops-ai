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
      url: `${baseUrl}/tickets`,
      changeFrequency: "weekly",
      priority: 0.8,
    },
    {
      url: `${baseUrl}/agent`,
      changeFrequency: "weekly",
      priority: 0.9,
    },
    {
      url: `${baseUrl}/approvals`,
      changeFrequency: "weekly",
      priority: 0.8,
    },
    {
      url: `${baseUrl}/knowledge`,
      changeFrequency: "weekly",
      priority: 0.9,
    },
    {
      url: `${baseUrl}/runs`,
      changeFrequency: "weekly",
      priority: 0.7,
    },
    {
      url: `${baseUrl}/observability`,
      changeFrequency: "weekly",
      priority: 0.8,
    },
  ];
}
