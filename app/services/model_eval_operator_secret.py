"""Terminal-only, ephemeral credential input for a future approved evaluation.

This module does not install the provider, read environment variables, persist a
secret, or open a network connection.  The caller must supply the authorized
operation as a callback and consume the credential within the same process.
"""

from __future__ import annotations

import getpass
import sys
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TextIO, TypeVar


class OperatorSecretInputBlocked(RuntimeError):
    """The credential could not be accepted without a visible-input risk."""


T = TypeVar("T")


@contextmanager
def operator_entered_api_key(
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    prompt_fn: Callable[..., str] = getpass.getpass,
) -> Iterator[str]:
    """Yield one masked, process-local key and drop our reference on exit.

    Both streams must be terminals.  ``getpass`` normally falls back to visible
    input when terminal control is unavailable; treating its warning as an error
    makes that fallback impossible here.  Python strings cannot be reliably
    zeroized, so this is lifetime minimization rather than a memory-erasure claim.
    """

    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stderr
    if not input_stream.isatty() or not output_stream.isatty():
        raise OperatorSecretInputBlocked(
            "restricted credential entry requires an interactive terminal"
        )

    secret = ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                secret = prompt_fn(
                    "Existing restricted CXOps AI API key: ",
                    stream=output_stream,
                )
            except (KeyboardInterrupt, Exception):  # noqa: BLE001 - redact prompt failures
                raise OperatorSecretInputBlocked(
                    "restricted credential entry failed without accepting input"
                ) from None
        if (
            not isinstance(secret, str)
            or not secret
            or any(character.isspace() for character in secret)
        ):
            raise OperatorSecretInputBlocked(
                "restricted credential was rejected without being retained"
            )
        yield secret
    finally:
        # Limit this module's reference lifetime.  This does not promise Python
        # heap zeroization or protection from privileged process inspection.
        secret = ""


def run_with_operator_api_key(operation: Callable[[str], T]) -> T:
    """Run one separately authorized operation in the same prompt process."""

    with operator_entered_api_key() as secret:
        return operation(secret)
