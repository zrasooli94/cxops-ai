# OpenAI Support case 18041142 — owner-provided screenshot transcription

## Provenance

- Case: `18041142`
- Subject shown: `New Case`
- Signed: `OpenAI AI Support Agent`
- Guidance effective date stated in the response: 2026-10-23
- Owner-reported receipt: 2026-10-24 01:28 Malaysia time (UTC+08:00)
- Evidence received in this workspace: owner-provided transcription on 2026-10-24
- Evidence class: transcription from an owner-reported screenshot, not an
  original email export, public developer documentation, or a confirmed human
  specialist response
- Original screenshot independently inspected on 2026-10-24 from the owner's
  native ChatGPT Library attachment. The private byte-identical copy is not
  stored in this public repository.
- Screenshot size: `433137` bytes
- Screenshot SHA-256:
  `ec6aeba1c797adeab058174b17ef76dcb4f01281e0d98ec0f6958561abf624bf`
- Visual inspection confirmed the subject `New Case`, case `18041142`, date
  `Oct 24, 2026 at 1:28 AM`, display name `Support`, signature `OpenAI AI
  Support Agent`, and the transcribed body below.
- Authentication boundary: the screenshot does not expose full sender headers,
  DKIM, SPF, or an original `.eml`; visual review therefore verifies the
  preserved screenshot bytes and visible content, not sender-domain
  authentication or a human-specialist identity.

## Verbatim owner-provided transcription

> As of October 23, 2026, POST /v1/responses/input_tokens is free. There is no per-request, per-token or model-dependent fee for the counting call. It does not consume paid generation budget or normal model inference quota.
>
> Failed or rejected counting calls also incur no token-counting fee. There is therefore no token-counting charge to reconcile in the billing dashboard. A separate request that generates a response is billed separately.
>
> Counting returns the input size, not an output estimate or guaranteed final bill. For your evaluation, generation and any other billable services must still fit within the total budget. A free counting call does not guarantee that the subsequent 50-turn run will stay below the ceiling.
>
> The public counting guide describes what the count includes:
> developers.openai.com/api/docs/guides/token-counting
>
> Applicable generation and tool prices are listed here: developers.openai.com/api/docs/pricing
>
> If this doesn't help with your issue, please respond to this message.
>
> Best,
> OpenAI AI Support Agent

## Use in the evaluation gate

The inspected screenshot supports a `no_charge` count-policy claim, including
failed and rejected count requests, attributed specifically to an OpenAI AI
Support Agent. It does not establish generation cost, taxes, regional
applicability, account entitlement, sender-header authenticity, or a guaranteed
final invoice. The runtime gate does not require a human-specialist title, but
it does require an authenticated `evaluation.manage` principal to review and
attest an expiring evidence identity in the exact activation manifest. This
document and private screenshot do not self-attest or activate that gate.
