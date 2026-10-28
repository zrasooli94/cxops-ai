"""Small, provider-free advisory leases for local evaluation review holds."""

from __future__ import annotations

import fcntl
import os
import stat
from dataclasses import dataclass
from typing import Self


def owner_lock_name(manifest_digest: str) -> str:
    return f".{manifest_digest}.owner-lock"


@dataclass
class ReviewHoldLease:
    descriptor: int
    name: str

    def close(self) -> None:
        if self.descriptor < 0:
            return
        descriptor, self.descriptor = self.descriptor, -1
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def acquire_review_hold_lease(
    directory_descriptor: int,
    manifest_digest: str,
    *,
    blocking: bool = False,
) -> ReviewHoldLease | None:
    """Acquire an owner-only lease without following links."""
    name = owner_lock_name(manifest_digest)
    descriptor = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_descriptor,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PermissionError("review hold lease is unsafe")
        os.fchmod(descriptor, 0o600)
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, flags)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return ReviewHoldLease(descriptor=descriptor, name=name)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
