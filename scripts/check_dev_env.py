#!/usr/bin/env python3
"""Developer environment preflight: is THIS interpreter the right one?

Run it with the interpreter you intend to use, before tests, the API, the
worker, or a migration:

    .venv/bin/python scripts/check_dev_env.py
    .venv/bin/python scripts/check_dev_env.py --json

It exists because plain ``python`` on a developer machine can silently resolve
to a different installation (for example Anaconda's Python 3.13 without the
project's dependencies) than the project's ``.venv`` (Python 3.12). Code then
fails in confusing ways - or, worse, partly works.

Design rules:

* **Standard library only.** It must run under the WRONG interpreter, which by
  definition may lack the project's dependencies.
* **Never connects to anything.** Modules are located with
  ``importlib.util.find_spec`` (not imported) and the database target is
  classified by parsing the URL string. No socket is opened, no DB is touched.
* **Never prints credentials.** Only the URL scheme, a host *category*, and the
  database name are reported.
* **Actionable.** Every problem says what to run instead.

Exit codes: 0 = OK (warnings allowed), 1 = at least one error (or a warning
with ``--strict``), 2 = bad arguments.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".venv"

# CI runs 3.11 (.github/workflows/*.yml); local development uses 3.12.
SUPPORTED_VERSIONS = {(3, 11): "CI", (3, 12): "development"}

# Import names of requirements.txt entries (plus greenlet, which async
# SQLAlchemy needs at runtime, and langchain_core, pulled in by langchain).
RUNTIME_MODULES = (
    "fastapi",
    "pydantic_settings",
    "dotenv",
    "sqlalchemy",
    "greenlet",
    "asyncpg",
    "alembic",
    "pgvector",
    "langchain_postgres",
    "httpx",
    "structlog",
    "openai",
    "langchain",
    "langchain_core",
    "langgraph",
    "langchain_openai",
    "langchain_community",
    "tiktoken",
    "pypdf",
    "prometheus_client",
    "jose",
    "cryptography",
)
DEV_MODULES = ("pytest", "pytest_asyncio", "respx", "ruff", "mypy")

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "postgres", "db"}


@dataclass
class Finding:
    level: str  # "error" | "warning" | "info"
    check: str
    message: str
    fix: str | None = None


@dataclass
class Report:
    python_version: str
    executable: str
    prefix: str
    in_repo_venv: bool
    conda: bool
    path_python: str | None
    database: dict[str, str | None]
    missing_modules: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


def unsupported_python_message(version: tuple[int, ...]) -> str | None:
    """An actionable message if ``version`` is not a supported interpreter."""
    if tuple(version[:2]) in SUPPORTED_VERSIONS:
        return None
    shown = ".".join(str(part) for part in version[:3])
    return (
        f"Python {shown} is not supported by cxops-ai (3.12 for development, 3.11 "
        "in CI). You are probably running a system or Anaconda interpreter instead "
        "of the project virtualenv. Use: .venv/bin/python -m pytest ...  "
        "Diagnose with: .venv/bin/python scripts/check_dev_env.py"
    )


def is_conda(prefix: str, version_text: str, environ: dict[str, str]) -> bool:
    """True if the interpreter belongs to a conda/Anaconda installation."""
    return (
        (Path(prefix) / "conda-meta").exists()
        or "conda" in version_text.lower()
        or "anaconda" in prefix.lower()
        or bool(environ.get("CONDA_PREFIX") and prefix.startswith(environ["CONDA_PREFIX"]))
    )


def classify_database_url(url: str | None) -> dict[str, str | None]:
    """Host category + db name of a database URL, without credentials.

    Categories: ``local``, ``neon`` (the production database authority),
    ``render``, ``other``, ``unset``, ``unparseable``. Never resolves or
    connects to the host.
    """
    if not url:
        return {"category": "unset", "scheme": None, "database": None, "source": None}
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return {"category": "unparseable", "scheme": None, "database": None, "source": None}
    if host in LOCAL_HOSTS or host.endswith((".localhost", ".local")):
        category = "local"
    elif host.endswith(".neon.tech"):
        category = "neon"
    elif host.endswith(".render.com"):
        category = "render"
    elif not host:
        category = "unparseable"
    else:
        category = "other"
    return {
        "category": category,
        "scheme": parts.scheme or None,
        "database": parts.path.lstrip("/") or None,
        "port": str(port) if port else None,
    }


def read_dotenv_value(path: Path, key: str) -> str | None:
    """A single ``KEY=value`` from a .env file (no interpolation, no import)."""
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ")
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def missing_modules(names: tuple[str, ...]) -> list[str]:
    missing = []
    for name in names:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return missing


def build_report(
    *,
    version: tuple[int, int, int],
    executable: str,
    prefix: str,
    version_text: str,
    environ: dict[str, str],
    path_python: str | None,
    venv_dir: Path,
    dotenv_path: Path,
    missing: list[str],
    allow_remote_database: bool = False,
) -> Report:
    in_venv = Path(prefix).resolve() == venv_dir.resolve() if venv_dir.exists() else False
    conda = is_conda(prefix, version_text, environ)
    db_url = environ.get("DATABASE_URL")
    source = "environment" if db_url else None
    if not db_url:
        db_url = read_dotenv_value(dotenv_path, "DATABASE_URL")
        source = ".env" if db_url else None
    database = classify_database_url(db_url)
    database["source"] = source

    report = Report(
        python_version=".".join(str(part) for part in version),
        executable=executable,
        prefix=prefix,
        in_repo_venv=in_venv,
        conda=conda,
        path_python=path_python,
        database=database,
        missing_modules=missing,
    )
    venv_python = venv_dir / "bin" / "python"
    use_venv = f"use {venv_python} (or `source .venv/bin/activate`)"

    if unsupported_python_message(version) is not None:
        report.findings.append(
            Finding(
                "error",
                "python_version",
                f"Python {report.python_version} is not supported; "
                "this project runs on 3.12 (development) and 3.11 (CI).",
                use_venv,
            )
        )
    if conda:
        report.findings.append(
            Finding(
                "error",
                "interpreter",
                "This is a conda/Anaconda interpreter, not the project's virtualenv.",
                use_venv,
            )
        )
    elif not in_venv:
        report.findings.append(
            Finding(
                "warning",
                "interpreter",
                f"Interpreter is outside the repository virtualenv ({venv_dir}).",
                use_venv,
            )
        )
    if path_python and Path(path_python).resolve() != Path(executable).resolve():
        report.findings.append(
            Finding(
                "warning",
                "path_python",
                f"Plain `python` on PATH resolves to {path_python}, not this interpreter; "
                "commands typed as `python ...` will use a different environment.",
                "invoke .venv/bin/python explicitly or activate .venv first",
            )
        )
    if missing:
        report.findings.append(
            Finding(
                "error",
                "modules",
                f"Missing required modules: {', '.join(missing)}.",
                f"{venv_python} -m pip install -r requirements.txt -r requirements-dev.txt",
            )
        )
    category = database["category"]
    if category == "neon":
        report.findings.append(
            Finding(
                "warning" if allow_remote_database else "error",
                "database",
                "DATABASE_URL points at Neon, the PRODUCTION database authority. "
                "Local tests, workers, and migrations must never run against it.",
                "point DATABASE_URL at a local or disposable database",
            )
        )
    elif category in ("render", "other"):
        report.findings.append(
            Finding(
                "warning" if allow_remote_database else "error",
                "database",
                f"DATABASE_URL points at a non-local host (category: {category}).",
                "use a local or disposable database for development and tests",
            )
        )
    elif category in ("unset", "unparseable"):
        report.findings.append(
            Finding("warning", "database", f"DATABASE_URL is {category}.", "set it in .env")
        )
    return report


def render_text(report: Report) -> str:
    db = report.database
    lines = [
        f"python      {report.python_version}  ({report.executable})",
        f"virtualenv  {'repository .venv' if report.in_repo_venv else 'NOT the repository .venv'}"
        + ("  [conda]" if report.conda else ""),
        f"PATH python {report.path_python or 'not found'}",
        (
            f"database    {db['category']}  scheme={db['scheme']} db={db['database']} "
            f"(from {db['source'] or 'nowhere'}; credentials never shown)"
        ),
        f"modules     {'all present' if not report.missing_modules else 'MISSING: ' + ', '.join(report.missing_modules)}",
    ]
    for finding in report.findings:
        lines.append(f"{finding.level.upper():7} [{finding.check}] {finding.message}")
        if finding.fix:
            lines.append(f"        fix: {finding.fix}")
    if report.errors:
        result = "FAIL"
    else:
        result = "OK (with warnings)" if report.warnings else "OK"
    lines.append(f"RESULT      {result}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_dev_env.py",
        description="Check that this interpreter and configuration are the project's.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dev", action="store_true", help="also require test/lint tooling")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument(
        "--allow-remote-database",
        action="store_true",
        help="downgrade a non-local DATABASE_URL from error to warning",
    )
    args = parser.parse_args(argv)

    modules = RUNTIME_MODULES + (DEV_MODULES if args.dev else ())
    report = build_report(
        version=tuple(sys.version_info[:3]),  # type: ignore[arg-type]
        executable=sys.executable,
        prefix=sys.prefix,
        version_text=sys.version,
        environ=dict(os.environ),
        path_python=shutil.which("python"),
        venv_dir=VENV_DIR,
        dotenv_path=REPO_ROOT / ".env",
        missing=missing_modules(modules),
        allow_remote_database=args.allow_remote_database,
    )
    if args.json:
        payload = asdict(report)
        payload["ok"] = not report.errors
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
