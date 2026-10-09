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
 *
 * Phase 1P.6.1 additions:
 *   - the launcher is a UI-only `panelOpen` flag, not a session state;
 *   - the header X minimizes — it only hides the panel and mutates nothing;
 *   - reopening a minimized session shows the same conversation via a
 *     no-session reopen branch in open();
 *   - ending a conversation is explicit and confirmed, and alone reaches the
 *     close endpoint and strips the stored token;
 *   - an explicit End also fails safely (1P.6.1 correction): the close
 *     endpoint is awaited first and all local cleanup happens only after the
 *     backend confirms; on failure nothing is discarded, polling continues,
 *     and a bounded friendly error allows a retry.
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
    const launcher = embedSource.indexOf(
      'if (!panelOpen || state === "closed") {',
    );
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

describe("minimize vs end conversation (Phase 1P.6.1)", () => {
  it("treats panel visibility as UI-only, separate from session state", () => {
    assert.ok(
      embedSource.includes('useState<WidgetState>("closed")'),
      "the initial session state must be closed, not launcher",
    );
    assert.ok(
      embedSource.includes(
        "const [panelOpen, setPanelOpen] =\n    useState(false);",
      ),
      "a UI-only panelOpen flag must gate visibility",
    );
    assert.ok(
      !embedSource.includes('"launcher"'),
      "launcher must no longer be a session state",
    );
    assert.ok(
      embedSource.includes(
        'if (!panelOpen || state === "closed") {',
      ),
      "the launcher renders exactly when the panel is closed",
    );
    assert.ok(
      embedSource.includes(
        'const isPanel =\n      panelOpen && state !== "closed";',
      ),
      "the resize must follow the UI-only visibility flag",
    );
    assert.ok(
      embedSource.includes("}, [panelOpen, state, theme]);"),
      "the resize effect must watch panelOpen",
    );
  });

  it("minimizes without a backend call or any session mutation", () => {
    const minimizeStart = embedSource.indexOf(
      "const minimize = useCallback",
    );
    const minimizeEnd = embedSource.indexOf(
      "}, []);",
      minimizeStart,
    );
    assert.ok(minimizeStart !== -1 && minimizeEnd !== -1);
    const minimizeBlock = embedSource.slice(
      minimizeStart,
      minimizeEnd,
    );
    assert.ok(
      minimizeBlock.includes("setPanelOpen(false);"),
      "minimize must hide the panel",
    );
    for (const forbidden of [
      "closePublicChatSession",
      "discardSession",
      "setSessionToken",
      "setMessages",
      "removeItem",
      'setState("closed")',
      "clearPoll",
    ]) {
      assert.ok(
        !minimizeBlock.includes(forbidden),
        `minimize must not ${forbidden}`,
      );
    }
  });

  it("binds the header X to minimize", () => {
    assert.ok(
      embedSource.includes(
        'onClick={minimize}\n          aria-label="Minimize chat"',
      ),
      "the header close button must minimize, never end the conversation",
    );
    assert.ok(
      !embedSource.includes('aria-label="Close chat"'),
      "no control may present ending as a mere close",
    );
  });

  it("reopens an in-memory session without a new session", () => {
    const openStart = embedSource.indexOf("const open = useCallback");
    const openEnd = embedSource.indexOf("widgetKey,\n  ]);", openStart);
    const openBlock = embedSource.slice(openStart, openEnd);
    const reopenAt = openBlock.indexOf("if (sessionTokenRef.current) {");
    assert.notEqual(reopenAt, -1, "open must check for an in-memory session");
    const reopenSlice = openBlock.slice(reopenAt, reopenAt + 120);
    assert.ok(
      reopenSlice.includes("setPanelOpen(true);"),
      "the reopen branch must show the panel and return",
    );
    assert.ok(
      !reopenSlice.includes("createPublicChatSession"),
      "the reopen branch must not create a session",
    );
    assert.ok(
      reopenAt < openBlock.indexOf("const created ="),
      "the reopen branch must precede the fresh-session create path",
    );
  });

  it("ends a conversation only through an explicit, confirmed action", () => {
    const endStart = embedSource.indexOf(
      "const endConversation = useCallback",
    );
    const endEnd = embedSource.indexOf(
      "}, [clearPoll, discardSession]);",
      endStart,
    );
    assert.ok(endStart !== -1 && endEnd !== -1);
    const endBlock = embedSource.slice(endStart, endEnd);
    assert.ok(endBlock.includes("await closePublicChatSession(token);"));
    assert.ok(endBlock.includes("discardSession();"));
    assert.ok(endBlock.includes("setSessionToken(null);"));
    assert.ok(endBlock.includes("setMessages([]);"));
    assert.ok(endBlock.includes('setState("closed");'));
    assert.ok(
      embedSource.includes(
        "End this conversation? You will start a\n                new chat next time.",
      ),
      "the footer must gate an end behind an inline confirmation",
    );
    assert.ok(
      embedSource.includes("onClick={() => setEndConfirm(true)}"),
      "the footer end action must ask for confirmation first",
    );
  });
});

describe("end-conversation failure safety (Phase 1P.6.1 correction)", () => {
  const endStart = embedSource.indexOf(
    "const endConversation = useCallback",
  );
  const endEnd = embedSource.indexOf(
    "}, [clearPoll, discardSession]);",
    endStart,
  );
  const endBlock = embedSource.slice(endStart, endEnd);
  const catchStart = endBlock.indexOf("} catch {");
  const catchEnd = endBlock.indexOf("return;\n    }", catchStart);
  const catchBlock = endBlock.slice(catchStart, catchEnd);
  const confirmMarker = "// End confirmed by the backend";
  const successBlock = endBlock.slice(
    endBlock.indexOf(confirmMarker),
    endBlock.length,
  );
  const cleanupCalls = [
    "clearPoll();",
    "discardSession();",
    "setSessionToken(null);",
    "setMessages([]);",
    "setPanelOpen(false);",
    "setEndConfirm(false);",
    'setState("closed");',
  ];

  it("fails safely: preserves the token on a failed End", () => {
    assert.ok(
      endBlock.includes("const token = sessionTokenRef.current;"),
      "End must read the in-memory token before deciding",
    );
    assert.ok(!catchBlock.includes("setSessionToken"));
    assert.ok(!catchBlock.includes("removeItem"));
  });

  it("fails safely: does not discard the stored session on a failed End", () => {
    assert.ok(!catchBlock.includes("discardSession"));
  });

  it("fails safely: preserves messages on a failed End", () => {
    assert.ok(!catchBlock.includes("setMessages"));
  });

  it("fails safely: preserves panel and session state on a failed End", () => {
    assert.ok(!catchBlock.includes("setState"));
    assert.ok(!catchBlock.includes("setPanelOpen"));
    assert.ok(!catchBlock.includes('setState("closed")'));
    assert.ok(
      catchBlock.includes(
        '"Unable to end this conversation right now. Please try again."',
      ),
      "a failed End must surface a bounded, friendly error",
    );
    assert.ok(
      catchBlock.includes("setEndConfirm(false);"),
      "failure must reset the confirm so the user can retry",
    );
  });

  it("fails safely: does not stop polling on a failed End", () => {
    assert.ok(!catchBlock.includes("clearPoll"));
  });

  it("successful End still performs full cleanup, only after the backend close", () => {
    const awaitIndex = endBlock.indexOf(
      "await closePublicChatSession(token);",
    );
    const confirmIndex = endBlock.indexOf(confirmMarker);
    assert.ok(awaitIndex !== -1 && confirmIndex !== -1);
    assert.ok(
      awaitIndex < confirmIndex,
      "local cleanup must run only after the confirmed server close",
    );
    for (const call of cleanupCalls) {
      assert.ok(
        successBlock.includes(call),
        `a successful End must ${call}`,
      );
    }
  });
});