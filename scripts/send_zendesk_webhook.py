import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx

from app.core.config import settings


url = "http://127.0.0.1:8000/webhooks/zendesk/tickets"

payload = {
    "event_type": "ticket.created",
    "ticket_id": 6,
    "channel": "email",
}


body = json.dumps(
    payload,
    separators=(",", ":"),
).encode("utf-8")


timestamp = (
    datetime.now(timezone.utc)
    .isoformat()
    .replace("+00:00", "Z")
)


message = (
    timestamp.encode("utf-8")
    + body
)


digest = hmac.new(
    settings.zendesk_webhook_secret.encode("utf-8"),
    message,
    hashlib.sha256,
).digest()


signature = base64.b64encode(
    digest
).decode("utf-8")


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