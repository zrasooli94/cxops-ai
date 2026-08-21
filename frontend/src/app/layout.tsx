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

export const metadata: Metadata = {
  title: {
    default: "CXOps AI",
    template: "%s | CXOps AI",
  },
  description:
    "Intelligent Customer Experience Automation & RAG Platform",
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
        <SmoothScroll />
        {children}
      </body>
    </html>
  );
}
