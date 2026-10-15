/**
 * Build-level Preview safety smoke test. Uses only loopback networking and a
 * disposable stub API, NEVER production or Nhost.
 *
 * Run after a Next build with VERCEL_ENV=preview:
 *   VERCEL_ENV=preview node scripts/preview-isolation-smoke.mjs
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import { createServer as createTcpServer } from "node:net";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const serverFile = resolve(".next/standalone/server.js");
assert.ok(existsSync(serverFile), "Build the standalone Next.js server first");
assert.equal(process.env.VERCEL_ENV, "preview", "Smoke must run as Vercel Preview");

async function listen(server) {
  await new Promise((yes, no) => server.listen(0, "127.0.0.1", yes).once("error", no));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  return address.port;
}

async function unusedPort() {
  const server = createTcpServer();
  const port = await listen(server);
  await new Promise((yes) => server.close(yes));
  return port;
}

async function closeServer(server) {
  await new Promise((yes) => server.close(yes));
}

async function stopChild(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  const exited = new Promise((yes) => child.once("exit", yes));
  const won = await Promise.race([exited.then(() => true), delay(3000).then(() => false)]);
  if (!won) child.kill("SIGKILL");
}

async function main() {
  let receivedBackendRequests = 0;
  const fakeBackend = createServer((_request, response) => {
    receivedBackendRequests++;
    response.writeHead(200, { "content-type": "application/json" });
    response.end('{"unsafe":"request reached backend"}');
  });
  const backendPort = await listen(fakeBackend);
  const appPort = await unusedPort();
  const origin = `http://127.0.0.1:${appPort}`;

  let output = "";
  const child = spawn(process.execPath, [serverFile], {
    cwd: process.cwd(),
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      NODE_ENV: "production",
      VERCEL_ENV: "preview",
      HOSTNAME: "127.0.0.1",
      PORT: String(appPort),
      // Loopback-only trap: if a route unexpectedly forwards traffic, the
      // counter below records it. No production host is ever contacted.
      BACKEND_API_URL: `http://127.0.0.1:${backendPort}`,
      CXOPS_PREVIEW_AUTH_ENABLED: "false",
      CXOPS_STAGING_ISOLATION_VERIFIED: "false",
      NEXT_PUBLIC_NHOST_SUBDOMAIN: "",
      NEXT_PUBLIC_NHOST_REGION: "",
    },
  });
  child.stdout?.on("data", (chunk) => { output = (output + String(chunk)).slice(-4000); });
  child.stderr?.on("data", (chunk) => { output = (output + String(chunk)).slice(-4000); });

  async function request(path, init = {}) {
    return fetch(origin + path, { ...init, redirect: "manual", signal: AbortSignal.timeout(9000) });
  }
  try {
    let reachable = false;
    for (let attempt = 0; attempt < 50; attempt++) {
      if (child.exitCode !== null) throw new Error(`Next server exited prematurely: ${output}`);
      try {
        const response = await request("/login");
        if (response.status === 200) { reachable = true; break; }
      } catch { /* startup may not be complete */ }
      await delay(200);
    }
    assert.ok(reachable, `Next Preview server did not start: ${output}`);

    const login = await request("/login");
    assert.equal(login.status, 200);
    const loginHtml = await login.text();
    assert.ok(loginHtml.includes("Staff sign-in is not enabled on this preview"),
      "Preview should display isolation notice, not a credential form");
    assert.ok(!loginHtml.includes('name="password"'), "Preview must not expose a login form");

    const probes = [
      ["GET", "/api/backend/me/organizations", undefined, 503],
      ["POST", "/api/backend/staff/public-chat/handoff", "{}", 503],
      ["GET", "/api/public/chat/config", undefined, 503],
      ["POST", "/api/public/chat/sessions", '{"widget_key":"synthetic"}', 503],
      ["GET", "/api/public/leads", undefined, 503],
      ["POST", "/api/public/leads", '{"company_name":"synthetic"}', 503],
    ];
    for (const [method, path, body, expected] of probes) {
      const result = await request(path, {
        method,
        ...(body ? { body, headers: { "content-type": "application/json" } } : {}),
      });
      assert.equal(result.status, expected, `${method} ${path} must fail closed`);
    }

    for (const path of ["/api/auth/landing", "/api/auth/session"]) {
      const result = await request(path);
      assert.ok([307, 308].includes(result.status), `${path} must redirect without backend contact`);
      const location = result.headers.get("location");
      assert.ok(location && new URL(location, origin).pathname === "/login",
        `${path} should redirect to safe preview notice`);
    }

    // Rewrites in next.config.ts must not expose production health or version.
    for (const path of ["/health", "/ready", "/version"]) {
      const result = await request(path);
      assert.equal(result.status, 404, `${path} must NOT rewrite to the backend in Preview`);
    }
    assert.equal(receivedBackendRequests, 0,
      "Preview must make ZERO requests to its configured backend");
    process.stdout.write("PASS: Preview login and 11 routes fail closed; zero backend calls.\n");
  } finally {
    await stopChild(child);
    await closeServer(fakeBackend);
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
