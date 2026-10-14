"""Strict email/Telegram-only transport. No Apprise config files or URL fan-out.

The URL is a trusted server secret, never customer/job-supplied. Apprise 2's
async_notify uses worker threads for blocking requests; cancelling an asyncio
task does NOT necessarily cancel an outbound send. We therefore rely on socket
cto/rto as well as globally bounded concurrency, not wait_for cancellation.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

EMAIL_SMTP_HOSTS = frozenset({
    "smtp.gmail.com",
    "smtp-relay.gmail.com",
    "smtp.office365.com",
    "smtp.sendgrid.net",
    "smtp.mailgun.org",
    "smtp.resend.com",
    "email-smtp.ap-southeast-1.amazonaws.com",
    "email-smtp.us-east-1.amazonaws.com",
})
EMAIL_PATTERN = re.compile(r"^[^@\s/?#]+@[^@\s/?#]+\.[^@\s/?#]+$")
TELEGRAM_PATTERN = re.compile(r"^tgram://[0-9]{5,15}:[A-Za-z0-9_-]{20,120}/-?[0-9]{1,20}$")
CHANNELS = ("email", "telegram")
CONNECT_TIMEOUT = "2"
READ_TIMEOUT = "4"


class NotificationDestinationError(ValueError):
    pass


class NotificationTransportError(RuntimeError):
    pass


def restricted_url(channel: str, configured: str) -> str:
    """Only exact known schemes, hosts, recipient shapes and parameters pass.

    Credential-bearing URLs are never formatted into an exception or log.
    The only destination source is the server's environment secret store.
    """
    if (
        not isinstance(configured, str)
        or not 1 <= len(configured) <= 2048
        or any(ord(char) <= 32 or ord(char) == 127 for char in configured)
    ):
        raise NotificationDestinationError("unsafe notification configuration")
    parts = urlsplit(configured)
    if parts.fragment:
        raise NotificationDestinationError("fragments are not permitted")
    params = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    if len(params) != len({k for k, _ in params}):
        raise NotificationDestinationError("duplicate options are forbidden")
    options = dict(params)
    if channel == "telegram":
        if parts.scheme != "tgram":
            raise NotificationDestinationError("Telegram scheme is not permitted")
        if TELEGRAM_PATTERN.fullmatch(urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))) is None:
            raise NotificationDestinationError("Telegram bot and recipient must be numeric-token forms")
        if set(options) - {"cto", "rto"}:
            raise NotificationDestinationError("unsupported Telegram option")
    elif channel == "email":
        if parts.scheme != "mailtos" or not parts.username or not parts.password:
            raise NotificationDestinationError("TLS-authenticated email URL required")
        if parts.path not in ("", "/") or parts.hostname is None or "@" in parts.path:
            raise NotificationDestinationError("unsupported email URL path")
        if parts.port not in (None, 465, 587, 2525):
            raise NotificationDestinationError("SMTP port not permitted")
        if set(options) - {"smtp", "to", "from", "cto", "rto"}:
            raise NotificationDestinationError("unsupported email options")
        smtp = options.get("smtp", parts.hostname).lower()
        if smtp not in EMAIL_SMTP_HOSTS:
            raise NotificationDestinationError("SMTP endpoint not allowlisted")
        for host in (parts.hostname, smtp):
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise NotificationDestinationError("IP literals prohibited")
        recipient = options.get("to", "")
        if not EMAIL_PATTERN.fullmatch(recipient):
            raise NotificationDestinationError("one email recipient required")
        if options.get("from") and not EMAIL_PATTERN.fullmatch(options["from"]):
            raise NotificationDestinationError("invalid sender address")
    else:
        raise NotificationDestinationError("unsupported channel")
    # Incoming parameters are never allowed to enlarge the socket deadlines.
    options.update(cto=CONNECT_TIMEOUT, rto=READ_TIMEOUT)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(options), ""))


async def send_staff_handoff(*, channel: str, configured: str, title: str, body: str) -> None:
    """Attempt one approved channel; accepted != read/delivered to the device."""
    url = restricted_url(channel, configured)
    if not title.startswith("CXOps AI") or not body.startswith("CXOps AI"):
        raise NotificationDestinationError("template rejected")
    # Lazy import keeps Apprise's plugin system off the API's import path.
    from apprise import Apprise

    sender = Apprise()
    try:
        if not sender.add(url):
            raise NotificationDestinationError("Apprise rejected configured service")
        result = await sender.async_notify(
            title=title,
            body=body,
            # Socket deadlines are in the validated URL. Deliberately no
            # asyncio.wait_for: it cannot cancel an already-running send.
        )
    except NotificationDestinationError:
        raise
    except Exception:
        # Underlying exceptions may include a credential-bearing service URL.
        raise NotificationTransportError("notification provider error") from None
    if not bool(result):
        raise NotificationTransportError("notification provider did not accept")
