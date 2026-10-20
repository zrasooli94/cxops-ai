# CXOps AI Chatbot — C4 mobile and accessible conversation UX

**21 October 2026 | Draft/source-only | Not deployed | Release HOLD**

This product milestone is stacked on the unmerged chatbot PR #53 and is
independent of Render worker migration. It changes how the **existing**
public-chat widget is presented. It does **not** modify AI decision policy,
tenant isolation, persistence, model settings, action permissions, database
schema or live provider integrations.

## Changes customers will notice

1. **Conversation ownership:** inbound/customer messages now align to the
   right; outbound/support messages to the left. The existing tenant-authored
   theme colors remain unchanged. Every message is labelled by sender and
   rendered with `dir="auto"` for right-to-left Persian/Arabic text, without
   HTML injection or rewriting message bodies.
2. **Accessible keyboard flow:** opening the iframe widget moves keyboard
   focus to the **Minimize chat** button (not the input, so mobile keyboards
   do not automatically open). Escape first closes the visible confirmation
   panel, then minimizes the widget on another press; it does **not** close
   the customer session or contact the backend. Closing the panel returns
   focus to the launcher. The dialog is correctly marked **non-modal**
   (`aria-modal="false"`) because the hosting customer website remains
   interactive.
3. **Respect reading older messages:** automatically follow new messages
   only while already near the bottom of the transcript. Manual scrolling
   up prevents new polling updates from forcibly jumping the reader back.
   **Jump to latest messages** restores the end and is keyboard accessible.
   The chat log has a label for assistive technology and leaves status
   messages on their existing low-frequency polling schedule.
4. **Rotation and mobile keyboard:** the embeddable loader remembers the
   logical panel size and recalculates the actual iframe dimensions when
   the *parent* window resizes, or when `visualViewport` changes.
   The viewport's obscured bottom area moves the iframe above the mobile
   keyboard when the browser exposes reliable measurements. The existing
   per-theme 380/400px panel widths, 56px launcher, 40px margin, 520x640
   upper clamps, transparent background and iframe sandbox are preserved.
5. **Stricter resize message validation:** a matching origin alone is not
   sufficient; the loader now also requires
   `event.source === iframe.contentWindow` before accepting a resize
   request. Another same-origin frame cannot arbitrarily resize the widget.
   This check is made only for the resize message, not for API or staff
   access, which remain under existing server permissions.

## Offline verification

- `conversation-view.test.ts`: executable direction mapping, scroll
  threshold, fractional/small and malformed viewport math.
- `embed-sizing.test.ts`: runs the actual `loader.js` in a Node sandbox;
  simulates parent rotation, mobile `visualViewport` resizing and keyboard
  occlusion, wrong-source messages, exact origin filtering, and existing
  launcher/theme framing invariants.
- `chat-accessibility.test.ts`: React TSX source-level checks for sender
  labelling, accessible focus targets, Escape semantics, user-controlled
  scrolling and plain-text output. These are **not** real DOM/screenreader
  tests, because the current repository lacks a browser automation fixture.
- Full frontend Node test suite, ESLint on changed TypeScript modules,
  Next.js route-type generation, TypeScript `tsc --noEmit`, plain loader
  JavaScript syntax check and Next.js build in an isolated GitHub Actions
  runner. No provider secrets or actual customer sessions.

## Important limits and next validation

These checks cover authored logic and the executable loader sandbox, not
the physical behavior of all Safari/Android keyboards or screen readers.
The mobile visual viewport API differs by browser, OS, zoom and orientation.
A qualified human UI review should open the draft preview on real iPhone
Safari, Android Chrome, desktop keyboard and screen-reader, verify a short
and long conversation, orientation change, focus restoration, real keyboard
overlay and the preserved scroll position. Because the widget embeds on
third-party pages, check both the host and iframe visual viewport.

**Technical debt:** the preexisting chat history merge/polling and window
resize code are not a substitute for full end-to-end browser QA. A new
browser-testing runner could be added in a later source-only milestone
after reviewing dependency size, CI constraints and permitted providers.

**Release boundaries:** no merge or production deployment, no new paid
service, no OpenAI or Zendesk requests, no database or API migrations,
no Render/GitHub worker schedule changes, no enabling Phase 1Y ticket
writes, no credential movement and no repository visibility change.
The synthetic Render Cron remains suspended; live-worker cutover and
real-model faithfulness still require separate approval.
