import { spawn } from "node:child_process";

const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", "--disable-gpu", "--no-first-run", "--remote-debugging-pipe", "about:blank",
], { stdio: ["ignore", "ignore", "inherit", "pipe", "pipe"] });
let nextId = 1;
const pending = new Map();
let buffer = "";
chrome.stdio[4].setEncoding("utf8");
chrome.stdio[4].on("data", (chunk) => {
  buffer += chunk;
  const frames = buffer.split("\0"); buffer = frames.pop() ?? "";
  for (const frame of frames) if (frame) {
    const message = JSON.parse(frame);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id); pending.delete(message.id);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    }
  }
});
function command(method, params = {}, sessionId) {
  const id = nextId++;
  chrome.stdio[3].write(`${JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })}\0`);
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const { targetId } = await command("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await command("Target.attachToTarget", { targetId, flatten: true });
  await command("Runtime.enable", {}, sessionId);
  await command("Page.enable", {}, sessionId);
  await command("Page.addScriptToEvaluateOnNewDocument", { source: `
    (() => {
      const originalFetch = globalThis.fetch.bind(globalThis);
      const probe = globalThis.__cxopsReplyRace = {
        armed: false, conversationA: null, conversationB: null,
        replyPosts: 0, replyDestinations: [], releasePost: null, rejectB: null,
        serverSaved: false, aRefreshCompleted: false, delayedPost: false, delayedB: false,
      };
      globalThis.fetch = (input, init = {}) => {
        const url = new URL(String(input), location.origin);
        const method = String(init.method || 'GET').toUpperCase();
        const match = url.pathname.match(/\\/api\\/local-demo\\/backend\\/conversations\\/(\\d+)\\/(messages|replies)$/);
        if (!match || !probe.armed) return originalFetch(input, init);
        const conversationId = Number(match[1]);
        if (method === 'POST' && match[2] === 'replies') {
          probe.replyPosts += 1;
          const body = JSON.parse(String(init.body || '{}'));
          probe.replyDestinations.push({ conversationId, body: body.body, clientRequestId: body.client_request_id });
          if (conversationId === probe.conversationA && !probe.delayedPost) {
            probe.delayedPost = true;
            return new Promise((resolve, reject) => {
              originalFetch(input, init).then((response) => {
                probe.serverSaved = true;
                probe.releasePost = () => resolve(response);
              }, reject);
            });
          }
          return originalFetch(input, init);
        }
        if (method === 'GET' && match[2] === 'messages' && conversationId === probe.conversationB && !probe.delayedB) {
          probe.delayedB = true;
          return new Promise((resolve) => { probe.rejectB = () => resolve(new Response(JSON.stringify({ detail: 'Synthetic B refresh failure' }), { status: 503, headers: { 'content-type': 'application/json' } })); });
        }
        if (method === 'GET' && match[2] === 'messages' && conversationId === probe.conversationA && probe.delayedPost) {
          return originalFetch(input, init).then((response) => { probe.aRefreshCompleted = true; return response; });
        }
        return originalFetch(input, init);
      };
    })();
  ` }, sessionId);
  await command("Page.navigate", { url: "http://127.0.0.1:3000/chat/owner-studio" }, sessionId);
  async function evaluate(expression) {
    const result = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
    return result.result.value;
  }
  async function waitFor(expression, timeout = 10000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) { if (await evaluate(expression)) return; await sleep(75); }
    throw new Error(`Timed out: ${expression}`);
  }
  const clickConversation = (id) => evaluate(`(()=>{const button=[...document.querySelectorAll('button')].find((item)=>item.textContent.includes('Conversation #${id}'));if(!button)return false;button.click();return true})()`);
  const clickText = (text) => evaluate(`(()=>{const button=[...document.querySelectorAll('button')].find((item)=>item.textContent.trim()===${JSON.stringify(text)});if(!button)return false;button.click();return true})()`);
  const setValue = (selector, value) => evaluate(`(()=>{const input=document.querySelector(${JSON.stringify(selector)});if(!input)return false;const setter=Object.getOwnPropertyDescriptor(input.constructor.prototype,'value').set;setter.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);

  await waitFor(`document.body.innerText.includes('Aster Cycle owner studio')`);
  const seeded = await evaluate(`(async()=>{
    const origin=location.origin;
    const ownerHeaders={'x-cxops-local-origin':origin};
    const createGap=async(label)=>{
      const before=await fetch('/api/local-demo/backend/conversations',{headers:ownerHeaders}).then((response)=>response.json());
      const known=new Set(before.items.map((item)=>item.id));
      const created=await fetch('/api/public/chat/sessions',{method:'POST',headers:{'content-type':'application/json','X-Embedding-Origin':origin},body:JSON.stringify({public_widget_key:'pk_local_aster_cycle_offline_demo'})}).then((response)=>response.json());
      const sent=await fetch('/api/public/chat/messages',{method:'POST',headers:{'content-type':'application/json','authorization':'Bearer '+created.session.token,'X-Embedding-Origin':origin},body:JSON.stringify({client_message_id:crypto.randomUUID(),text:label})}).then((response)=>response.json());
      if(!sent.handoff) throw new Error('synthetic gap did not hand off');
      const after=await fetch('/api/local-demo/backend/conversations',{headers:ownerHeaders}).then((response)=>response.json());
      return after.items.find((item)=>!known.has(item.id)).id;
    };
    const conversationA=await createGap('RACE-A unique visitor question');
    const conversationB=await createGap('RACE-B unique visitor question');
    const handoffs=await fetch('/api/local-demo/backend/staff/public-chat/handoff',{headers:ownerHeaders}).then((response)=>response.json());
    const sessionIds=[];
    for(const conversationId of [conversationA,conversationB]){
      const handoff=handoffs.sessions.find((item)=>item.conversation_id===conversationId);
      const assigned=await fetch('/api/local-demo/backend/staff/public-chat/sessions/'+handoff.session_id+'/assign',{method:'POST',headers:ownerHeaders});
      if(!assigned.ok) throw new Error('assign failed '+assigned.status);
      sessionIds.push(handoff.session_id);
    }
    return {conversationA,conversationB,sessionIds};
  })()`);
  await command("Page.navigate", { url: "http://127.0.0.1:3000/chat/owner-studio?view=inbox" }, sessionId);
  await waitFor(`document.body.innerText.includes('Conversation #${seeded.conversationA}') && document.body.innerText.includes('Conversation #${seeded.conversationB}')`);
  await clickConversation(seeded.conversationA);
  await waitFor(`document.querySelector('[data-conversation-message-count]')?.innerText.includes('RACE-A unique visitor question') && !!document.querySelector('input[placeholder="Reply as staff…"]')`);
  await setValue('input[placeholder="Reply as staff…"]', 'STAFF-A exactly-once reply');
  await evaluate(`Object.assign(globalThis.__cxopsReplyRace,{armed:true,conversationA:${seeded.conversationA},conversationB:${seeded.conversationB}})`);
  await clickText("Send");
  await waitFor(`globalThis.__cxopsReplyRace.replyPosts===1 && globalThis.__cxopsReplyRace.serverSaved && typeof globalThis.__cxopsReplyRace.releasePost==='function'`);

  await clickConversation(seeded.conversationB);
  await waitFor(`typeof globalThis.__cxopsReplyRace.rejectB==='function' && document.body.innerText.includes('Loading conversation')`);
  await setValue('input[placeholder="Reply as staff…"]', 'DRAFT-B must survive stale A and failed B refresh');
  await evaluate(`globalThis.__cxopsReplyRace.rejectB()`);
  await waitFor(`document.querySelector('[role="alert"]')?.textContent.includes('Synthetic B refresh failure')`);
  if (await evaluate(`document.querySelector('input[placeholder="Reply as staff…"]')?.value!=='DRAFT-B must survive stale A and failed B refresh'`)) throw new Error('failed B refresh cleared B draft');

  await evaluate(`globalThis.__cxopsReplyRace.releasePost()`);
  await waitFor(`globalThis.__cxopsReplyRace.aRefreshCompleted`);
  if (await evaluate(`document.querySelector('[data-conversation-message-count]')?.innerText.includes('RACE-A unique visitor question')`)) throw new Error('stale A transcript rendered under B');
  if (await evaluate(`document.querySelector('input[placeholder="Reply as staff…"]')?.value!=='DRAFT-B must survive stale A and failed B refresh'`)) throw new Error('late A completion cleared B draft');
  if (!await evaluate(`document.querySelector('[role="alert"]')?.textContent.includes('Synthetic B refresh failure')`)) throw new Error('late A completion cleared B error state');

  await clickConversation(seeded.conversationA);
  await waitFor(`document.querySelector('[data-conversation-message-count]')?.innerText.includes('STAFF-A exactly-once reply') && !document.querySelector('[role="alert"]')`);
  if (await evaluate(`document.querySelector('input[placeholder="Reply as staff…"]')?.value!==''`)) throw new Error('B draft leaked into A');
  await clickConversation(seeded.conversationB);
  await waitFor(`document.querySelector('[data-conversation-message-count]')?.innerText.includes('RACE-B unique visitor question') && !document.querySelector('[role="alert"]')`);
  if (await evaluate(`document.querySelector('[data-conversation-message-count]')?.innerText.includes('STAFF-A exactly-once reply')`)) throw new Error('A reply rendered in B');

  const verified = await evaluate(`(async()=>{
    const headers={'x-cxops-local-origin':location.origin};
    const [a,b]=await Promise.all([
      fetch('/api/local-demo/backend/conversations/${seeded.conversationA}/messages',{headers}).then((response)=>response.json()),
      fetch('/api/local-demo/backend/conversations/${seeded.conversationB}/messages',{headers}).then((response)=>response.json()),
    ]);
    const probe=globalThis.__cxopsReplyRace;
    return {
      replyPosts:probe.replyPosts,
      destinations:probe.replyDestinations,
      aReplyCount:a.filter((message)=>message.body==='STAFF-A exactly-once reply').length,
      bReplyCount:b.filter((message)=>message.body==='STAFF-A exactly-once reply').length,
    };
  })()`);
  if (verified.replyPosts !== 1 || verified.destinations.length !== 1 || verified.destinations[0].conversationId !== seeded.conversationA || !verified.destinations[0].clientRequestId || verified.aReplyCount !== 1 || verified.bReplyCount !== 0) throw new Error(`reply destination/count mismatch: ${JSON.stringify(verified)}`);
  const cleanup = await evaluate(`(async()=>{const headers={'x-cxops-local-origin':location.origin};const statuses=[];for(const sessionId of ${JSON.stringify(seeded.sessionIds)}){statuses.push((await fetch('/api/local-demo/backend/staff/public-chat/sessions/'+sessionId+'/resolve',{method:'POST',headers})).status);}return statuses;})()`);
  if (cleanup.some((status) => status !== 200)) throw new Error(`handoff cleanup failed: ${JSON.stringify(cleanup)}`);

  console.log(JSON.stringify({
    stale_success_suppressed: true,
    selected_loading_and_error_scoped: true,
    selected_draft_preserved: true,
    repeated_switch_transcripts_scoped: true,
    reply_saved_once_to_intended_conversation: true,
    no_switch_resend: true,
    synthetic_handoffs_resolved: true,
  }));
  chrome.kill();
}

main().catch((error) => { console.error(error); chrome.kill(); process.exitCode = 1; });
