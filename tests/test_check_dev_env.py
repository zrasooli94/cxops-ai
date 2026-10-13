"""The developer environment preflight must catch the wrong interpreter loudly,
never connect to anything, and never print credentials."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from scripts import check_dev_env as env

SECRET_URL = "postgresql+asyncpg://admin:s3cr3t-pw@ep-cool-1234.us-east-2.aws.neon.tech/neondb"


def _report(tmp_path: Path, **overrides):
    venv = tmp_path / ".venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    params = {
        "version": (3, 12, 13),
        "executable": str(venv / "bin" / "python"),
        "prefix": str(venv),
        "version_text": "3.12.13 (main) [Clang]",
        "environ": {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/cxops"},
        "path_python": str(venv / "bin" / "python"),
        "venv_dir": venv,
        "dotenv_path": tmp_path / ".env",
        "missing": [],
    }
    params.update(overrides)
    return env.build_report(**params)


def test_project_venv_with_local_database_is_ok(tmp_path: Path) -> None:
    report = _report(tmp_path)
    assert report.errors == []
    assert report.warnings == []
    assert report.in_repo_venv is True
    assert report.database["category"] == "local"


def test_anaconda_313_without_dependencies_is_rejected(tmp_path: Path) -> None:
    """The exact local failure: plain python -> Anaconda 3.13, no langchain_core."""
    conda = tmp_path / "anaconda3"
    (conda / "conda-meta").mkdir(parents=True)
    report = _report(
        tmp_path,
        version=(3, 13, 9),
        executable=str(conda / "bin" / "python"),
        prefix=str(conda),
        version_text="3.13.9 | packaged by Anaconda, Inc. |",
        path_python=str(conda / "bin" / "python"),
        missing=["langchain_core"],
    )
    checks = {f.check for f in report.errors}
    assert {"python_version", "interpreter", "modules"} <= checks
    assert report.conda is True
    assert all(f.fix for f in report.errors), "every error must say what to do instead"
    assert "langchain_core" in next(f.message for f in report.errors if f.check == "modules")


def test_plain_python_resolving_elsewhere_is_flagged(tmp_path: Path) -> None:
    report = _report(tmp_path, path_python="/opt/anaconda3/bin/python")
    assert [f.check for f in report.warnings] == ["path_python"]
    assert report.errors == []


@pytest.mark.parametrize("version", [(3, 11, 9), (3, 12, 1)])
def test_ci_and_development_versions_are_supported(tmp_path: Path, version) -> None:
    assert not [f for f in _report(tmp_path, version=version).findings if f.check == "python_version"]


@pytest.mark.parametrize("version", [(3, 10, 14), (3, 13, 0), (3, 14, 0)])
def test_other_versions_are_errors(tmp_path: Path, version) -> None:
    assert [f.check for f in _report(tmp_path, version=version).errors] == ["python_version"]


def test_interpreter_outside_venv_is_a_warning(tmp_path: Path) -> None:
    report = _report(tmp_path, prefix="/usr/local", executable="/usr/local/bin/python3.12",
                     path_python="/usr/local/bin/python3.12")
    assert [f.check for f in report.warnings] == ["interpreter"]


def test_neon_database_is_an_error_and_never_printed(tmp_path: Path) -> None:
    report = _report(tmp_path, environ={"DATABASE_URL": SECRET_URL})
    assert report.database["category"] == "neon"
    assert [f.check for f in report.errors] == ["database"]
    rendered = env.render_text(report) + json.dumps(env.asdict(report))
    for leaked in ("s3cr3t-pw", "admin", "ep-cool-1234"):
        assert leaked not in rendered


def test_remote_database_can_be_explicitly_downgraded(tmp_path: Path) -> None:
    report = _report(tmp_path, environ={"DATABASE_URL": SECRET_URL}, allow_remote_database=True)
    assert report.errors == []
    assert [f.check for f in report.warnings] == ["database"]


@pytest.mark.parametrize(
    ("url", "category"),
    [
        ("postgresql://u:p@localhost/x", "local"),
        ("postgresql://u:p@127.0.0.1:55432/x_disposable_test", "local"),
        ("postgresql://u:p@postgres:5432/cxops", "local"),
        ("postgresql://u:p@dpg-abc.oregon-postgres.render.com/db", "render"),
        ("postgresql://u:p@db.example.com/db", "other"),
        ("", "unset"),
        (None, "unset"),
    ],
)
def test_database_classification(url, category) -> None:
    assert env.classify_database_url(url)["category"] == category


def test_database_url_falls_back_to_dotenv_without_importing_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text('# local\nexport DATABASE_URL="postgresql://u:p@localhost/cxops"\n')
    report = _report(tmp_path, environ={}, dotenv_path=dotenv)
    assert report.database["category"] == "local"
    assert report.database["source"] == ".env"
    assert report.database["database"] == "cxops"


def test_missing_database_url_is_a_warning(tmp_path: Path) -> None:
    report = _report(tmp_path, environ={})
    assert [f.check for f in report.warnings] == ["database"]


def test_missing_modules_are_located_without_importing() -> None:
    assert env.missing_modules(("json", "definitely_not_a_module_xyz")) == [
        "definitely_not_a_module_xyz"
    ]


def test_main_never_opens_a_socket(monkeypatch, capsys) -> None:
    def _no_socket(*_args, **_kwargs):
        raise AssertionError("check_dev_env must never open a network connection")

    monkeypatch.setattr(socket, "create_connection", _no_socket)
    monkeypatch.setattr(socket.socket, "connect", _no_socket)
    monkeypatch.setenv("DATABASE_URL", SECRET_URL)

    code = env.main(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1  # Neon is an error by default
    assert payload["ok"] is False
    assert payload["database"]["category"] == "neon"
    assert "s3cr3t-pw" not in json.dumps(payload)


def test_strict_turns_warnings_into_failure(monkeypatch) -> None:
    monkeypatch.setattr(env, "build_report", lambda **_kw: env.Report(
        python_version="3.12.13", executable="x", prefix="x", in_repo_venv=False,
        conda=False, path_python=None, database={"category": "local", "scheme": None,
        "database": None, "source": None},
        findings=[env.Finding("warning", "interpreter", "outside venv")],
    ))
    assert env.main([]) == 0
    assert env.main(["--strict"]) == 1


@pytest.mark.parametrize(
    ("version", "supported"),
    [((3, 11, 9), True), ((3, 12, 13), True), ((3, 13, 9), False), ((3, 10, 0), False)],
)
def test_pytest_gate_message(version, supported) -> None:
    message = env.unsupported_python_message(version)
    assert (message is None) is supported
    if message:
        assert ".venv/bin/python" in message and "check_dev_env.py" in message
