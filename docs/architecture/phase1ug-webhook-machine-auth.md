# Phase 1U-G — Signed machine webhook OpenAPI contracts

**Status:** draft release. Documentation and offline signature tests only;
there are no changes to runtime authentication, persistence, or integrations.

## Verified live code paths

### Generic ticket events

- Route: `POST /webhooks/ticket-events`.
- FastAPI dependency: `verify_ticket_event_signature` inside `app/api/deps.py`.
- Machine-only `X-CXOps-Signature`: HMAC-SHA256 **hexadecimal** digest of
  UTF-8 bytes of the exact `X-CXOps-Timestamp` header followed immediately by
  the raw HTTP request body. Do **not** hash parsed/normalized JSON.
- Timestamp is required and parsed as ISO-8601 or a positive epoch timestamp.
  `GENERIC_WEBHOOK_REPLAY_WINDOW_SECONDS` bounds past and future timestamps.
- Missing/invalid signature or timestamp returns 401; missing server signing
  key fails closed with 503; malformed signed event JSON returns 422.
- The signed JSON's `event_id` is a persistent event-deduplication key.
  Timestamp validation limits replay in time; this is **not** one-time HMAC.

### Zendesk webhook delivery

- Route: `POST /webhooks/zendesk/tickets/{integration_id}`.
- The integration ID resolves one organization-owned connection and its own
  webhook signing key before processing. Unknown integration ID returns 404.
- `X-Zendesk-Webhook-Signature` is **Base64** HMAC-SHA256 of the original
  `X-Zendesk-Webhook-Signature-Timestamp` value concatenated with the
  original request body bytes. It is not interchangeable with the generic
  webhook's hexadecimal signature.
- The signed timestamp must be within
  `ZENDESK_WEBHOOK_REPLAY_WINDOW_SECONDS` of server time (past or future).
  Missing/invalid signature or key returns 401, missing invocation ID returns
  400, and invalid signed JSON/event structure returns 422.
- `X-Zendesk-Webhook-Invocation-Id` is mandatory and is the durable
  idempotency key, **but is not included in the signed message**. The same
  timestamp/body/signature can be replayed within the valid window using a
  different invocation ID. TLS, connection secrets, timestamps and dedupe
  mitigate this, but DO NOT provide cryptographic one-time replay prevention.
  This gap remains for a separately approved security-hardening milestone.

## OpenAPI documentation implementation

Two explicit API-key-in-header security schemes:
`TicketEventHMAC` and `ZendeskWebhookHMAC`. These point to **signature
headers**, not the server signing secrets. Required timestamp headers and
Zendesk's unsigned invocation ID are recorded as mandatory header parameters
on their own routes. Vendor extensions identify HMAC input bytes, output
encoding, replay-window configuration and idempotency source. No global
security requirement; no implied staff JWT, visitor session token, OAuth
scopes, or permissions.

FastAPI's effective nested route/dependency graph is inspected for the generic
webhook verifier; the exact Zendesk route handler is matched by callable
identity because Zendesk performs its verification within that handler rather
than in a dependency. If a route mixes authentication modes or an existing
documentation definition conflicts, schema generation refuses the conflict.

OAuth login redirects/callbacks remain governed by their existing
signed-state flow, **not** webhook HMAC or a fabricated staff JWT on the
callback. Other machine credentials are not described without confirming a
dedicated, existing runtime verifier.

## Verification and non-goals

- Deterministic tests compare signature bytes, timestamp boundaries,
  absent/wrong secrets, raw-body tampering, output encoding and real
  timestamp-header requirements.
- A real generic verifier is exercised through an in-process ASGI probe.
  The Zendesk route is exercised using mocked connection lookup and enqueue
  service, without database writes or network providers.
- Tests check two distinct generated machine security schemes, exact
  required headers, failure responses, no global auth defaults, route graph
  matches, cached OpenAPI idempotence and no embedded credentials.
- Phase 1U-F staff JWT and visitor token tests remain as strict inventory
  checks across the same route graph.
- No database migration, secrets, paid services, model calls, app runtime
  authorization refactoring, or new package dependencies.

## Remaining risk and future decision

The unsigned Zendesk invocation header can be changed by a holder of a valid
captured request during the accepted replay interval. Consider a separate
reviewed remediation that binds deduplication to signed payload bytes and
timestamp within a bounded window, while retaining Zendesk's legitimate retry
semantics. Avoid changing tenant trust boundaries or assuming a successful
HMAC verify prevents all replay. This PR adds verification and documentation,
not a redesign of webhook delivery or side effects.
