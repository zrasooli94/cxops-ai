// Origins allowed to frame the public chat widget (Phase 1Q).
//
// The base list lives in next.config.ts. New customers' domains are appended
// from CXOPS_WIDGET_FRAME_ANCESTORS (comma or whitespace separated) so adding
// an approved tenant needs an environment change and a redeploy, not a code
// change. Every entry must be an exact https origin; wildcards, paths, http
// and anything unparsable are dropped (fail closed), and the caller reports
// what was rejected so a typo is visible in the build log.

export interface FrameAncestorParse {
  origins: string[];
  rejected: string[];
}

export function parseFrameAncestors(
  raw: string | undefined | null,
  options: { allowLoopbackHttp?: boolean } = {},
): FrameAncestorParse {
  const origins: string[] = [];
  const rejected: string[] = [];
  if (!raw) {
    return { origins, rejected };
  }
  for (const entry of raw.split(/[\s,]+/)) {
    const candidate = entry.trim();
    if (!candidate) {
      continue;
    }
    if (
      isExactHttpsOrigin(candidate) ||
      (options.allowLoopbackHttp === true && isLoopbackHttpOrigin(candidate))
    ) {
      if (!origins.includes(candidate)) {
        origins.push(candidate);
      }
    } else {
      rejected.push(candidate);
    }
  }
  return { origins, rejected };
}

function isExactHttpsOrigin(value: string): boolean {
  if (value.includes("*") || value.endsWith("/")) {
    return false;
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  return (
    url.protocol === "https:" &&
    !url.username &&
    !url.password &&
    url.hostname.includes(".") &&
    url.origin === value
  );
}

/** http://localhost:PORT style origins - only ever accepted outside production. */
function isLoopbackHttpOrigin(value: string): boolean {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  return (
    url.protocol === "http:" &&
    ["localhost", "127.0.0.1"].includes(url.hostname) &&
    url.origin === value
  );
}

export function mergeFrameAncestors(base: readonly string[], extra: readonly string[]): string[] {
  const merged = [...base];
  for (const origin of extra) {
    if (!merged.includes(origin)) {
      merged.push(origin);
    }
  }
  return merged;
}
