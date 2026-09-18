"""Deterministic lock-file consistency regression test.

The Render Dockerfile installs requirements-lock.txt when present, so a
runtime dependency declared in requirements.txt but omitted from the lock file
never reaches production (e.g. the missing cryptography/ python-jose that caused
startup to crash with ModuleNotFoundError). This test keeps the check local and
fast: parse both files, require every declared runtime package to be pinned.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO_ROOT / "requirements.txt"
LOCK_FILE = REPO_ROOT / "requirements-lock.txt"


def _dist_name(token: str) -> str:
    """Canonical distribution name (PEP 503), e.g. prometheus_client -> prometheus-client."""
    return token.replace("_", "-").lower()


def _declared_runtime_packages() -> set[str]:
    names: set[str] = set()
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "-c ", "-e ")):
            continue
        package = line.split(";")[0].strip()
        if "[" in package:
            package = package[: package.index("[")]
        if "=" in package:
            package = package[: package.index("=")]
        package = package.rstrip("><=!,~ ")
        if package:
            names.add(_dist_name(package))
    return names


def _lock_file_names() -> set[str]:
    names: set[str] = set()
    for raw in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("==", 1)[0].strip()
        if name:
            names.add(_dist_name(name))
    return names


def test_runtime_deps_are_pinned_in_lock_file() -> None:
    declared = _declared_runtime_packages()
    pinned = _lock_file_names()
    assert declared, "no runtime deps parsed from requirements.txt"
    missing = sorted(declared - pinned)
    assert not missing, (
        "requirements-lock.txt is missing runtime deps declared in "
        f"requirements.txt: {', '.join(missing)}. Render installs the lock "
        "file, so omitted packages never reach production."
    )