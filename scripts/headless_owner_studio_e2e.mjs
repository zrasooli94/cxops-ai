import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

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
  return new Promise((resolve, reject) => pending.set(id, {
    resolve,
    reject: (error) => reject(new Error(`${method}: ${error.message}`)),
  }));
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const { targetId } = await command("Target.createTarget", { url: "http://127.0.0.1:3000/chat/owner-studio" });
  const { sessionId } = await command("Target.attachToTarget", { targetId, flatten: true });
  await command("Runtime.enable", {}, sessionId);
  await command("Page.enable", {}, sessionId);
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false }, sessionId);
  async function evaluate(expression) {
    let result;
    try {
      result = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    } catch (error) {
      throw new Error(`${error.message}; expression=${expression.slice(0, 160)}`);
    }
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }
  async function waitFor(expression, timeout = 10000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) { if (await evaluate(expression)) return; await sleep(100); }
    throw new Error(`Timed out: ${expression}`);
  }
  const clickText = (text) => evaluate(`(()=>{const e=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(text)});if(!e)return false;e.click();return true})()`);
  const setValue = (selector, value) => evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return false;const s=Object.getOwnPropertyDescriptor(e.constructor.prototype,'value').set;s.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);

  await waitFor(`location.origin==='http://127.0.0.1:3000'`);
  await evaluate(`sessionStorage.removeItem('cxops.chat.owner-studio.preview.v1')`);
  await command("Page.reload", {}, sessionId);
  await waitFor(`document.querySelector('input')?.value==='Aster Cycle Studio'`);
  const originalName = await evaluate(`document.querySelector('input').value`);
  const changedName = `${originalName} UI verified`;
  await setValue("input", changedName); await clickText("Save and preview");
  await waitFor(`document.body.innerText.includes('Assistant settings saved')`);
  await command("Page.reload", {}, sessionId);
  await waitFor(`document.querySelector('input')?.value===${JSON.stringify(changedName)}`);
  await setValue("input", originalName); await clickText("Save and preview");
  mkdirSync("artifacts", { recursive: true });
  const setupShot = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
  writeFileSync("artifacts/owner-studio-setup.png", Buffer.from(setupShot.data, "base64"));

  await clickText("knowledge"); await waitFor(`document.querySelectorAll('textarea').length>0`);
  const originalFaq = (await evaluate(`document.querySelector('textarea').value`)).replaceAll(" UI-SAVE-CHECK", "");
  const changedFaq = `${originalFaq} UI-SAVE-CHECK`;
  await setValue("textarea", changedFaq); await clickText("Save knowledge");
  await waitFor(`document.body.innerText.includes('FAQ knowledge saved')`);
  await command("Page.reload", {}, sessionId); await waitFor(`!!document.querySelector('input')`); await clickText("knowledge");
  await waitFor(`document.querySelector('textarea')?.value?.includes('UI-SAVE-CHECK')`);
  await setValue("textarea", originalFaq); await clickText("Save knowledge");

  await command("Page.navigate", { url: "http://127.0.0.1:3000/chat/owner-studio" }, sessionId);
  await waitFor(`location.pathname==='/chat/owner-studio' && document.body.innerText.includes('Assistant setup')`);
  await waitFor(`!!document.querySelector('[aria-label="Open support chat"]')`); await evaluate(`document.querySelector('[aria-label="Open support chat"]').click()`);
  await waitFor(`!!document.querySelector('#cxops-chat-input')`); await setValue("#cxops-chat-input", "What are your opening hours?");
  await waitFor(`!document.querySelector('[aria-label="Send message"]')?.disabled`);
  await evaluate(`document.querySelector('[aria-label="Send message"]').click()`);
  await waitFor(`document.querySelector('[aria-label="Support conversation"]')?.innerText.includes('Monday')`, 15000);
  await command("Page.reload", {}, sessionId); await waitFor(`document.body.innerText.includes('Assistant setup')`); await waitFor(`!!document.querySelector('[aria-label="Open support chat"]')`);
  await evaluate(`document.querySelector('[aria-label="Open support chat"]').click()`);
  await waitFor(`document.querySelector('[aria-label="Support conversation"]')?.innerText.includes('Monday')`, 15000);

  await setValue("#cxops-chat-input", "Please book a snowboard repair");
  await waitFor(`!document.querySelector('[aria-label="Send message"]')?.disabled`);
  await evaluate(`document.querySelector('[aria-label="Send message"]').click()`);
  await waitFor(`document.querySelector('[aria-label="Support conversation"]')?.innerText.includes('needs to review')`, 15000);

  await command("Page.navigate", { url: "http://127.0.0.1:3000/chat/owner-studio?view=inbox" }, sessionId);
  await waitFor(`document.body.innerText.includes('Shared inbox') && document.body.innerText.includes('Claim & take over')`);
  await clickText("Claim & take over"); await waitFor(`document.body.innerText.includes('Release')`);

  // Exercise the visitor UI after staff takeover. The message must persist, but
  // no additional automated assistant message may be appended.
  await command("Page.navigate", { url: "http://127.0.0.1:3000/chat/owner-studio" }, sessionId);
  await waitFor(`document.body.innerText.includes('Assistant setup') && !!document.querySelector('[aria-label="Open support chat"]')`); await sleep(500); await evaluate(`document.querySelector('[aria-label="Open support chat"]').click()`);
  await waitFor(`!!document.querySelector('#cxops-chat-input')`);
  const assistantCountAfterTakeover = await evaluate(`document.querySelectorAll('[aria-label="Assistant message"]').length`);
  await setValue("#cxops-chat-input", "I am still here after staff takeover");
  await waitFor(`!document.querySelector('[aria-label="Send message"]')?.disabled`); await evaluate(`document.querySelector('[aria-label="Send message"]').click()`);
  await waitFor(`document.querySelector('[aria-label="Support conversation"]')?.innerText.includes('I am still here after staff takeover')`, 15000);
  await sleep(1500);
  const assistantCountAfterVisitorSend = await evaluate(`document.querySelectorAll('[aria-label="Assistant message"]').length`);
  if (assistantCountAfterVisitorSend !== assistantCountAfterTakeover) throw new Error("assistant replied after staff takeover");

  await command("Page.navigate", { url: "http://127.0.0.1:3000/chat/owner-studio?view=inbox" }, sessionId);
  await waitFor(`document.body.innerText.includes('Shared inbox') && document.body.innerText.includes('Release')`);
  await setValue(`input[placeholder="Reply as staff…"]`, "UI verified staff reply");
  await waitFor(`![...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Send')?.disabled`);
  await clickText("Send"); await waitFor(`document.body.innerText.includes('UI verified staff reply')`);
  await clickText("Release"); await waitFor(`document.body.innerText.includes('Claim & take over')`);
  await clickText("Claim & take over"); await waitFor(`document.body.innerText.includes('Resolve')`);
  const inboxShot = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
  writeFileSync("artifacts/owner-studio-inbox.png", Buffer.from(inboxShot.data, "base64"));
  await clickText("Resolve"); await waitFor(`!document.body.innerText.includes('Release')`);

  console.log(JSON.stringify({ settings_save_reload: true, faq_save_reload: true, visitor_send_poll_restore: true, no_automated_reply_after_takeover: true, handoff_claim_reply_release_resolve: true }));
  chrome.kill();
}

main().catch((error) => { console.error(error); chrome.kill(); process.exitCode = 1; });
