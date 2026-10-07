"""The optional live RAG and Agent evaluations must never touch the production key.

Scheduled production work made ``secrets.OPENAI_API_KEY`` a required repository
secret. That collateral turned two optional evaluation jobs from "skipped
because unprovisioned" into "now running against a real key" -- a cost and
side-effect change nobody opted into. This module pins the separation that
keeps them optional again:

* the evaluation jobs read only their own secret pair
  (``EVALUATION_OPENAI_API_KEY`` + ``EVALUATION_ORGANIZATION_ID``);
* the two must both be configured -- one is not enough;
* either missing means a successful run that prints a skip message, not a
  skipped job and not a failure;
* every tenant-scoped command carries ``--organization-id`` explicitly, so a
  run that does execute can never operate on an unresolved/NULL organization;
* ``production-worker.yml`` keeps consuming ``secrets.OPENAI_API_KEY`` -- this
  separation is one-way.

Evaluation executes against a real LLM provider, so the assertions that block
it ("both secrets required", "--organization-id present") are written to fail
loud if a future edit weakens them; no run in this file is executed and no
network call is made.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PRODUCTION_WORKER = REPO_ROOT / ".github" / "workflows" / "production-worker.yml"

EVAL_JOBS = ("rag-evaluation", "agent-evaluation")


def _source(path: Path) -> str:
    assert path.exists(), f"{path.relative_to(REPO_ROOT)} is missing"
    return path.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Executable lines only: comments and blanks removed, indentation kept."""
    lines: list[str] = []
    for raw in text.splitlines():
        without_comment = raw.split(" #", 1)[0]
        if not without_comment.strip() or without_comment.strip().startswith("#"):
            continue
        lines.append(without_comment.rstrip())
    return lines


@pytest.fixture(scope="module")
def ci_indented() -> list[str]:
    return _code_lines(_source(CI))


@pytest.fixture(scope="module")
def worker_indented() -> list[str]:
    return _code_lines(_source(PRODUCTION_WORKER))


@pytest.fixture(scope="module")
def ci_lines(ci_indented: list[str]) -> list[str]:
    return [line.strip() for line in ci_indented]


@pytest.fixture(scope="module")
def worker_lines(worker_indented: list[str]) -> list[str]:
    return [line.strip() for line in worker_indented]


def _run_lines(lines: list[str]) -> list[str]:
    """Every ``run:`` command line in a stripped line list."""
    return [line for line in lines if line.startswith("run:")]


def _job_block(indented: list[str], header: str) -> list[str]:
    """The stripped lines of one top-level job, until the next job's key.

    Job keys sit at a fixed indent (``jobs:`` is nested under the file root);
    any later line indented to that same depth opens the next sibling.
    """
    start = next(
        i
        for i, line in enumerate(indented)
        if line.strip() in (header, f"{header}:")
    )
    header_indent = len(indented[start]) - len(indented[start].lstrip())
    collected: list[str] = []
    for line in indented[start + 1 :]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= header_indent:
            break
        collected.append(line.strip())
    return collected


# ---------------------------------------------------------------------------
# Isolation from the production key
# ---------------------------------------------------------------------------


def test_ci_evaluations_never_refer_to_the_production_key(ci_lines: list[str]) -> None:
    """``secrets.OPENAI_API_KEY`` exists nowhere in CI.

    This is the whole point: the moment the production key was added for
    ``production-worker.yml``, the old ``if: env.OPENAI_API_KEY != ''`` gates
    went live against a real credential. The fix is structural -- the name
    cannot appear here at all, so there is no future edit that re-wires the
    evaluation jobs to the production secret by accident.
    """
    assert not any("secrets.OPENAI_API_KEY" in line for line in ci_lines), (
        "CI evaluations must never consume the production OPENAI_API_KEY"
    )


def test_production_worker_keeps_the_production_key(worker_lines: list[str]) -> None:
    """The production worker's key mapping is unchanged."""
    assert "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}" in worker_lines, (
        "production-worker.yml must keep consuming the production key"
    )


def test_every_openai_key_used_in_ci_evaluations_is_the_dedicated_eval_key(
    ci_indented: list[str],
) -> None:
    """Every ``OPENAI_API_KEY`` mapping in the eval jobs is the eval secret.

    The unit-test job legitimately maps ``OPENAI_API_KEY: test-key`` for a
    fake-key test matrix; that is not a live evaluation and is out of scope
    here. But inside the two live evaluation jobs there must be no other
    source of a key: the mapping exists and is always the dedicated eval
    secret, never the production one.
    """
    for job in EVAL_JOBS:
        block = _job_block(ci_indented, job)
        mappings = [line for line in block if line.startswith("OPENAI_API_KEY:")]
        assert mappings, f"{job} must map the eval key into OPENAI_API_KEY"
        assert all(
            line == "OPENAI_API_KEY: ${{ secrets.EVALUATION_OPENAI_API_KEY }}"
            for line in mappings
        ), mappings


# ---------------------------------------------------------------------------
# Both secrets required, otherwise a successful skip
# ---------------------------------------------------------------------------


def test_both_evaluation_secrets_gate_the_run(ci_lines: list[str]) -> None:
    """The enable marker needs the key AND the org id.

    ``--organization-id`` is required by the scripts, so a key without an org
    id is a failing run, not a skipped one. Requiring the pair up front means
    the gate never offers a half-configured evaluation.
    """
    markers = [
        line
        for line in ci_lines
        if line.startswith("EVALUATION_ENABLED:")
    ]
    assert len(markers) == len(EVAL_JOBS), markers
    for marker in markers:
        assert "EVALUATION_OPENAI_API_KEY != ''" in marker
        assert "EVALUATION_ORGANIZATION_ID != ''" in marker
        assert " && " in marker, "one missing secret must not enable the run"


def test_missing_secrets_prints_a_clear_skip(ci_indented: list[str]) -> None:
    """Each evaluation job ends in a skip step that exits successfully.

    Disabled is ``EVALUATION_ENABLED == 'false'`` -- the exact inverse of the
    gate on every tenant-scoped step. That inverse pairing is what guarantees
    the run prints the message exactly when nothing executes, and that the
    message names both secrets so an operator knows what to provision.
    """
    for job in EVAL_JOBS:
        block = _job_block(ci_indented, job)
        label = "RAG" if job == "rag-evaluation" else "Agent"
        assert any(f"{label} evaluation skipped" in line for line in block), (
            f"{job} must declare a skip step"
        )
        assert any(
            line == "if: ${{ env.EVALUATION_ENABLED == 'false' }}" for line in block
        ), f"{job} skip step must fire exactly when evals are disabled"
        block_text = " ".join(block)
        assert "EVALUATION_OPENAI_API_KEY and EVALUATION_ORGANIZATION_ID" in block_text
        assert "not both configured" in block_text


def test_evaluation_jobs_are_not_silently_skipped(ci_indented: list[str]) -> None:
    """The job itself runs (no job-level ``if``), so it can print its message.

    A job-level gate would make the missing-secret path a console entry that
    says ``skipped`` and nothing else. Completing "successfully" here means an
    echo, which requires the job to actually start.
    """
    for job in EVAL_JOBS:
        block = _job_block(ci_indented, job)
        first_step = next(
            (i for i, line in enumerate(block) if line.startswith("- name:")),
            len(block),
        )
        header = block[:first_step]
        assert not any(line.startswith("if:") for line in header), (
            f"{job} must not gate the whole job -- it must run to print its skip message"
        )
        assert any(
            line.startswith("if: ${{ env.EVALUATION_ENABLED ==") for line in block
        ), f"{job} must react to the disabled state inside the job"


# ---------------------------------------------------------------------------
# Tenant scoping of every evaluation command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job", "script"),
    [
        ("rag-evaluation", "scripts.seed_knowledge_base"),
        ("rag-evaluation", "scripts.evaluate_rag"),
        ("agent-evaluation", "scripts.seed_knowledge_base"),
        ("agent-evaluation", "scripts.evaluate_agent"),
    ],
)
def test_tenant_scoped_commands_receive_the_organization_id(
    ci_indented: list[str], job: str, script: str
) -> None:
    """Every evaluation command is scoped to one organization.

    The scripts require ``--organization-id``; a bare invocation aborts. The
    run line must hand it over explicitly for the seeded and evaluated
    organization to match, so the knowledge base and the metric both belong to
    the same tenant.
    """
    block = "\n".join(_job_block(ci_indented, job))
    run_lines = [
        line.strip()
        for line in block.splitlines()
        if line.startswith("run:")
    ]
    matches = [line for line in run_lines if script in line]
    assert matches, f"{job} must invoke {script}"
    for match in matches:
        assert f"{script} --organization-id \"$EVALUATION_ORGANIZATION_ID\"" in match, (
            f"{job} invokes {script} without an explicit organization id"
        )


def test_no_evaluation_command_is_bare(ci_lines: list[str]) -> None:
    """No ``run:`` line calls an evaluation script without the org flag."""
    for run in _run_lines(ci_lines):
        scripts_involved = (
            "seed_knowledge_base",
            "evaluate_rag",
            "evaluate_agent",
        )
        if any(script in run for script in scripts_involved):
            assert "--organization-id" in run, run


# ---------------------------------------------------------------------------
# Secret values stay out of the log
# ---------------------------------------------------------------------------


def test_secrets_never_appear_in_a_run_block(ci_lines: list[str]) -> None:
    """Secrets are referenced only in ``env:`` mappings and the if-marker.

    A secret interpolated into ``run:`` is echoed by the shell -- GitHub masks
    it only after it has already gone to the log transcript in unmasked form.
    The strongest form of this check is trivially structural: ``run:`` lines
    must contain no ``${{ ... }}`` and no ``secrets.`` at all.
    """
    for run in _run_lines(ci_lines):
        assert "secrets." not in run, f"secret referenced inside a run block: {run}"
        assert "${{" not in run, f"expression inside a run block: {run}"


def test_secret_references_are_limited_to_supported_shapes(ci_lines: list[str]) -> None:
    """Every ``secrets.`` occurrence is an env mapping or the enable marker.

    ``printenv``/``set -x``/``env |`` would dump the whole environment; each of
    those turns a ``secrets.*`` env mapping into a log line. None may appear in
    the workflow.
    """
    assignment = re.compile(r"^[A-Z0-9_]+: \$\{\{ secrets\.[A-Z0-9_]+ \}\}$")
    marker = (
        r"^EVALUATION_ENABLED: \$\{\{ secrets\.EVALUATION_OPENAI_API_KEY != '' && "
        r"secrets\.EVALUATION_ORGANIZATION_ID != '' \}\}$"
    )
    for line in ci_lines:
        if "secrets." not in line:
            continue
        assert assignment.match(line) or re.match(marker, line), (
            f"secrets used outside a supported shape: {line!r}"
        )

    for forbidden in ("printenv", "set -x", "env |"):
        assert not any(forbidden in line for line in ci_lines), (
            f"{forbidden!r} would print the environment"
        )