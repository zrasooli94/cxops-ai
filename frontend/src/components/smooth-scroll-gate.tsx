"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";

// Phase 1P.9R: the embedded chat widget (/chat/embed) is framed on tenants'
// sites and loaded on their every page view. It has no use for the site's
// smooth scrolling, so gsap + lenis (~130 KiB of JavaScript) are loaded
// lazily, and never on the widget route.
const SmoothScroll = dynamic(() => import("./smooth-scroll"), { ssr: false });

export function isWidgetRoute(pathname: string | null): boolean {
  return pathname !== null && pathname.startsWith("/chat/embed");
}

export default function SmoothScrollGate() {
  const pathname = usePathname();
  if (isWidgetRoute(pathname)) {
    return null;
  }
  return <SmoothScroll />;
}
