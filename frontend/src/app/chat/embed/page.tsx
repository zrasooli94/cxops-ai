import type { Metadata } from "next";

import { PublicChatWidget } from "./embed";

// Public web-chat embed host (Phase 1P.1).
//
// Lives outside ``(control-center)``, with no capability guard: the backend
// resolves the tenant from the public widget key (``?key=``) and no staff
// authorization is involved. Robots are blocked (per-page metadata plus the
// global robots.ts). The page renders the client widget in ./embed.tsx; the
// widget carries the key in the embed URL.

const widgetKey = process.env.CXOPS_PUBLIC_CHAT_WIDGET_KEY ?? "";

export const metadata: Metadata = {
  robots: {
    index: false,
    follow: false,
  },
};

interface EmbedPageProps {
  searchParams: Promise<{
    key?: string | string[];
  }>;
}

export default async function EmbedPage({
  searchParams,
}: EmbedPageProps) {
  const params = await searchParams;
  const keyValue = Array.isArray(params.key)
    ? params.key[0]
    : params.key;

  const resolvedKey =
    keyValue && keyValue.length >= 8
      ? keyValue
      : widgetKey;

  if (!resolvedKey) {
    return (
      <main>
        <p>Unable to load the chat widget.</p>
      </main>
    );
  }

  return (
    <main>
      <PublicChatWidget widgetKey={resolvedKey} />
    </main>
  );
}