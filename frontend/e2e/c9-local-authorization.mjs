/* C9 local-only backend contract fixture.
 *
 * This is NOT the real CXOps backend. It serves ONLY localhost /me/* GETs,
 * backed by fictional users/orgs and no database or provider connections.
 * Never import it into application code or deploy it to a public server.
 */
import { createServer } from "node:http";

const HOST = "127.0.0.1";
const PORT = 18767;
const ORGS = [
  {
    id: 9001, name: "Fictional Aster Workshop", industry: "bicycle repair",
    external_id: "fixture-aster", created_at: "2026-10-20T00:00:00Z",
  },
  {
    id: 9002, name: "Fictional Birch Print Studio", industry: "printing",
    external_id: "fixture-birch", created_at: "2026-10-20T00:00:00Z",
  },
];
const READ = ["evaluation.read"];
const MANAGE = ["evaluation.read", "evaluation.manage"];
const accounts = new Set([
  "manager-a", "manager-both", "read-only", "manage-only",
  "revoked", "no-membership", "cross-tenant",
]);
const audit = [];
const authCalls = new Map();

function json(res, status, body) {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-robots-tag": "noindex, nofollow",
  });
  res.end(JSON.stringify(body));
}

function readFixtureIdentity(req) {
  const header = req.headers.authorization;
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return null;
  const token = header.slice(7).split(".");
  if (token.length !== 3 || token[0] !== "c9" || token[2] !== "local-only-test") {
    return null;
  }
  let payload;
  try {
    payload = JSON.parse(Buffer.from(token[1], "base64url").toString("utf8"));
  } catch {
    return null;
  }
  if (
    !payload ||
    !accounts.has(payload.sub) ||
    typeof payload.exp !== "number" ||
    payload.exp < Date.now() / 1000
  ) {
    return null;
  }
  return payload.sub;
}

function membershipFor(subject) {
  return subject === "no-membership" ? [ORGS[1]] : ORGS;
}

function permitted(subject, orgId, authCallNumber) {
  if (subject === "read-only") return READ;
  if (subject === "manage-only") return ["evaluation.manage"];
  if (subject === "manager-a" || subject === "cross-tenant") {
    return orgId === 9001 ? MANAGE : READ;
  }
  if (subject === "revoked" && authCallNumber >= 2) {
    return READ; // Parent layout passed, child page MUST recheck and deny.
  }
  return MANAGE;
}

const server = createServer((req, res) => {
  const pathname = new URL(req.url ?? "/", "http://" + HOST + ":" + PORT).pathname;
  if (req.method === "GET" && pathname === "/__c9__/audit") {
    json(res, 200, { requests: audit, not_production: true });
    return;
  }
  const subject = readFixtureIdentity(req);
  const requestedOrg = req.headers["x-cxops-organization-id"];
  audit.push({
    subject: subject ?? "invalid",
    path: pathname,
    method: req.method ?? "unknown",
    selected_organization: requestedOrg ?? null,
  });
  if (!subject) {
    json(res, 401, { detail: "Fixture-only authorization denied" });
    return;
  }
  if (req.method !== "GET") {
    json(res, 405, { detail: "C9 mock never performs writes" });
    return;
  }
  if (pathname === "/me/organizations") {
    json(res, 200, membershipFor(subject));
    return;
  }
  if (pathname === "/me/authorization") {
    const org = Number(requestedOrg);
    if (
      !Number.isSafeInteger(org) ||
      !membershipFor(subject).some((o) => o.id === org)
    ) {
      json(res, 403, { detail: "Organization membership not found" });
      return;
    }
    const key = subject + ":" + String(org);
    const call = (authCalls.get(key) ?? 0) + 1;
    authCalls.set(key, call);
    json(res, 200, {
      organization_id: org,
      role: permitted(subject, org, call).includes("evaluation.manage")
        ? "fixture-manager" : "fixture-viewer",
      capabilities: permitted(subject, org, call),
      organization_status: "active",
    });
    return;
  }
  json(res, 404, { detail: "Unknown local-only C9 test path" });
});

server.listen(PORT, HOST, () => {
  process.stdout.write("C9 fictional loopback backend ready\n");
});
process.on("SIGTERM", () => server.close());
