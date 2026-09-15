import hashlib
import hmac

HEX_SIGNATURE_LENGTH = 64


def compute_signature(
    *,
    secret: str,
    body: bytes,
) -> str:
    """HMAC-SHA256 hex digest over the raw request body."""
    return hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    *,
    secret: str,
    body: bytes,
    signature: str,
) -> bool:
    """Constant-time verification of a hex HMAC-SHA256 signature.

    Only 64-char lowercase hex digests are accepted; anything else is
    rejected before comparison. Comparison uses hmac.compare_digest.
    """
    if not signature or len(signature) != HEX_SIGNATURE_LENGTH:
        return False

    try:
        bytes.fromhex(signature)
    except ValueError:
        return False

    expected = compute_signature(
        secret=secret,
        body=body,
    )

    return hmac.compare_digest(
        expected,
        signature.lower(),
    )
