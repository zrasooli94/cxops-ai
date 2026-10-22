import { spawn } from "node:child_process";

const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", "--disable-gpu", "--no-first-run", "--remote-debugging-pipe", "about:blank",
], { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });
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
  const { targetId } = await command("Target.createTarget", { url: "http://127.0.0.1:3000/chat/owner-studio" });
  const { sessionId } = await command("Target.attachToTarget", { targetId, flatten: true });
  await command("Runtime.enable", {}, sessionId);
  async function evaluate(expression) {
    const result = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline && !await evaluate(`document.body.innerText.includes('Aster Cycle owner studio')`)) await sleep(100);

  const result = await evaluate(`(async()=>{
    const origin=location.origin;
    const ownerHeaders={'x-cxops-local-origin':origin};
    const requestJson=async(url,options={})=>{
      const response=await fetch(url,options);
      if(!response.ok) throw new Error('HTTP '+response.status+' for '+url+': '+await response.text());
      return response.status===204?null:response.json();
    };
    const phrase='Synthetic zephyr pickup is available after 8:20 PM.';
    const question='When is synthetic zephyr pickup available?';
    const form=new FormData();
    form.set('file',new File([phrase],'freshness-zephyr.txt',{type:'text/plain'}));
    const uploaded=await requestJson('/api/local-demo/backend/staff/tenant-config/offline-documents',{method:'POST',headers:ownerHeaders,body:form});
    const create=()=>requestJson('/api/public/chat/sessions',{method:'POST',headers:{'content-type':'application/json','X-Embedding-Origin':origin},body:JSON.stringify({public_widget_key:'pk_local_aster_cycle_offline_demo'})});
    const send=(token,id)=>requestJson('/api/public/chat/messages',{method:'POST',headers:{'content-type':'application/json','authorization':'Bearer '+token,'X-Embedding-Origin':origin},body:JSON.stringify({client_message_id:id,text:question})});
    const oldSession=await create();
    const oldAnswer=await send(oldSession.session.token,'freshness-old-browser');
    await requestJson('/api/local-demo/backend/staff/tenant-config/offline-documents/'+uploaded.document_id,{method:'DELETE',headers:ownerHeaders});
    const evidence=await requestJson('/api/local-demo/backend/staff/tenant-config/offline-evidence?question='+encodeURIComponent(question),{headers:ownerHeaders});
    const freshSession=await create();
    const freshAnswer=await send(freshSession.session.token,'freshness-new-browser');
    const history=await requestJson('/api/public/chat/sessions/updates?after_id=0',{headers:{'authorization':'Bearer '+oldSession.session.token,'X-Embedding-Origin':origin}});
    return {oldAnswer,freshAnswer,evidence,history,documentId:uploaded.document_id};
  })()`);

  const historicalText = JSON.stringify(result.history);
  if (!result.oldAnswer.reply?.includes("8:20 PM")) throw new Error("initial grounded answer missing");
  if (result.evidence.items?.some((item) => item.excerpt.includes("8:20 PM"))) throw new Error("removed evidence remained current");
  if (!result.freshAnswer.handoff || result.freshAnswer.reply?.includes("8:20 PM")) throw new Error("fresh visitor reused removed knowledge");
  if (!historicalText.includes("8:20 PM")) throw new Error("historical transcript was rewritten");
  console.log(JSON.stringify({
    initial_grounded_answer: true,
    removed_source_absent_from_owner_evidence: true,
    fresh_visitor_does_not_receive_removed_knowledge: true,
    restored_historical_transcript_retains_sent_answer: true,
  }));
  chrome.kill();
}

main().catch((error) => { console.error(error); chrome.kill(); process.exitCode = 1; });
