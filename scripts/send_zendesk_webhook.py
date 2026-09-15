import argparse
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx

from app.core.config import settings

parser = argparse.ArgumentParser(
    description="Send a signed Zendesk webhook to a per-organization integration."
)
parser.add_argument(
    "--integration-id",
    default="",
    help="Integration id encoded in the webhook path (the connection's integration_id).",
)
parser.add_argument(
    "--secret",
    default="",
    help="Organization webhook signing secret. Defaults to ZENDESK_WEBHOOK_SECRET env.",
)
parser.add_argument(
    "--url",
    default="http://127.0.0.1:8000",
    help="CXOps base URL.",
)

args = parser.parse_args()

if not args.integration_id:
    raise SystemExit("--integration-id is required")

secret = args.secret or settings.zendesk_webhook_secret

if not secret:
    raise SystemExit("--secret (or ZENDESK_WEBHOOK_SECRET) is required")

url = f"{args.url.rstrip('/')}/webhooks/zendesk/tickets/{args.integration_id}"

payload = {
    "type": "zen:event-type:ticket.updated",
    "detail": {
        "id": 6,
    },
    "channel": "email",
}


body = json.dumps(
    payload,
    separators=(",", ":"),
).encode("utf-8")


timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


message = timestamp.encode("utf-8") + body


digest = hmac.new(
    secret.encode("utf-8"),
    message,
    hashlib.sha256,
).digest()


signature = base64.b64encode(digest).decode("utf-8")


headers = {
    "Content-Type": "application/json",
    "X-Zendesk-Webhook-Signature": signature,
    "X-Zendesk-Webhook-Signature-Timestamp": timestamp,
    "X-Zendesk-Webhook-Invocation-Id": "zd_invocation_003",
}


response = httpx.post(
    url,
    content=body,
    headers=headers,
)


print("Status:", response.status_code)
print("Response:", response.json())
