import { permanentRedirect } from "next/navigation";

// Phase 1Q: the former platform overview described an engineering
// demonstration. Its content now lives on /features; keep the URL working for
// existing links and search results with a permanent redirect.
export default function PlatformPage() {
  permanentRedirect("/features");
}
