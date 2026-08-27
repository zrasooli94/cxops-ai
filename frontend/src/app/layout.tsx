import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import SmoothScroll from "@/components/smooth-scroll";

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
  "CXOps AI is a demonstration customer experience operations platform for support-ticket workflows, RAG-assisted knowledge retrieval, AI agents, human approvals, execution audit trails, and operational observability.";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),

  title: {
    default:
      "CXOps AI | Customer Experience Operations & RAG Platform",
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
      "CXOps AI | Customer Experience Operations & RAG Platform",
    description:
      siteDescription,
  },

  twitter: {
    card: "summary",
    title:
      "CXOps AI | Customer Experience Operations & RAG Platform",
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
    "Support ticket workflows",
    "RAG-assisted knowledge retrieval",
    "AI agent workflows",
    "Human approval controls",
    "Execution audit trails",
    "Operational observability",
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

        <SmoothScroll />
        {children}
      </body>
    </html>
  );
}
