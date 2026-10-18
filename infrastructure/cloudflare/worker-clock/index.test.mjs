import test from 'node:test';
import assert from 'node:assert/strict';
import api, { runScheduledTick, signTick, FIXED_TICK_URL, CLOCK_CRON, CLOCK_SLOT_MS } from './index.mjs';

const clock = 1800000000000;
const nonce = '11111111-1111-4111-8111-111111111111';
const key = 'a'.repeat(40);
const env = { CXOPS_CLOCK_DISPATCH_ENABLED: 'true', CXOPS_CLOCK_DISPATCH_KEY: key };
const event = { cron: CLOCK_CRON, scheduledTime: clock };

test('no outbound network or key lookup while default-disabled', async () => {
  let count = 0;
  const result = await runScheduledTick(event, {}, {request: () => { count++; throw new Error('network'); }});
  assert.deepEqual(result, {status:'disabled'});
  assert.equal(count, 0);
});

test('one signed request to the only pinned route', async () => {
  const calls = [];
  const result = await runScheduledTick(event, {...env, DISPATCH_URL: 'https://evil.invalid'}, {
    now: () => clock + 2000, makeNonce: () => nonce,
    request: async (url, init) => { calls.push({url, init}); return {status: 202}; },
  });
  assert.deepEqual(result, {status:'acknowledged',slot: clock / CLOCK_SLOT_MS});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, FIXED_TICK_URL);
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.redirect, 'error');
  assert.equal(calls[0].init.credentials, 'omit');
  assert.equal(calls[0].init.headers['x-cxops-clock-nonce'], nonce);
  assert.equal(calls[0].init.headers['x-cxops-clock-key-id'], 'current');
  assert.match(calls[0].init.headers['x-cxops-clock-signature'], /^v1=[a-f0-9]{64}$/);
});

test('clock HMAC body bound: known cross-language vector', async () => {
  const a = await signTick({scheduledTime:clock,secret:key,nonce});
  assert.deepEqual(JSON.parse(a.body),{version:1,slot:clock/CLOCK_SLOT_MS});
  assert.equal(a.headers['x-cxops-clock-signature'], 'v1=20f6df674101e7d22b157201ed800a838215bb5e062714819547c5d04ded5d41');
});

test('reject bad cron, stale, future, invalid slot, and short secret before fetch', async () => {
  for (const [e, v] of [
    [{...event,cron:'* * * * *'},env],
    [{...event,scheduledTime:clock-20*60*1000},env],
    [{...event,scheduledTime:clock+10*60*1000},env],
    [{...event,scheduledTime:clock+1},env],
    [event,{...env,CXOPS_CLOCK_DISPATCH_KEY:'short'}],
  ]) {
    let count=0;
    await assert.rejects(runScheduledTick(e,v,{now:()=>clock,makeNonce:()=>nonce,request:()=>{count++;}}));
    assert.equal(count,0);
  }
});

test('never treats errors/redirects as an acknowledgement', async () => {
  for (const status of [301,401,403,404,429,500,503]) {
    let count=0;
    const isRetryable = [503].includes(status);
    await assert.rejects(runScheduledTick(event,env,{now:()=>clock,makeNonce:()=>nonce,
      delay:async()=>{},request:async()=>{count++;return {status};}}),/not-acknowledged/);
    assert.equal(count,isRetryable ? 2 : 1);
  }
});

test('no public HTTP control-plane endpoint', async () => {
  assert.equal((await api.fetch()).status,404);
});

test('retries a gateway failure once with identical signed request', async () => {
  const attempts = [];
  const result = await runScheduledTick(event,env,{now:()=>clock,makeNonce:()=>nonce,delay:async()=>{},
    request:async(_url,req)=>{attempts.push(req);return {status: attempts.length === 1 ? 503 : 202};}});
  assert.equal(result.status,'acknowledged');
  assert.equal(attempts.length,2);
  assert.equal(attempts[0].body,attempts[1].body);
  assert.deepEqual(attempts[0].headers,attempts[1].headers);
});

test('retries a thrown transient network error only once', async () => {
  let tries=0;
  const result=await runScheduledTick(event,env,{now:()=>clock,makeNonce:()=>nonce,delay:async()=>{},
    request:async()=>{tries++;if(tries===1)throw new Error('private network info');return {status:200};}});
  assert.equal(result.status,'acknowledged');
  assert.equal(tries,2);
});
