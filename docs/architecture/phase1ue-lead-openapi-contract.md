# Phase 1U-E — Public lead website OpenAPI contract (Issue #27)

**Status:** Draft; no production activation or merge until required CI green.

## Confirmed mismatch

The `LeadSubmission.website` field previously advertised a nullable string
with `maxLength: 255`. Schemathesis generated a schema-compliant string with
no dotted domain; FastAPI's `normalize_website` correctly returned HTTP 422.

The source validator in `app/services/commercial_common.py` currently:

- Accepts a bare dotted hostname or an `http://` / `https://` URL.
- Trims surrounding whitespace. An empty value becomes `None`.
- Assumes HTTPS for bare hostnames; canonicalizes scheme and lowercase host,
  preserving an explicitly valid port.
- Rejects non-HTTP schemes, embedded credentials, missing dots, or whitespace
  in a hostname; invalid ports and excess length also fail validation.
- Discards paths, query strings and fragments when normalizing.

The precise behavior comes from `urllib.parse.urlsplit` and a post-field
validator; even inputs with paths that normalize to a bare host are accepted.
A single `pattern` or `format: uri` would misdescribe existing accepted
requests. Instead the generated OpenAPI field includes a detailed description,
valid examples, and `x-cxops-*` semantic metadata.

Both `LeadSubmission` and `OperatorLeadCreate` share this same validator
and documentation, so public and operator wire descriptions stay aligned.
No Python validation behavior, lead capture flag, schema or route changed.

## New verification

- Deterministic backend tests check both OpenAPI components and all accepted
  examples against actual Pydantic normalization; invalid inputs remain 422.
- The disposable-only Schemathesis job replays explicit positive requests with
  the *real FastAPI parser* and *mocked LeadService.submit*: 202 and expected
  canonical URLs must match. It also replays unsupported website forms and
  asserts 422 with **zero** calls to the fake service.
- The lead-capture feature flag is still OFF in CI and production. The fake
  service never stores leads, sends mail or calls providers. No actual email,
  customer data, model use or new database writes are needed.
- The existing fuzz suite continues to allow controlled documented 422 for
  unexpressible semantic input constraints; this positive example suite makes
  explicitly documented valid inputs a blocking CI acceptance requirement.
- No new infrastructure, runtime package, Alembic migration or hosting charge.

## Remaining limitations

- Arbitrary positive Schemathesis-generated strings are not guaranteed to
  pass custom domain parsing; this is documented rather than hidden.
- Successful lead persistence, consent and rate-limiting are exercised by
  the existing commercial integration suite, not this fake-service contract
  test. A future deeper contract stage may fuzz a dedicated disposable
  feature-enabled lead route with synthetic data and explicit cost limits.
- Only close Issue #27 once the final commit passes all required checks,
  independent review, and the schema is confirmed live after deployment.
