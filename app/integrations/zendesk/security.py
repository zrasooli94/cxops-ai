import base64
import hashlib
import hmac


def verify_zendesk_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:

    message = timestamp.encode("utf-8") + body

    digest = hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()

    expected_signature = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(
        expected_signature,
        signature,
    )
