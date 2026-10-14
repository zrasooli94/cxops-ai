"""Non-sensitive build identity for release verification (Phase 1R).

``/version`` reports which commit a running process was built from, so an
operator can confirm a deploy without shell access or dashboard login. The
value is read once from the platform's own build metadata (Render exposes
``RENDER_GIT_COMMIT``; CI exposes ``GITHUB_SHA``) and is accepted only when it
is a plain hexadecimal commit id. Anything else - a branch name, a URL, a
token that was put in the wrong variable - is dropped rather than echoed, so
this endpoint can never become a channel for configuration or secrets.
"""

from __future__ import annotations

import os
import re
import time
from datetime import UTC, datetime

# Checked in order; the first well-formed value wins.
_SHA_ENVIRONMENT_VARIABLES = (
    "BUILD_SHA",
    "RENDER_GIT_COMMIT",
    "SOURCE_VERSION",
    "GIT_COMMIT",
    "GITHUB_SHA",
)
_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")

PROCESS_STARTED_AT = datetime.now(UTC)
_PROCESS_STARTED_MONOTONIC = time.monotonic()


def build_sha(environ: dict[str, str] | None = None) -> str | None:
    """The deployed commit id, or ``None`` when no valid one is available."""
    source = os.environ if environ is None else environ
    for name in _SHA_ENVIRONMENT_VARIABLES:
        candidate = (source.get(name) or "").strip().lower()
        if _SHA_PATTERN.fullmatch(candidate):
            return candidate
    return None


def uptime_seconds() -> float:
    return round(time.monotonic() - _PROCESS_STARTED_MONOTONIC, 1)
