"""The scheduled production worker workflow is a production entry point, so it is
read as text and asserted line by line.

Two things make a workflow file easy to break without noticing. It is never
executed by the test suite, so nothing fails when the schedule disappears; and
it is mostly prose comments, so a check that searches the raw file for a
forbidden word will trip over the comment that warns against it. Comments are
therefore stripped before every assertion below, which is the same convention
the launcher tests use: a warning that names the migration step must not
register as the migration step.

The properties asserted here are the ones whose loss is silent:

* a schedule removed during a workflow tidy-up leaves the pilot with no
  background worker at all, and no test, page, or log line says so;
* a preflight demoted to a later step (or dropped) lets a half-configured
  process claim a real production job;
* a migration added "while we're here" races the operator's deploy, from a
  scheduler that runs every five minutes;
* a secret interpolated into a ``run:`` block is written to the job log.

Nothing in this file is executed and no network call is made.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-worker.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

REQUIRED_SECRETS = (
    "DATABASE_URL",
    "ENCRYPTION_KEYS",
    "OPENAI_API_KEY",
    "AUTH_JWKS_URL",
    "AUTH_JWT_ISSUER",
)

REQUIRED_CONFIG = {
    "ENVIRONMENT": "production",
    "DEBUG": "false",
    "AUTH_MODE": "jwks",
    "AUTH_DEV_MODE": "false",
    "FRONTEND_BASE_URL": "https://cxops-ai.vercel.app",
    "BACKEND_PUBLIC_URL": "https://cxops-ai.onrender.com",
}


def _source(path: Path) -> str:
    assert path.exists(), f"{path.relative_to(REPO_ROOT)} is missing"
    return path.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Executable lines only: comments and blanks removed, indentation kept.

    A comment is a ``#`` at the start of the line or preceded by whitespace.
    Stripping this way keeps values like the cron expression intact while
    removing the prose that deliberately names the things being forbidden.
    Indentation survives because it is the only structure YAML has: without it
    a block helper cannot tell where ``permissions:`` ends.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        without_comment = raw.split(" #", 1)[0]
        if not without_comment.strip() or without_comment.strip().startswith("#"):
            continue
        lines.append(without_comment.rstrip())
    return lines


def _stripped(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines]


def _line_index(lines: list[str], needle: str) -> int:
    for index, line in enumerate(lines):
        if needle in line:
            return index
    raise AssertionError(f"no executable line containing {needle!r}")


def _block(lines: list[str], header: str) -> list[str]:
    """The stripped body of the YAML block opened by ``header``.

    A child belongs to the block while it is indented past the header; the
    first sibling or ancestor at the same depth ends it.
    """
    start = _line_index(lines, header)
    header_indent = len(lines[start]) - len(lines[start].lstrip())
    collected: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= header_indent:
            break
        collected.append(line.strip())
    return collected


@pytest.fixture(scope="module")
def workflow_indented() -> list[str]:
    return _code_lines(_source(WORKFLOW))


@pytest.fixture(scope="module")
def workflow_lines(workflow_indented: list[str]) -> list[str]:
    """Indented lines flattened, for membership assertions."""
    return _stripped(workflow_indented)


# ---------------------------------------------------------------------------
# Triggers and scheduling
# ---------------------------------------------------------------------------


def test_workflow_runs_every_five_minutes(workflow_lines: list[str]) -> None:
    """The pilot's only background worker has to actually be scheduled.

    This is the assertion that turns a silent deletion into a red test: without
    it, removing the schedule is indistinguishable from a quiet queue, because
    the next thing anyone notices is a customer waiting on an escalation that
    never fired.
    """
    assert any("*/5 * * * *" in line for line in workflow_lines), (
        "the production worker must keep its five-minute schedule"
    )
    assert any("cron:" in line for line in workflow_lines)


def test_workflow_can_be_dispatched_manually(workflow_lines: list[str]) -> None:
    """An operator must be able to run the drain on demand.

    A five-minute schedule is the wrong tool for the moment after a deploy, or
    for the moment a queue is visibly backed up and waiting for the next tick
    is worse than useless.
    """
    assert "workflow_dispatch:" in workflow_lines


def test_ci_does_not_gain_a_scheduled_worker() -> None:
    """The production worker is its own workflow, not a CI side effect.

    CI runs on push and pull request. A production job consumer belongs to the
    schedule that drains the queue, so that a branch build can never be mistaken
    for a production run -- and so that CI's permissions stay the ones a build
    needs rather than the ones a production worker needs.
    """
    ci = _source(CI_WORKFLOW)
    assert "scripts.worker" not in ci, "CI must not invoke the worker"
    assert "cron:" not in ci, "CI must not acquire a production schedule"


# ---------------------------------------------------------------------------
# Privilege and overlap
# ---------------------------------------------------------------------------


def test_workflow_permissions_are_read_only(workflow_indented: list[str]) -> None:
    """``contents: read`` and nothing else.

    The worker checks out source and talks to a database; it needs no write
    token, no pull-request token, and no package token. A workflow that can be
    triggered on a schedule should be assumed to be triggerable by anything
    that can trigger it.
    """
    permissions = _block(workflow_indented, "permissions:")
    assert permissions, "the workflow must declare permissions explicitly"
    assert "contents: read" in permissions
    assert not any("write" in line for line in permissions), (
        f"a scheduled production job must not hold a write token: {permissions}"
    )


def test_concurrent_runs_are_serialised(workflow_indented: list[str]) -> None:
    """One runner at a time, and a queued run waits rather than being dropped.

    ``cancel-in-progress: false`` is the load-bearing half: cancelling an
    in-flight production worker mid-job would strand a ``processing`` row until
    stale recovery. Note that this is an operational guard only -- correctness
    under overlap comes from atomic row-locked claiming in the database, and
    the workflow comments must keep saying so, because a future reader who
    believes the concurrency group is the integrity mechanism will delete it
    "safely" one day.
    """
    concurrency = _block(workflow_indented, "concurrency:")
    assert any("group: cxops-production-worker" in line for line in concurrency)
    assert any("cancel-in-progress: false" in line for line in concurrency)


# ---------------------------------------------------------------------------
# The gate, then the work
# ---------------------------------------------------------------------------


def test_production_preflight_runs_before_the_worker(workflow_lines: list[str]) -> None:
    """The gate has to be first, or it gates nothing.

    A worker that starts before validation can claim a job with a missing
    encryption key and fail halfway through a customer's integration. The
    ordering here is what makes the preflight a precondition rather than a
    diagnostic printed next to the damage.
    """
    preflight = _line_index(workflow_lines, "preflight_production_env.py")
    worker = _line_index(workflow_lines, "python -m scripts.worker")
    assert preflight < worker, "the preflight must run before the worker starts"
    assert workflow_lines[preflight].startswith("run:")


def test_a_failed_preflight_stops_the_job(workflow_lines: list[str]) -> None:
    """No ``continue-on-error`` on the gate, and no ``if: always()`` on the run.

    GitHub skips subsequent steps when one fails, which is only a safety
    property while nothing opts out of it. An explicit opt-out here would let
    the worker run on a rejected environment, which is precisely the failure
    the preflight exists to prevent.
    """
    assert not any("continue-on-error" in line for line in workflow_lines)
    assert not any(line.startswith("if:") for line in workflow_lines), (
        "conditional steps would let the worker run without the gate"
    )


def test_the_worker_command_is_bounded(workflow_lines: list[str]) -> None:
    """``--once --max-jobs 25 --max-seconds 240``, spelled out.

    The defaults live in ``scripts/worker.py`` too, but the workflow states
    them so the bound is visible in the one place an operator looks when a run
    is truncated: the job log. 240 seconds sits inside the five-minute
    interval, which is what keeps two scheduled runs from overlapping by
    design rather than by luck.
    """
    assert (
        "run: python -m scripts.worker --once --max-jobs 25 --max-seconds 240"
        in workflow_lines
    )


def test_the_job_timeout_is_a_backstop(workflow_lines: list[str]) -> None:
    """Six minutes, behind the application's own 240-second deadline.

    The platform timeout is the last line of defence against a hung network
    call, not the primary control. If it were the only bound, the failure mode
    would be a runner killed mid-job and a row stranded in ``processing`` for
    the stale-recovery window.
    """
    timeout = [line for line in workflow_lines if line.startswith("timeout-minutes:")]
    assert timeout == ["timeout-minutes: 6"]


def test_python_is_pinned_to_311(workflow_lines: list[str]) -> None:
    assert 'python-version: "3.11"' in workflow_lines


def test_checkout_uses_the_default_branch(workflow_lines: list[str]) -> None:
    """No ``ref:`` pin.

    A scheduled workflow must run the code the default branch currently
    points at. Pinning a ref would silently freeze the pilot on an old commit
    while the repository moved on -- the schedule would keep running, and it
    would keep running the wrong worker.
    """
    assert "uses: actions/checkout@v4" in workflow_lines
    assert not any(line.startswith("ref:") for line in workflow_lines)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def test_required_secrets_come_from_github_secrets(workflow_lines: list[str]) -> None:
    """Every credential is read from the Actions secret store.

    ``DATABASE_URL`` is the same Neon production database the Render API uses.
    There is exactly one production database, so the workflow must reference
    the shared credential rather than introduce a second one -- and it must
    reference it by name so no value can be committed here.
    """
    for name in REQUIRED_SECRETS:
        assert f"{name}: ${{{{ secrets.{name} }}}}" in workflow_lines, (
            f"{name} must be read from GitHub secrets"
        )


def test_production_configuration_is_explicit(workflow_lines: list[str]) -> None:
    """The non-sensitive half of the environment is stated, not assumed.

    These are shapes and origins rather than credentials, so they live in the
    file where a reviewer can see them. Getting any of them wrong is caught by
    the preflight -- which is exactly why the preflight runs first.
    """
    for key, value in REQUIRED_CONFIG.items():
        expected = f'{key}: "{value}"' if value in ("false", "true") else f"{key}: {value}"
        assert expected in workflow_lines, f"expected {expected!r}"


def test_no_secret_value_is_printed(workflow_lines: list[str]) -> None:
    """Secrets appear only as ``NAME: ${{ secrets.NAME }}`` env assignments.

    Interpolating a secret into a ``run:`` block writes it to the step's
    echoed command, and GitHub echoes commands by default. Constraining every
    occurrence to an env mapping means there is no syntax available in this
    file that could put a value on stdout.
    """
    assignment = re.compile(r"^[A-Z0-9_]+: \$\{\{ secrets\.[A-Z0-9_]+ \}\}$")
    occurrences = [line for line in workflow_lines if "${{ secrets." in line]
    assert occurrences, "the workflow must reference its secrets"
    for line in occurrences:
        assert assignment.match(line), f"secret used outside an env assignment: {line!r}"

    for forbidden in ("echo ${{", "printenv", "set -x", "env |"):
        assert not any(forbidden in line for line in workflow_lines), (
            f"{forbidden!r} would print the environment"
        )


# ---------------------------------------------------------------------------
# Migrations stay an operator action
# ---------------------------------------------------------------------------


def test_the_workflow_has_no_migration_step(workflow_lines: list[str]) -> None:
    """Migrations are never run from here -- not on a schedule, not anywhere.

    This job runs every five minutes. A migration step would mean a second
    migrator contending with a deploy an operator is running by hand, five
    minutes apart, forever. The migration path is ``scripts/run_migrations.sh``
    and it is invoked by a person, once per deploy.
    """
    for forbidden in ("alembic", "run_migrations", "upgrade head", "upgrade_head"):
        assert not any(forbidden in line for line in workflow_lines), (
            f"the production worker workflow must not run {forbidden!r}"
        )
