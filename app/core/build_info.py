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


# --- boot timing (Phase 1T) ---------------------------------------------------
# Seconds since the Python process was exec'd, read from /proc (Linux only, so
# ``None`` on a developer laptop). It spans interpreter start-up, every import,
# and the schema gate - exactly the "launch command -> serving" delay that
# platform logs show as a gap between "API startup command" and "Uvicorn
# process started". Nothing is read from the environment or the network.


def process_age_seconds() -> float | None:
    try:
        with open("/proc/self/stat", encoding="ascii") as handle:
            after_comm = handle.read().rsplit(")", 1)[1].split()
        started_ticks = int(after_comm[19])  # field 22: starttime
        with open("/proc/uptime", encoding="ascii") as handle:
            uptime = float(handle.read().split()[0])
        return max(0.0, uptime - started_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, AttributeError):
        return None


# Taken as soon as this module is first imported, i.e. right after the
# interpreter and the web framework were loaded and ``app`` began importing.
IMPORT_STARTED_AGE_SECONDS = process_age_seconds()
_boot_complete_age: float | None = None


def mark_boot_complete() -> None:
    global _boot_complete_age
    _boot_complete_age = process_age_seconds()


def boot_seconds() -> float | None:
    """Process start -> start-up complete, or ``None`` before/unsupported."""
    return None if _boot_complete_age is None else round(_boot_complete_age, 1)


def import_phase_seconds() -> float | None:
    """Time the application's own imports took (start of ``app`` -> complete)."""
    if _boot_complete_age is None or IMPORT_STARTED_AGE_SECONDS is None:
        return None
    return round(_boot_complete_age - IMPORT_STARTED_AGE_SECONDS, 1)
