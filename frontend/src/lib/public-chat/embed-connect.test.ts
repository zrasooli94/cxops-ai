import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Phase 1P.6 connect/handoff contract for the public chat widget.
 *
 * `embed.tsx` renders JSX and cannot be imported under `node --test`, so — as
 * with the sizing and security suites — the behaviour is asserted against the
 * source. Each assertion pins an intentional behaviour change:
 *
 *   - the first click opens the panel in `connecting` before any request runs;
 *   - a bounded, GET-only config prefetch starts on mount so a tenant theme
 *     (e.g. RISPU) is resolved before the launcher is ever clicked — it never
 *     creates a session, never blocks the click, and fails safely;
 *   - a fresh session needs no separate config request (the create response
 *     carries the config);
 *   - a single-flight ref makes rapid double-clicks collapse into one session;
 *   - a stored session resumes state and config concurrently; the prefetched
 *     config is reused so no second GET is issued;
 *   - a failed connection offers a friendly retry;
 *   - handed-off sessions keep the composer enabled (human_requested and
 *     human_assigned) and never downgrade human_assigned.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = join(HERE, "..", "..", "..");
const embedSource = readFileSync(
  join(FRONTEND_ROOT, "src", "app", "chat", "embed", "embed.tsx"),
  "utf8",
);

describe("first-click latency", () => {
  it("opens the panel in the connecting state before any request", () => {
    const openStart = embedSource.indexOf("const open = useCallback");
    const openEnd = embedSource.indexOf("widgetKey,\n  ]);", openStart);
    assert.ok(openStart !== -1 && openEnd !== -1);
    const openBlock = embedSource.slice(openStart, openEnd);
    const connectingAt = openBlock.indexOf('setState("connecting")');
    const firstAwait = openBlock.indexOf("await ");
    assert.notEqual(connectingAt, -1, "open must set connecting synchronously");
    assert.notEqual(firstAwait, -1);
    assert.ok(
      connectingAt < firstAwait,
      "the panel must enter connecting before the first network await",
    );
  });
});

describe("single-flight session creation", () => {
  it("guards the launcher against rapid double-clicks", () => {
    assert.ok(
      embedSource.includes(
        "if (connectInflightRef.current) {\n      return;\n    }",
      ),
      "a second click while a connect is in flight must do nothing",
    );
    assert.ok(
      embedSource.includes("connectInflightRef.current = true;"),
      "the connect must be marked in flight once accepted",
    );
    assert.ok(
      embedSource.includes("connectInflightRef.current = false;"),
      "the guard must release in the finally block",
    );
  });

  it("creates the session exactly once in the fresh path", () => {
    assert.equal(
      embedSource.split("createPublicChatSession(").length - 1,
      1, // a fresh connect must create exactly one server session (the import omits the parenthesis)
      "a fresh connect must create exactly one server session",
    );
  });
});

describe("no redundant config request", () => {
  it("does not fetch config ahead of creating a fresh session", () => {
    const freshStart = embedSource.indexOf("if (!token) {");
    const createAt = embedSource.indexOf("const created =");
    assert.ok(freshStart !== -1 && createAt !== -1);
    assert.ok(!embedSource.slice(freshStart, createAt).includes("fetchPublicChatConfig"),
      "the fresh-session branch must not call the config endpoint separately");
  });

  it("takes the config from the create-session response", () => {
    assert.ok(embedSource.includes("setConfig(created.config)"));
  });

  it("resumes a stored session without a sequential config wait", () => {
    const statePromise =
      embedSource.indexOf("const statePromise = loadState(storedToken);");
    assert.notEqual(statePromise, -1);
    const allSettled = embedSource.indexOf(
      "await Promise.allSettled([",
      statePromise,
    );
    assert.notEqual(allSettled, -1);
    const shared = embedSource.slice(statePromise, allSettled);
    assert.ok(
      shared.includes(
        "config\n          ? Promise.resolve(config)\n          : getPublicConfig()",
      ),
      "the resume must reuse the prefetched/authored config instead of a second GET",
    );
    const settled = embedSource.slice(allSettled, allSettled + 240);
    assert.ok(settled.includes("statePromise"));
    assert.ok(settled.includes("configPromise"));
  });

  it("discards an unusable stored session", () => {
    assert.ok(embedSource.includes("discardSession();"));
  });
});

describe("config prefetch before any click", () => {
  it("prefetches public config when the widget mounts", () => {
    const prefetchStart = embedSource.indexOf(
      "// Best-effort theme prefetch",
    );
    const prefetchEnd = embedSource.indexOf(
      "}, [getPublicConfig]);",
      prefetchStart,
    );
    assert.ok(prefetchStart !== -1 && prefetchEnd !== -1);
    const prefetchBlock = embedSource.slice(
      prefetchStart,
      prefetchEnd,
    );
    assert.ok(
      prefetchBlock.includes("getPublicConfig()"),
      "the prefetch must issue the bounded config GET on mount",
    );
    assert.ok(
      prefetchBlock.includes(
        "setConfig((previous) => previous ?? resolved)",
      ),
      "a resolved prefetch must populate config without clobbering a newer value",
    );
  });

  it("prefetches config without creating a session", () => {
    const prefetchStart = embedSource.indexOf(
      "// Best-effort theme prefetch",
    );
    const prefetchEnd = embedSource.indexOf(
      "}, [getPublicConfig]);",
      prefetchStart,
    );
    assert.ok(prefetchStart !== -1 && prefetchEnd !== -1);
    const prefetchBlock = embedSource.slice(
      prefetchStart,
      prefetchEnd,
    );
    assert.ok(
      !prefetchBlock.includes("createPublicChatSession"),
      "the mount prefetch must never create a session",
    );
  });

  it("creates a session only after the launcher is clicked", () => {
    const openStart = embedSource.indexOf("const open = useCallback");
    const openEnd = embedSource.indexOf("widgetKey,\n  ]);", openStart);
    assert.ok(openStart !== -1 && openEnd !== -1);
    const createAt = embedSource.indexOf("createPublicChatSession(");
    assert.ok(
      createAt > openStart && createAt < openEnd,
      "session creation must live exclusively inside the click-driven open()",
    );
    assert.equal(
      embedSource.split("createPublicChatSession(").length - 1,
      1,
      "exactly one session-creation call site",
    );
  });

  it("lets a RISPU launcher resolve the RISPU theme before the click", () => {
    assert.ok(
      embedSource.includes("const theme = resolveTheme(config?.theme_token);"),
      "the theme must resolve from config, which the mount prefetch populates",
    );
    const launcher = embedSource.indexOf('if (state === "launcher") {');
    assert.notEqual(launcher, -1);
    assert.ok(
      embedSource
        .slice(launcher, launcher + 500)
        .includes("<LauncherButton\n          theme={theme}"),
      "the pre-click launcher must render with the resolved theme",
    );
  });

  it("prefetch failure is safe: default theme, no error state, no block", () => {
    const prefetchStart = embedSource.indexOf(
      "// Best-effort theme prefetch",
    );
    const prefetchEnd = embedSource.indexOf(
      "}, [getPublicConfig]);",
      prefetchStart,
    );
    assert.ok(prefetchStart !== -1 && prefetchEnd !== -1);
    const prefetchBlock = embedSource.slice(
      prefetchStart,
      prefetchEnd,
    );
    assert.ok(
      prefetchBlock.includes(".catch(() => {"),
      "the prefetch must swallow failures",
    );
    assert.ok(
      !prefetchBlock.includes("setErrorMessage"),
      "a failed prefetch must not surface an error banner",
    );
    assert.ok(
      !prefetchBlock.includes('setState("error"'),
      "a failed prefetch must not halt the widget",
    );
    assert.ok(
      embedSource.includes(
        "open() can still create a session\n    // normally and receive authoritative config",
      ),
      "open() must proceed to create a session when the prefetch failed",
    );
  });

  it("shares one public-config GET between prefetch and resume", () => {
    assert.equal(
      embedSource.split("fetchPublicChatConfig(widgetKey)").length - 1,
      1,
      "all callers must go through the shared getPublicConfig promise",
    );
  });
});

describe("handed-off composer", () => {
  it("keeps the composer enabled for human states", () => {
    assert.ok(
      embedSource.includes(
        'state === "ai_active" ||\n    state === "human_requested" ||\n    state === "human_assigned"',
      ),
    );
    assert.ok(
      embedSource.includes("disabled={!composerEnabled || sending}"),
    );
  });

  it("never lets the server downgrade human_assigned", () => {
    const sendBlock = embedSource.slice(
      embedSource.indexOf('if (result.status === "human_assigned")'),
      embedSource.indexOf("finally {", embedSource.indexOf("async (text: string)")),
    );
    assert.ok(
      sendBlock.includes('setState("human_assigned")'),
      "human_assigned must be applied before the handoff fallback",
    );
    assert.ok(
      sendBlock.includes('result.status === "human_requested"'),
    );
  });
});

describe("failed-connection retry", () => {
  it("offers a friendly retry that reopens the panel", () => {
    assert.ok(embedSource.includes("Try again"));
    assert.ok(
      embedSource.includes('onClick={() => void open()}'),
      "the retry control must reuse the single-flight open flow",
    );
    assert.ok(embedSource.includes('state === "error"'));
  });
});