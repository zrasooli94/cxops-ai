import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import SmoothScrollGate from "@/components/smooth-scroll-gate";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const siteUrl =
  "https" + "://" + "cxops-ai.vercel.app";

const siteDescription =
  "CXOps AI is an AI website assistant for businesses: it answers visitors from your own knowledge, hands conversations to your team when a person is needed, and keeps AI spending within limits you set.";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),

  title: {
    default:
      "CXOps AI | AI website assistant with human handoff",
    template:
      "%s | CXOps AI",
  },

  description:
    siteDescription,

  applicationName:
    "CXOps AI",

  robots: {
    index: true,
    follow: true,
  },

  openGraph: {
    type: "website",
    siteName: "CXOps AI",
    title:
      "CXOps AI | AI website assistant with human handoff",
    description:
      siteDescription,
  },

  twitter: {
    card: "summary",
    title:
      "CXOps AI | AI website assistant with human handoff",
    description:
      siteDescription,
  },
};

const structuredData = {
  "@context":
    "https" + "://" + "schema.org",

  "@type":
    "SoftwareApplication",

  name:
    "CXOps AI",

  url:
    siteUrl,

  sameAs: [
    "https://github.com/zrasooli94/cxops-ai",
  ],

  applicationCategory:
    "BusinessApplication",

  operatingSystem:
    "Web",

  description:
    siteDescription,

  featureList: [
    "Website chat assistant",
    "Answers grounded in business knowledge",
    "Human handoff queue",
    "Team workspaces and roles",
    "Exact-domain widget allowlists",
    "Monthly AI usage limits",
  ],
};

export default function RootLayout({
  children,
}: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable}`}
    >
      <body>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html:
              JSON.stringify(
                structuredData
              ),
          }}
        />

        <SmoothScrollGate />
        {children}
      </body>
    </html>
  );
}
