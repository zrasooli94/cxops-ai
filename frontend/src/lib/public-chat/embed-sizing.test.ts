import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";

/**
 * Sizing contract for the public chat embed (Phase 1P.1).
 *
 * The widget runs inside an iframe that the loader creates at 96x96. Inside
 * the iframe, `window.innerWidth`/`window.innerHeight` report the iframe's own
 * viewport, not the parent page's, so the embed sends its desired size as fixed
 * logical constants (56x56 launcher, theme-width x 540 panel) and the parent
 * loader is the only code that applies the margin and viewport clamps. The
 * panel width is authored per theme (380 for `default`, 400 for `rispu`), never
 * a shared constant, so default tenants keep their original dimensions.
 *
 * `embed.tsx` is asserted as source (it renders JSX and could not be imported
 * under `node --test`); `loader.js` is executed in a sandboxed global so the
 * message-handling, origin check, and clamping behaviour are proven live.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = join(HERE, "..", "..", "..");

const embedSource = readFileSync(
  join(FRONTEND_ROOT, "src", "app", "chat", "embed", "embed.tsx"),
  "utf8",
);
const loaderSource = readFileSync(
  join(FRONTEND_ROOT, "public", "embed", "loader.js"),
  "utf8",
);

const LOADER_ORIGIN = "https://app.cxops.example";
const WIDGET_KEY = "k".repeat(32);

interface LoaderStub {
  iframe: {
    style: Record<string, string>;
    attributes: Record<string, string>;
  };
  handler: ((event: { origin: string; data: unknown }) => void) | null;
  appended: boolean;
}

function bootLoader(viewport: {
  innerWidth: number;
  innerHeight: number;
}): LoaderStub {
  const stub: LoaderStub = {
    iframe: { style: {}, attributes: {} },
    handler: null,
    appended: false,
  };

  const documentStub = {
    currentScript: {
      src: `${LOADER_ORIGIN}/embed/loader.js`,
      getAttribute: (name: string) =>
        name === "data-widget-key" ? WIDGET_KEY : null,
    },
    createElement: () => ({
      setAttribute: (name: string, value: string) => {
        stub.iframe.attributes[name] = value;
      },
      style: stub.iframe.style,
    }),
    body: {
      appendChild: () => {
        stub.appended = true;
      },
    },
  };

  const windowStub = {
    location: { href: "https://rispu.com/contact" },
    innerWidth: viewport.innerWidth,
    innerHeight: viewport.innerHeight,
    addEventListener: (type: string, handler: (event: { origin: string; data: unknown }) => void) => {
      if (type === "message") {
        stub.handler = handler;
      }
    },
  };

  runInNewContext(loaderSource, {
    document: documentStub,
    window: windowStub,
    URL,
    Number,
    isFinite,
    encodeURIComponent,
    Math,
  });

  assert.equal(stub.appended, true, "loader must append the iframe");
  assert.ok(stub.handler, "loader must subscribe to parent-page messages");
  return stub;
}

function resize(
  stub: LoaderStub,
  origin: string,
  data: unknown,
): void {
  stub.handler?.({ origin, data });
}

describe("embed size requests (embed.tsx)", () => {
  it("requests 56x56 from the launcher and closed states", () => {
    assert.ok(embedSource.includes("const isPanel"));
    assert.ok(embedSource.includes("const width = isPanel ? theme.panel.width : 56;"));
    assert.ok(embedSource.includes("const height = isPanel ? 540 : 56;"));
  });

  it("drives the panel width from the authored theme, not a shared constant", () => {
    // Regression pin (Phase 1P.6): the earlier change bumped the open-panel
    // width globally (380 -> 400). That widened default tenants too. The width
    // must come from theme.panel.width (380 default / 400 rispu) so each theme
    // keeps its own dimensions. The panel CSS must use the same value, and the
    // size must never be derived from the iframe's own (96x96) viewport.
    assert.ok(embedSource.includes("const width = isPanel ? theme.panel.width : 56;"));
    assert.ok(
      !embedSource.includes("const width = isPanel ? 400 : 56;"),
      "a single hardcoded 400 must not widen default tenants",
    );
    assert.ok(
      embedSource.includes(
        'width: `min(${theme.panel.width}px, calc(100vw - 24px))`',
      ),
      "the rendered panel must use the authored theme width",
    );
    assert.ok(!embedSource.includes("window.innerWidth"));
    assert.ok(!embedSource.includes("window.innerHeight"));
  });

  it("requests the panel size while still connecting", () => {
    // Phase 1P.6: the first click expands the panel before any request has
    // completed, so the connecting state must already post the panel size.
    assert.ok(
      embedSource.includes(
        'state !== "launcher" && state !== "closed"',
      ),
      "the resize effect must treat connecting as panel state",
    );
  });

  it("no regression to chat composer rendering", () => {
    assert.ok(embedSource.includes('id="cxops-chat-input"'));
    assert.ok(embedSource.includes('aria-label="Send message"'));
    assert.ok(embedSource.includes("Type your message…"));
    assert.ok(embedSource.includes("cxops-embed:resize"));
  });
});

describe("authored widget chrome (open animation + placeholder)", () => {
  it("runs a subtle ~200ms panel entrance animation", () => {
    assert.ok(
      embedSource.includes("@keyframes cxops-chat-panel-in"),
      "the entrance must be an authored, closed keyframe set",
    );
    assert.ok(
      embedSource.includes("from {\n    opacity: 0;\n    transform: translateY(10px) scale(0.985);\n  }"),
      "entrance must be a slight rise/fade/scale — no flashy motion",
    );
    assert.ok(
      embedSource.includes("animation: cxops-chat-panel-in 200ms ease-out;"),
      "the animation must be around 200ms",
    );
    assert.ok(
      embedSource.includes('className="cxops-chat-panel"'),
      "the panel must carry the authored animation class",
    );
    assert.ok(embedSource.includes(".cxops-chat-panel {"));
  });

  it("disables the entrance animation under prefers-reduced-motion", () => {
    const reducedBlock = embedSource.slice(
      embedSource.indexOf("@media (prefers-reduced-motion: reduce)"),
      embedSource.length,
    );
    assert.ok(reducedBlock.includes(".cxops-chat-spinner,"));
    assert.ok(reducedBlock.includes(".cxops-chat-panel { animation: none; }"));
  });

  it("keeps the animation authored and tenant-independent", () => {
    const rulesStart = embedSource.indexOf("const EMBED_STYLE_RULES");
    const rulesEnd = embedSource.indexOf("`;", rulesStart);
    assert.ok(rulesStart !== -1 && rulesEnd !== -1);
    const rules = embedSource.slice(rulesStart, rulesEnd);
    assert.ok(
      !rules.includes("theme."),
      "the style rules must be a module constant and never interpolate theme values",
    );
    const sectionOpen = embedSource.indexOf('className="cxops-chat-panel"');
    assert.notEqual(sectionOpen, -1);
    assert.ok(
      !embedSource.slice(sectionOpen, sectionOpen + 800).includes("animation"),
      "the panel must not animate through inline styles — only the authored class",
    );
  });

  it("applies the authored composer placeholder color", () => {
    assert.ok(
      embedSource.includes(".cxops-chat-input::placeholder"),
      "the placeholder rule must be authored CSS, not inline on the DOM node",
    );
    assert.ok(
      embedSource.includes("color: var(--cxops-chat-placeholder, #64748b);"),
      "the rule must read the closed CSS variable with a default fallback",
    );
    assert.ok(
      embedSource.includes('"--cxops-chat-placeholder":\n            theme.composer.placeholder'),
      "the panel must feed the authored palette value into the variable",
    );
    assert.ok(
      embedSource.includes('className="cxops-chat-input"'),
      "the input must carry the authored class so ::placeholder applies",
    );
  });
});

describe("loader.js iframe sizing", () => {
  it("expands a default-width 380x540 open-panel request to the margin-adjusted size", () => {
    const stub = bootLoader({ innerWidth: 1400, innerHeight: 1000 });
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:resize",
      width: 380,
      height: 540,
    });
    assert.equal(stub.iframe.style.width, "420px");
    assert.equal(stub.iframe.style.height, "580px");
  });

  it("expands a RISP U-width 400x540 open-panel request to the margin-adjusted size", () => {
    const stub = bootLoader({ innerWidth: 1400, innerHeight: 1000 });
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:resize",
      width: 400,
      height: 540,
    });
    assert.equal(stub.iframe.style.width, "440px");
    assert.equal(stub.iframe.style.height, "580px");
  });

  it("lets a 96x96 iframe expand to the full open panel", () => {
    const stub = bootLoader({ innerWidth: 1400, innerHeight: 1000 });
    assert.equal(stub.iframe.style.width, "96px");
    assert.equal(stub.iframe.style.height, "96px");
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:resize",
      width: 400,
      height: 540,
    });
    assert.equal(stub.iframe.style.width, "440px");
    assert.equal(stub.iframe.style.height, "580px");
  });

  it("clamps the expanded panel to a narrow parent viewport", () => {
    const stub = bootLoader({ innerWidth: 300, innerHeight: 480 });
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:resize",
      width: 400,
      height: 540,
    });
    assert.equal(stub.iframe.style.width, "300px");
    assert.equal(stub.iframe.style.height, "480px");
  });

  it("clamps the expanded panel height on a short viewport", () => {
    const stub = bootLoader({ innerWidth: 1200, innerHeight: 400 });
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:resize",
      width: 400,
      height: 540,
    });
    assert.equal(stub.iframe.style.width, "440px");
    assert.equal(stub.iframe.style.height, "400px");
  });

  it("keeps the closed launcher compact on desktop", () => {
    const stub = bootLoader({ innerWidth: 1400, innerHeight: 1000 });
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:resize",
      width: 56,
      height: 56,
    });
    assert.equal(stub.iframe.style.width, "96px");
    assert.equal(stub.iframe.style.height, "96px");
  });

  it("keeps the launcher from exceeding a sub-launcher viewport", () => {
    const stub = bootLoader({ innerWidth: 70, innerHeight: 70 });
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:resize",
      width: 56,
      height: 56,
    });
    assert.equal(stub.iframe.style.width, "70px");
    assert.equal(stub.iframe.style.height, "70px");
  });

  it("rejects resize messages from the wrong origin", () => {
    const stub = bootLoader({ innerWidth: 1400, innerHeight: 1000 });
    resize(stub, "https://evil.example", {
      type: "cxops-embed:resize",
      width: 9999,
      height: 9999,
    });
    assert.equal(stub.iframe.style.width, "96px");
    assert.equal(stub.iframe.style.height, "96px");
  });

  it("rejects resize messages with the wrong message type", () => {
    const stub = bootLoader({ innerWidth: 1400, innerHeight: 1000 });
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:other",
      width: 400,
      height: 540,
    });
    assert.equal(stub.iframe.style.width, "96px");
    assert.equal(stub.iframe.style.height, "96px");
  });

  it("rejects non-finite dimensions", () => {
    const stub = bootLoader({ innerWidth: 1400, innerHeight: 1000 });
    resize(stub, LOADER_ORIGIN, {
      type: "cxops-embed:resize",
      width: "not-a-number",
      height: Infinity,
    });
    assert.equal(stub.iframe.style.width, "96px");
    assert.equal(stub.iframe.style.height, "96px");
  });

  it("frames the embed from the loader's own origin under a sandbox", () => {
    const stub = bootLoader({ innerWidth: 1400, innerHeight: 1000 });
    assert.equal(
      stub.iframe.attributes["sandbox"],
      "allow-scripts allow-forms allow-same-origin",
    );
    assert.ok(
      stub.iframe.attributes["src"].startsWith(
        `${LOADER_ORIGIN}/chat/embed?key=${WIDGET_KEY}`,
      ),
    );
    assert.equal(stub.iframe.style.position, "fixed");
    assert.equal(stub.iframe.style.right, "0");
    assert.equal(stub.iframe.style.bottom, "0");
    assert.equal(stub.iframe.style.zIndex, "9999");
  });
});