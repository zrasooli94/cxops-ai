# CXOps AI — C3: safe customer follow-up topic continuity

**21 October 2026. Source-only DRAFT. Production RELEASE HOLD.**

## Existing limitation and product improvement

CXOps already passes up to twelve tenant-bound public messages to the
**agent decision workflow**, but its separate public **RAG answer generator**
received only \`question=text\`. A short follow-up such as
"What about weekends?" would therefore retrieve knowledge without the
previous subject (for example, "When is the workshop open?").

This PR enables a **narrow topic cue** for a customer who has *just received*
an automatic grounded answer. The previous customer question is not a
trusted source, may contain prompt injection, and grants no tool rights.

### Opt-in and safety remain unchanged

- The tenant must still explicitly enable \`grounded_auto_reply_enabled\`.
- The coordinator must classify the *current* question as pure information;
  the persisted agent run must be \`respond\` with **no business tool**.
- No customer text authorizes an action, approval, CRM write, booking, refund,
  new job type, new secret or provider call.
- Unverified citation, low similarity, untrusted retrieval, RAG failure,
  no-knowledge and genuine action requests still follow existing fail-closed
  handoff rules. Human-assigned/requested sessions remain human-only.

### Read-only topic selection

\`app/services/chat_followup_context.py\` implements a pure, deterministic
gate. \`PublicChatService._previous_grounded_question_for_followup\` performs
one *existing* tenant-filtered public conversation-message query **only if**
the current message is a short, referential information question.

Required sequence in the last three visible public messages:

1. Earlier **customer inbound question**, maximum 240 chars, information
   intent, single line, free of obvious secrets or prompt-override text.
2. A **completed CXOps outbound answer** whose \`client_message_id\` matches
   the earlier inbound (not a staff note, wrong-tenant message, missing/queued
   delivery, standard receipt fallback or handoff text).
3. The exact **current inbound message**, from the same trusted tenant and
   conversation, with matching persisted ID and body.

All three must be chronologically valid and no more than **30 minutes**
apart. Only the **previous customer question** is reused, not a generated
answer, staff note, private message, arbitrary old session, previous API
call or another tenant's document. If any invariant is uncertain, the
chatbot performs the original single-question RAG behavior unchanged.

Eligible referential patterns are deliberately narrow (English "What
about...", "How about...", "And on Sundays?", "And weekends?" and a few
Persian weekend equivalents). Standalone questions, commands, long inputs,
line breaks, ambiguous states, expired topics and human handoffs do not
reuse an old topic. This intentionally prioritizes safety over general
anaphora resolution.

### Actual RAG behavior

\`RAGService.answer\` accepts optional
\`previous_customer_question\`, validated again at the RAG boundary:

- Retrieval is performed with \`prior_question + newline + current_question\`
  only for a valid referential current question. The **audit question remains
  the exact current customer text**.
- The generation prompt explicitly separates **PRIOR CUSTOMER QUESTION
  (untrusted topic context only)**, **CURRENT CUSTOMER QUESTION**, and
  **KNOWLEDGE BASE CONTEXT**. The system prompt warns never to cite or trust
  the previous customer question as factual evidence or instruction.
- The same single RAG LLM invocation and existing at-most-one citation repair
  apply; **no extra model invocation is introduced** by context.
- No database migration, new index, model configuration, tenant setting or
  environment variable.

### Safety boundaries and known limitations

1. **This is not unrestricted conversation memory.** It only restores the
   immediately preceding answered question for a subset of elliptical
   follow-ups. It does not solve references to older turns, follow-ups after
   failed/unanswered questions, complex multilingual anaphora or unrelated
   business intents.
2. **Untrusted customer text remains untrusted.** Obvious injection/privacy
   patterns are filtered; a sophisticated instruction could still evade a
   heuristic. RAG source fidelity must come from actual tenant knowledge.
3. **Citation syntax is not factual entailment.** The C2 quality audit
   showed a made-up claim with a syntactically valid \`[S1]\` could pass the
   structural gate. This remains a real product-quality gap. New context
   does not fix it or enable autonomous production.
4. **Retrieval may improve relevance but is not guaranteed to.** Questions
   whose correct source is absent or whose semantic similarity is too low
   still fail closed; context can also degrade retrieval relevance. Actual
   bilingual/model evaluation requires a separate authorized provider
   budget and human-labelled review.
5. **Latency**: an eligible referential follow-up causes one additional,
   tenant-scoped, bounded SQL read. All other questions do not. A true
   cold-start/Render CPU profile has not been performed.

## Offline verification gates

A standalone GitHub Actions PR-only CI uses the **same strict** C2
\`scripts/chatbot_c2_disposable_guard.py\`: exact GitHub Actions
development flag, exact PostgreSQL 16 loopback name/user/password,
empty OpenAI/Zendesk/notifications secrets and no paid model requests.

- Pure context helpers: valid English/Persian variants, stale contexts,
  wrong organization/conversation/provider, internal/human/queued replies,
  mismatched client IDs, malformed/secret-bearing prior messages, restored
  sessions and out-of-order turn records.
- Real \`RAGService.answer\` with fake tenant retrieval/LLM/telemetry:
  checks one model call, correct search query, clean audit question and
  untrusted-prior instruction in generation prompt.
- Real FastAPI public web-chat sessions, full sync and async turn path,
  tenant isolation, grounded-auto-reply gate and idempotent messages against
  the disposable PostgreSQL database. No real provider credentials.
- Prior 70 C2 FAQ/citation tests, full public-chat suite, Rust? **No** —
  Python \`pytest\` only, and pinned \`ruff==0.16.10\` on changed files.

An actual passing CI means **mocked behavioral proof**, not browser/real
provider/model certification. Require a qualified security/product reviewer,
a real-browser pilot in a disposable test tenant and separately authorized
nonzero-cost model evaluation before production activation.

**Keep PRs draft/unmerged.** Do not merge, deploy, unsuspend the existing
inert Render Cron, change the production GitHub worker, turn on Phase 1Y
writes, move secrets, pay for new resources or change repository visibility.
