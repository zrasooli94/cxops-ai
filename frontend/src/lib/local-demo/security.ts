const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1"]);

function isSameLoopbackOrigin(candidate: string, requestOrigin: string): boolean {
  const candidateUrl = new URL(candidate);
  const requestUrl = new URL(requestOrigin);
  return LOOPBACK_HOSTS.has(candidateUrl.hostname)
    && LOOPBACK_HOSTS.has(requestUrl.hostname)
    && candidateUrl.protocol === requestUrl.protocol
    && candidateUrl.port === requestUrl.port;
}

export const LOCAL_DEMO_PATHS = [
  /^staff\/tenant-config\/public-chat$/,
  /^staff\/tenant-config\/offline-faqs$/,
  /^staff\/public-chat\/handoff$/,
  /^staff\/public-chat\/sessions\/\d+\/(assign|release|resolve)$/,
  /^conversations$/,
  /^conversations\/\d+\/messages$/,
  /^conversations\/\d+\/replies$/,
];

export function isAllowedLocalDemoPath(path: string): boolean {
  return LOCAL_DEMO_PATHS.some((pattern) => pattern.test(path));
}

export function isAuthorizedLocalDemoRequest(input: {
  nodeEnv: string | undefined;
  hostname: string;
  method: string;
  origin: string | null;
  fetchSite?: string | null;
  localOrigin?: string | null;
  requestOrigin: string;
}): boolean {
  if (input.nodeEnv === "production" || !LOOPBACK_HOSTS.has(input.hostname)) return false;
  if (["GET", "HEAD"].includes(input.method.toUpperCase())) return true;
  if (!input.origin) {
    if (input.fetchSite === "same-origin") return true;
    if (!input.localOrigin) return false;
    try {
      return isSameLoopbackOrigin(input.localOrigin, input.requestOrigin);
    } catch {
      return false;
    }
  }
  try {
    return isSameLoopbackOrigin(input.origin, input.requestOrigin);
  } catch {
    return false;
  }
}
