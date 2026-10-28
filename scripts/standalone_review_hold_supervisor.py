"""Standalone, provider-impossible monitor for local CXOps review holds.

This file is packaged verbatim outside the TCC-protected checkout. It uses
only the Python standard library, a fixed local ``psql`` binary for narrow
state verification, and the existing loopback public-chat close endpoint.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PACKAGE_VERSION = "cxops-review-supervisor-v1"
UTC = timezone.utc
HOLD_VERSION = 1
EXPECTED_BINDINGS = 10
RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class SupervisorBlocked(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Binding:
    fixture_conversation_id: str
    session_id: int
    token: str


@dataclass(frozen=True)
class Hold:
    path: Path
    digest: str
    organization_id: int
    run_id: str
    expires_at: datetime
    reviewed_source_commit: str
    bindings: tuple[Binding, ...]


@dataclass
class Lease:
    descriptor: int

    def close(self) -> None:
        if self.descriptor < 0:
            return
        descriptor, self.descriptor = self.descriptor, -1
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _canonical_digest(manifest: dict[str, Any]) -> str:
    body = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(body).hexdigest()


def _read_json_regular(path: Path, *, maximum_bytes: int = 1_000_000) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > maximum_bytes
        ):
            raise SupervisorBlocked("owner-only regular file required")
        data = b""
        while chunk := os.read(descriptor, 65536):
            data += chunk
            if len(data) > maximum_bytes:
                raise SupervisorBlocked("file exceeds bounded size")
    finally:
        os.close(descriptor)
    result = json.loads(data)
    if not isinstance(result, dict):
        raise SupervisorBlocked("JSON object required")
    return result


def _safe_directory(path: Path, *, create: bool = False) -> int:
    if create:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
        except FileExistsError:
            pass
    before = path.lstat()
    if not stat.S_ISDIR(before.st_mode) or path.is_symlink():
        raise SupervisorBlocked("unsafe directory")
    if before.st_uid != os.getuid() or before.st_mode & 0o077:
        raise SupervisorBlocked("directory must be owner-only")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    after = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(descriptor)
        raise SupervisorBlocked("directory changed while opening")
    return descriptor


def _acquire(directory_fd: int, name: str) -> Lease | None:
    descriptor = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise SupervisorBlocked("unsafe lease")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return Lease(descriptor)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _parse_hold(path: Path) -> Hold:
    payload = _read_json_regular(path)
    if payload.get("version") != HOLD_VERSION:
        raise SupervisorBlocked("hold version mismatch")
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        raise SupervisorBlocked("manifest missing")
    digest = _canonical_digest(manifest)
    name = path.name
    for suffix in (".cleanup", ".claimed"):
        name = name.removesuffix(suffix)
    name = name.removeprefix(".").removesuffix(".json")
    if payload.get("manifest_digest") != digest or name != digest:
        raise SupervisorBlocked("manifest digest mismatch")
    bindings = tuple(
        Binding(
            fixture_conversation_id=str(item["fixture_conversation_id"]),
            session_id=int(item["session_id"]),
            token=str(item["token"]),
        )
        for item in payload.get("bindings", ())
    )
    if (
        len(bindings) != EXPECTED_BINDINGS
        or len({item.session_id for item in bindings}) != EXPECTED_BINDINGS
        or any(not item.token for item in bindings)
    ):
        raise SupervisorBlocked("session bindings invalid")
    organization_id = int(manifest["organization_id"])
    run_id = str(manifest["run_id"])
    if not RUN_ID.fullmatch(run_id):
        raise SupervisorBlocked("run identity invalid")
    if (
        int(payload.get("organization_id", -1)) != organization_id
        or payload.get("run_id") != run_id
        or tuple(manifest.get("public_chat_session_ids", ()))
        != tuple(item.session_id for item in bindings)
    ):
        raise SupervisorBlocked("tenant/run/session drift")
    expiry = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
    manifest_expiry = datetime.fromisoformat(
        str(manifest["expires_at"]).replace("Z", "+00:00")
    )
    if expiry.tzinfo is None or manifest_expiry.tzinfo is None or expiry != manifest_expiry:
        raise SupervisorBlocked("expiry drift")
    reviewed_commit = str(manifest.get("reviewed_source_commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", reviewed_commit):
        raise SupervisorBlocked("reviewed source identity invalid")
    return Hold(
        path=path,
        digest=digest,
        organization_id=organization_id,
        run_id=run_id,
        expires_at=expiry.astimezone(UTC),
        reviewed_source_commit=reviewed_commit,
        bindings=bindings,
    )


class StandaloneSupervisor:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.hold_directory = Path(str(config["hold_directory"]))
        self.psql = Path(str(config["psql_path"]))
        self.backend_close_url = str(config["backend_close_url"])
        self.reviewed_source_commit = str(config["reviewed_source_commit"])

    def _psql(self, sql: str) -> str:
        result = subprocess.run(
            [str(self.psql), "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"],
            input=sql,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
                "PGHOST": str(self.config["db_host"]),
                "PGPORT": str(self.config["db_port"]),
                "PGDATABASE": str(self.config["db_name"]),
                "PGUSER": str(self.config["db_user"]),
            },
        )
        if result.returncode != 0:
            raise SupervisorBlocked("local database unavailable")
        return result.stdout.strip()

    def _latest_decision(self, hold: Hold) -> str | None:
        sql = f"""
SELECT COALESCE((
  SELECT d.decision
  FROM ai_evaluation_release_decisions d
  JOIN ai_evaluation_runs r ON r.id=d.candidate_run_id
   AND r.organization_id=d.organization_id
  WHERE r.organization_id={hold.organization_id}
    AND r.run_id='{hold.run_id}'
  ORDER BY d.created_at DESC, d.id DESC LIMIT 1
), '');
"""
        return self._psql(sql) or None

    def _verify_session(self, hold: Hold, binding: Binding) -> None:
        token_hash = hashlib.sha256(binding.token.encode()).hexdigest()
        sql = f"""
SELECT id::text || '|' || organization_id::text || '|' || status
FROM public_chat_sessions
WHERE id={binding.session_id}
  AND organization_id={hold.organization_id}
  AND token_hash='{token_hash}'
  AND expires_at > now();
"""
        value = self._psql(sql)
        if value != f"{binding.session_id}|{hold.organization_id}|closed" and not value.startswith(
            f"{binding.session_id}|{hold.organization_id}|"
        ):
            raise SupervisorBlocked("session binding unavailable")

    def _close_session(self, hold: Hold, binding: Binding) -> None:
        self._verify_session(hold, binding)
        request = urllib.request.Request(
            self.backend_close_url,
            method="POST",
            headers={"Authorization": f"Bearer {binding.token}"},
            data=b"",
        )
        try:
            with urllib.request.build_opener(_NoRedirect).open(
                request, timeout=10
            ) as response:
                payload = json.loads(response.read(4096))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise SupervisorBlocked("local close endpoint unavailable") from exc
        if payload != {"status": "closed"}:
            raise SupervisorBlocked("local close endpoint returned unexpected state")

    def _cleanup(self, path: Path, *, lease_owned: bool = False) -> tuple[int, ...]:
        hold = _parse_hold(path)
        directory_fd = _safe_directory(self.hold_directory)
        lease = None
        try:
            if not lease_owned:
                lease = _acquire(directory_fd, f".{hold.digest}.owner-lock")
                if lease is None:
                    return tuple(item.session_id for item in hold.bindings)
            failures: list[int] = []
            for binding in hold.bindings:
                try:
                    self._close_session(hold, binding)
                except Exception:  # noqa: BLE001 - retry exact cleanup later
                    failures.append(binding.session_id)
            if failures:
                return tuple(failures)
            try:
                os.unlink(path.name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            for suffix in ("claim-ack", "claim-lock"):
                try:
                    os.unlink(f".{hold.digest}.json.{suffix}", dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            return ()
        finally:
            if lease is not None:
                lease.close()
            os.close(directory_fd)

    def _invalidate(self, path: Path, reason: str) -> dict[str, Any]:
        hold = _parse_hold(path)
        directory_fd = _safe_directory(self.hold_directory)
        lease = None
        cleanup = self.hold_directory / f".{hold.digest}.json.cleanup"
        try:
            lease = _acquire(directory_fd, f".{hold.digest}.owner-lock")
            if lease is None:
                return {"digest": hold.digest, "status": "live_claim_active"}
            try:
                os.rename(path.name, cleanup.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            except FileNotFoundError:
                return {"digest": hold.digest, "status": "changed_concurrently"}
            failures = self._cleanup(cleanup, lease_owned=True)
            status = "cleanup_pending" if failures else "cleanup_complete"
            return {"digest": hold.digest, "status": f"{reason}_{status}", "cleanup_failure_count": len(failures)}
        finally:
            if lease is not None:
                lease.close()
            os.close(directory_fd)

    def reconcile_once(self) -> list[dict[str, Any]]:
        directory_fd = _safe_directory(self.hold_directory, create=True)
        os.close(directory_fd)
        events: list[dict[str, Any]] = []
        for path in sorted(self.hold_directory.glob(".*.json.cleanup")):
            try:
                hold = _parse_hold(path)
                failures = self._cleanup(path)
                events.append({"digest": hold.digest, "status": "cleanup_pending" if failures else "cleanup_complete", "cleanup_failure_count": len(failures)})
            except Exception:  # noqa: BLE001 - durable cleanup remains pending
                events.append({"digest": path.name, "status": "cleanup_pending"})
        candidates = sorted(self.hold_directory.glob("*.json")) + sorted(self.hold_directory.glob(".*.json.claimed"))
        for path in candidates:
            try:
                hold = _parse_hold(path)
                if path.name.endswith(".claimed"):
                    events.append(self._invalidate(path, "abandoned_claim"))
                elif hold.reviewed_source_commit != self.reviewed_source_commit:
                    events.append(self._invalidate(path, "source_drift"))
                elif datetime.now(UTC) >= hold.expires_at:
                    events.append(self._invalidate(path, "expired"))
                elif self._latest_decision(hold) == "rejected":
                    events.append(self._invalidate(path, "rejected"))
                else:
                    events.append({"digest": hold.digest, "status": "active"})
            except Exception:  # noqa: BLE001 - one invalid hold cannot stop polling
                events.append({"digest": path.name, "status": "invalid_hold"})
        return events


def _load_config(path: Path) -> dict[str, Any]:
    config = _read_json_regular(path, maximum_bytes=32_768)
    if config.get("package_version") != PACKAGE_VERSION:
        raise SupervisorBlocked("package version mismatch")
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if config.get("runtime_source_sha256") != source_hash:
        raise SupervisorBlocked("runtime source integrity mismatch")
    psql = Path(str(config.get("psql_path", "")))
    if psql.resolve() != Path(str(config.get("psql_realpath", ""))) or not psql.is_file():
        raise SupervisorBlocked("psql identity mismatch")
    if hashlib.sha256(psql.resolve().read_bytes()).hexdigest() != config.get("psql_sha256"):
        raise SupervisorBlocked("psql integrity mismatch")
    if config.get("environment") != "development":
        raise SupervisorBlocked("standalone supervisor is development-only")
    if config.get("provider_enabled") is not False:
        raise SupervisorBlocked("provider must remain disabled")
    if config.get("db_host") != "127.0.0.1":
        raise SupervisorBlocked("database host must be literal loopback")
    close_url = urlsplit(str(config.get("backend_close_url", "")))
    if (
        close_url.scheme != "http"
        or close_url.hostname != "127.0.0.1"
        or close_url.username is not None
        or close_url.password is not None
        or close_url.port is None
        or close_url.path != "/public/chat/sessions/close"
        or close_url.query
        or close_url.fragment
    ):
        raise SupervisorBlocked("close endpoint must be loopback")
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    arguments = parser.parse_args()
    config = _load_config(arguments.config)
    supervisor = StandaloneSupervisor(config)
    directory_fd = _safe_directory(supervisor.hold_directory, create=True)
    singleton = _acquire(directory_fd, ".provider-disabled-supervisor.owner-lock")
    os.close(directory_fd)
    if singleton is None:
        raise SupervisorBlocked("another supervisor is active")
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        while not stopping:
            print(json.dumps({"mode": PACKAGE_VERSION, "events": supervisor.reconcile_once()}, sort_keys=True), flush=True)
            if arguments.once:
                return 0
            signal.pause() if arguments.poll_seconds <= 0 else __import__("time").sleep(arguments.poll_seconds)
        return 0
    finally:
        singleton.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SupervisorBlocked as exc:
        print(f"supervisor_blocked={exc}", file=sys.stderr)
        raise SystemExit(2) from None
