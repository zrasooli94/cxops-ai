/**
 * CXOps AI - off-by-default five-minute schedule transport PROTOTYPE.
 *
 * No executable Python worker, DB access, or production API endpoint lives
 * here. The receiver endpoint DOES NOT EXIST until separately reviewed.
 * Do not turn on the Worker or supply secrets without deployment approval.
 */
export const CLOCK_CRON = '*/5 * * * *';
export const CLOCK_SLOT_MS = 5 * 60 * 1000;
export const CLOCK_MAX_AGE_MS = 15 * 60 * 1000;
export const CLOCK_MAX_FUTURE_MS = 60 * 1000;
export const FIXED_TICK_URL = 'https://cxops-ai.onrender.com/internal/worker-clock/tick';
export const TICK_PATH = '/internal/worker-clock/tick';
const encoder = new TextEncoder();

function hex(bytes) {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Construct one pinned, signed, body-bound dispatch. No arbitrary destinations. */
export async function signTick({ scheduledTime, secret, nonce, keyId = 'current', subtle = crypto.subtle }) {
  if (!Number.isSafeInteger(scheduledTime) || scheduledTime <= 0 || scheduledTime % CLOCK_SLOT_MS !== 0) {
    throw new Error('invalid-schedule-slot');
  }
  if (typeof secret !== 'string' || secret.length < 32 || secret.length > 256) {
    throw new Error('missing-dispatch-key');
  }
  if (typeof nonce !== 'string' || !/^[a-f\d]{8}-[a-f\d]{4}-4[a-f\d]{3}-[89ab][a-f\d]{3}-[a-f\d]{12}$/i.test(nonce)) {
    throw new Error('invalid-request-nonce');
  }
  if (typeof keyId !== 'string' || !/^[a-z0-9][a-z0-9-]{0,31}$/.test(keyId)) {
    throw new Error('invalid-dispatch-key-id');
  }
  const slot = scheduledTime / CLOCK_SLOT_MS;
  const body = JSON.stringify({ version: 1, slot });
  const digest = hex(new Uint8Array(await subtle.digest('SHA-256', encoder.encode(body))));
  const canonical = ['cxops-clock-v1', 'POST', TICK_PATH, scheduledTime, nonce, keyId, digest].join('\n');
  const key = await subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signature = hex(new Uint8Array(await subtle.sign('HMAC', key, encoder.encode(canonical))));
  return {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-cxops-clock-time': String(scheduledTime),
      'x-cxops-clock-nonce': nonce,
      'x-cxops-clock-key-id': keyId,
      'x-cxops-clock-signature': `v1=${signature}`,
    },
    body,
    redirect: 'error',
    cache: 'no-store',
    credentials: 'omit',
  };
}

/**
 * Return no-op while disabled. Enabling alone will not add a cron trigger:
 * wrangler.jsonc explicitly has an empty cron list.
 */
export async function runScheduledTick(event, env, {
  now = () => Date.now(),
  request = (...args) => fetch(...args),
  makeNonce = () => crypto.randomUUID(),
  delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  if (env?.CXOPS_CLOCK_DISPATCH_ENABLED !== 'true') return { status: 'disabled' };
  if (event?.cron !== CLOCK_CRON) throw new Error('invalid-cron');
  const at = event?.scheduledTime;
  const current = now();
  if (!Number.isSafeInteger(at) || !Number.isSafeInteger(current) ||
      at > current + CLOCK_MAX_FUTURE_MS || current - at > CLOCK_MAX_AGE_MS) {
    throw new Error('stale-or-future-scheduled-event');
  }
  // No URL from request, event or environment is ever accepted.
  const params = await signTick({
    scheduledTime: at,
    secret: env.CXOPS_CLOCK_DISPATCH_KEY,
    keyId: env.CXOPS_CLOCK_DISPATCH_KEY_ID || 'current',
    nonce: makeNonce(),
  });
  // Exactly one bounded retry for transient gateway/network failure. Reuse
  // the *same* signed body/nonce/slot; the future receiver must deduplicate
  // durably under its PostgreSQL slot key before acknowledging the request.
  for (let attempt = 0; attempt < 2; attempt++) {
    let response;
    try {
      response = await request(FIXED_TICK_URL, {
        ...params,
        signal: AbortSignal.timeout(12000),
      });
    } catch {
      if (attempt === 0) { await delay(500); continue; }
      throw new Error('clock-upstream-unavailable');
    }
    if ([200, 202].includes(response?.status)) {
      return { status: 'acknowledged', slot: at / CLOCK_SLOT_MS };
    }
    if ([502, 503, 504].includes(response?.status) && attempt === 0) {
      await delay(500);
      continue;
    }
    // Never retry authorization failures, redirects, malformed or rate-limit
    // responses. Do not log raw upstream responses, signed data or secrets.
    throw new Error('clock-trigger-not-acknowledged');
  }
  throw new Error('clock-upstream-unavailable');
}

export default {
  async fetch() { return new Response('Not found', { status: 404 }); },
  async scheduled(event, env) { await runScheduledTick(event, env); },
};
