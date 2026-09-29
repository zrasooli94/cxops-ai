"""`.replit` must stay loadable by Replit, and GitHub must be the source of truth.

Background, because this test exists rather than being obvious:

A committed `.replit` defined `run = "dev"` as a plain string *and* a `[[run]]`
array of tables. Those are the same TOML key, so the file could not parse:

    Cannot overwrite a value (at line 36, column 6)

Replit surfaces a config that will not load as a workspace that refuses to
start, and a workspace that refuses to start gets reset to the last commit on
`origin/master`. That reset then repeatedly discarded working Replit-side fixes
and restored the invalid file from GitHub — so the bug could not be fixed from
the Replit side at all, only from the repository. Making GitHub correct is the
only durable repair, which is what these tests enforce.

Everything here is static: it parses committed files and asserts their
semantics. No Replit account, network access, or live workspace is involved, so
CI catches a reintroduced defect before a workspace is ever created.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLIT = REPO_ROOT / ".replit"
REPLIT_NIX = REPO_ROOT / "replit.nix"
REPLIT_SOURCE = REPLIT.read_text(encoding="utf-8")

#: nix attributes the live Replit environment rejected as invalid in the current
#: channel. Listed as module constants so the assertion and its explanation stay
#: together, and so a future failure names the exact attribute.
INVALID_NIX_ATTRIBUTES = {
    "pkgs.python312": "Python is supplied by the `python-3.12` runtime module.",
    "pkgs.python312Packages.pip": (
        "Pip comes from the runtime module; the nix attribute is not available."
    ),
    "pkgs.nodejs_22": (
        "Node is supplied by the `nodejs-22` runtime module; duplicating it as "
        "a system dependency is a redundant runtime declaration."
    ),
}

#: The wrong module spelling, and the correct one. Easy to "fix" back by hand.
WRONG_PYTHON_MODULE = "python3-12"
RIGHT_PYTHON_MODULES = {"python-3.12", "nodejs-22"}


def _executable_lines(text: str) -> list[str]:
    """Non-comment lines, with trailing comments removed.

    Scanning raw text is the naive version of this helper, and it fails on
    exactly the comments that document a forbidden construct: a file explaining
    why `[[run]]` is disallowed necessarily contains the string `[[run]]`, and
    a test that greps the raw source cannot tell the explanation from the
    mistake. Every assertion below therefore runs against the effective
    configuration, not against the prose about it.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # Leading indentation is preserved on purpose. A top-level `run` and
        # `[deployment]`'s `run` differ only by that indentation, so normalising
        # it away would make the two indistinguishable and the "exactly one
        # top-level run" assertion count both. Only the trailing comment and
        # trailing space are removed.
        lines.append(raw.rstrip().split(" #", 1)[0].rstrip())
    return lines


def _effective_replit() -> str:
    return "\n".join(_executable_lines(REPLIT_SOURCE))


def _config() -> dict:
    """Parse `.replit`, reporting a parse failure as a test failure.

    A raw `tomllib.TOMLDecodeError` would abort collection and hide the actual
    defect, so it is converted into a normal assertion failure that names the
    line, which is the difference between a fixable report and a stack trace.
    """
    try:
        return tomllib.loads(REPLIT_SOURCE)
    except tomllib.TOMLDecodeError as exc:
        pytest.fail(
            f".replit is not valid TOML: {exc}. Replit cannot load the workspace, "
            "and an unloadable workspace resets to origin/master — so this "
            "invalid file would keep coming back and discarding Replit-side "
            "fixes. See the TOML DISCIPLINE comment at the top of .replit."
        )


CONFIG = _config()


# ---------------------------------------------------------------------------
# A. .replit structure
# ---------------------------------------------------------------------------


def test_replit_parses_and_run_is_toplevel() -> None:
    """`run` must exist and be a top-level string.

    Scoping is the subtlety. A key written *after* a `[table]` header belongs to
    that table, so `run` placed below `[nix]` parses cleanly and produces
    `nix.run` — a file Replit loads and finds no Run command in. Asserting on
    the parsed top level rather than on the text is what catches that.
    """
    assert "run" in CONFIG, (
        ".replit has no top-level `run`. A `run` below a [table] header is "
        "nested into that table, so the file parses while Replit sees no Run."
    )
    assert isinstance(CONFIG["run"], str), (
        f"top-level run must be a string, got {type(CONFIG['run']).__name__}"
    )
    assert "run" not in CONFIG.get("nix", {}), (
        "the development run command is nested inside [nix]; it must be "
        "top-level, above the first table header"
    )


def test_no_duplicate_run_definitions() -> None:
    """No `[[run]]` tables, and `run` defined exactly once.

    This is the specific defect that shipped. `run = "..."` and `[[run]]` are the
    same TOML key, so the second one is a parse error rather than an additional
    command — which is why it is caught at parse time above and again here by
    text, so the message points at the cause.

    Scanned against the effective config, so the comment explaining this defect
    does not read as a second instance of it.
    """
    effective = _effective_replit()
    assert "[[run]]" not in effective, (
        "[[run]] array-of-tables cannot coexist with a top-level `run` string: "
        "both are the same TOML key. Declare the development command as the "
        "top-level `run` and the deployment command under [deployment]."
    )
    # A raw `run =` count is deliberately NOT used to prove uniqueness. TOML lets
    # a key under a table sit at column 0, so `[deployment]`'s `run` is
    # indistinguishable from the top-level one by text alone — counting lines
    # would report two. Uniqueness is enforced by tomllib above, which rejects
    # the file outright if a key is defined twice, and by the absence of `[[run]]`
    # here. The parsed structure is the source of truth; the text is only used to
    # give the parse failure a name and a line.
    tables = re.findall(r"^\s*\[\[run\]\]", effective, flags=re.MULTILINE)
    assert not tables, f"found {len(tables)} [[run]] tables"
    # A `[[run]]` table, if it ever parsed, would surface as a list-of-dicts
    # here. Asserting the *type* of the top-level run is the structural form of
    # "not a table", and it also catches a run that is present but empty.
    assert isinstance(CONFIG.get("run"), str), (
        f"top-level run must be a command string, got "
        f"{type(CONFIG.get('run')).__name__}; a [[run]] table cannot coexist "
        "with it"
    )


def test_deployment_run_targets_web_only() -> None:
    """The deployment starts the web process, and is not a list.

    `run = ["web", "worker"]` asked one deployment to start two long-running
    processes. That is a scaling bug, not a config error: the worker would
    replicate with the web deployment, and one job consumer per replica is
    precisely what the separate worker deployment exists to prevent.
    """
    deployment = CONFIG.get("deployment", {})
    assert "run" in deployment, ".replit has no [deployment] run"
    assert not isinstance(deployment["run"], list), (
        f"deployment run must name one process, got list {deployment['run']!r}. "
        "The worker is a separate deployment with its own run command."
    )
    run = deployment["run"]
    assert "start_replit_web.sh" in run, f"deployment run must target the web process, got {run!r}"
    assert "worker" not in run, f"the web deployment must not also start the worker, got {run!r}"


def test_development_run_uses_the_dev_launcher() -> None:
    """The workspace Run button must start the development launcher.

    A development command that inlined uvicorn alone was wrong twice over: it
    served no frontend, so there was no preview URL to open, and it diverged
    from the launcher that had already been proven in the workspace.
    """
    run = CONFIG["run"]
    assert "start_replit_dev.sh" in run, (
        f"the development run must use the dev launcher, got {run!r}"
    )
    launcher = REPO_ROOT / "scripts" / "start_replit_dev.sh"
    assert launcher.exists(), f"{run!r} points at scripts/start_replit_dev.sh, which does not exist"


def test_python_and_node_come_from_runtime_modules() -> None:
    """Modules use the hyphenated spellings, and cover Python 3.12 and Node 22."""
    modules = CONFIG.get("modules", [])
    assert WRONG_PYTHON_MODULE not in modules, (
        f"`{WRONG_PYTHON_MODULE}` is not a Replit module name; use "
        "`python-3.12`. A name that does not resolve leaves the project without "
        "the interpreter the application requires."
    )
    missing = RIGHT_PYTHON_MODULES - set(modules)
    assert not missing, f"missing runtime module(s): {sorted(missing)}"


# ---------------------------------------------------------------------------
# C. System dependencies
# ---------------------------------------------------------------------------


def test_replit_nix_is_not_present() -> None:
    """System dependencies live in `.replit` `[nix]`, not a second file.

    Two files declaring the same system dependencies is two sources of truth.
    That is how invalid attributes were committed in the first place:
    plausible-looking names in a file that nothing validated, while the form
    Replit's own tooling had proven was the one that works.
    """
    assert not REPLIT_NIX.exists(), (
        "replit.nix is superseded by the [nix] array in .replit. Keep system "
        "dependencies in one place; two files drift, and the drifting one is "
        "the one that breaks the workspace."
    )


def test_no_invalid_nix_attributes_anywhere() -> None:
    """The attributes the live environment rejected must not reappear."""
    effective = _effective_replit()
    for attribute, reason in INVALID_NIX_ATTRIBUTES.items():
        assert attribute not in effective, (
            f"{attribute} is not available in the current Replit channel. {reason}"
        )


def test_system_dependencies_are_listed_in_replit() -> None:
    """The `[nix]` array carries system packages, and not the runtimes.

    Redundant runtime declarations are the specific risk: Python and Node are
    already supplied by `modules`, so repeating them can leave two sources
    disagreeing with the later one winning silently.
    """
    nix = CONFIG.get("nix", {})
    assert "array" in nix, ".replit has no [nix] array of system dependencies"
    packages = nix["array"]
    assert packages, "[nix].array is empty"
    for runtime in ("python", "python3", "python312", "nodejs", "node"):
        assert not any(p == runtime or p.startswith(f"{runtime}-3") for p in packages), (
            f"{runtime!r} in [nix].array is redundant: the runtime is supplied "
            "by the modules list. Two declarations of the same runtime can "
            "disagree, and the disagreement is silent."
        )
    # bash is load-bearing, not decoration: all three start scripts are bash.
    assert "bash" in packages, "[nix].array must include bash for the start scripts"


def test_system_dependencies_cover_the_documented_candidates() -> None:
    """Every package present must be one the app actually needs.

    A whitelist rather than a requirement list, so adding a genuinely required
    package is a deliberate act — the failure mode being fixed here was
    plausible-looking names nobody needed.
    """
    allowed = {
        "bash",  # the start scripts are bash
        "postgresql_16",  # psql for the manual pgvector check
        "pkg-config",  # headers, when a wheel is unavailable
        "openssl",  # ditto
        "cacert",  # TLS roots for outbound https
    }
    unexpected = set(CONFIG["nix"]["array"]) - allowed
    assert not unexpected, (
        f"unexpected [nix] entries: {sorted(unexpected)}; allowed: {sorted(allowed)}"
    )


# ---------------------------------------------------------------------------
# B. Development launcher
# ---------------------------------------------------------------------------


def _dev_launcher() -> str:
    return (REPO_ROOT / "scripts" / "start_replit_dev.sh").read_text(encoding="utf-8")


def _effective_dev_launcher() -> str:
    return "\n".join(_executable_lines(_dev_launcher()))


@pytest.mark.parametrize(
    "forbidden,label",
    [
        ("preflight_production_env", "the production environment gate"),
        ("run_migrations", "the migration step"),
        ("alembic", "the migration tool"),
        ("upgrade head", "a schema upgrade"),
    ],
)
def test_dev_launcher_runs_nothing_production_only(forbidden: str, label: str) -> None:
    """No preflight and no migrations on the development path.

    A migration here would run against whatever DATABASE_URL is present, which
    in a workspace may be a shared or an operator's database. A preflight would
    require production credentials that do not exist in a fresh workspace, so
    the preview could not start at all.
    """
    executed = _executable_lines(_dev_launcher())
    hits = [line for line in executed if forbidden in line]
    assert not hits, f"the dev launcher invokes {label}: {hits}"


def test_dev_launcher_requires_no_production_secrets() -> None:
    """It must not *read* a secret just to decide to start.

    Checking that no `${ENCRYPTION_KEYS}`-style reference is required for the
    start decision, beyond the BACKEND_API_URL it constructs itself.
    """
    effective = _effective_dev_launcher()
    for secret in ("ENCRYPTION_KEYS", "OPENAI_API_KEY", "DATABASE_URL", "AUTH_MODE"):
        assert secret not in effective, (
            f"the dev launcher references {secret}; development must not depend "
            "on a production secret being present"
        )


def test_dev_launcher_binds_api_to_loopback_and_frontend_to_public_port() -> None:
    """The preview reproduces the deployment's origin shape.

    FastAPI on loopback, Next.js on the platform port: a developer who previews
    against a directly-exposed API learns a habit the deployment does not
    support, and the difference only surfaces after deploying.
    """
    text = _effective_dev_launcher()
    assert "--host 127.0.0.1" in text, "the API must bind loopback in development"
    assert "--host 0.0.0.0" not in text, (
        "the API must not bind all interfaces; only the frontend is published"
    )
    assert "HOSTNAME=0.0.0.0" in text, "the frontend must bind the published port"
    assert "BACKEND_API_URL" in text, "the BFF must be pointed at the loopback API explicitly"


def test_dev_launcher_prefers_venv_then_falls_back_to_system_python() -> None:
    """System Python 3.12 when there is no .venv; a prepared .venv wins."""
    text = _effective_dev_launcher()
    assert ".venv/bin/python" in text, "a prepared .venv must be preferred"
    assert "python3.12" in text, (
        "must fall back to the system python3.12 supplied by the runtime module"
    )
    # Matched as `python3 -m venv` / `python -m venv` / `python3.12 -m venv`,
    # with a word boundary after the interpreter so a `venv` inside another
    # token cannot trigger it. A literal "python -m venv" test misses
    # `python3 -m venv`, which is the spelling that actually appears in scripts.
    assert not re.search(r"\bpython[\d.]*\s+-m\s+venv\b", text), (
        "the dev launcher must not create a venv; that is the production "
        "scripts' job and it makes a cold workspace start slow"
    )


def test_dev_launcher_reaps_children_and_handles_signals() -> None:
    """No orphans: TERM/INT handled, and every child waited on."""
    text = _effective_dev_launcher()
    for required in ("trap 'on_signal TERM' TERM", "trap 'on_signal INT' INT", "wait "):
        assert required in text, f"the dev launcher is missing: {required}"
    # The kill loop is written as a single `for pid in "$api_pid" "$web_pid"`
    # over both, so assert that both variables are supervised rather than
    # looking for two separate kill statements — asserting on a literal
    # 'kill -TERM "$pid"' inside the loop would pass even if only one variable
    # were ever signalled.
    assert 'for pid in "$api_pid" "$web_pid"' in text, (
        "both children must be signalled by one loop over both pids"
    )
    for pid_var in ("api_pid", "web_pid"):
        assert pid_var in text, f"{pid_var} is not supervised"


def test_dev_launcher_does_not_start_the_frontend_through_npm() -> None:
    """The frontend child must be `next` itself, not an npm wrapper.

    `npm run dev &` makes `$!` the npm process, and npm runs next as a
    grandchild. Signalling `$!` then kills npm and leaves the real next process
    alive, still holding the published port — an orphan that presents as a
    preview which will not restart after a stop, and which a static review of
    `terminate()` would not catch. Verified by probe before this assertion
    existed, not assumed: killing the wrapper left two orphaned children.

    So next is started through `node .../next/dist/bin/next`, making the
    supervised PID the process that actually serves the port. Same reasoning as
    the production launcher starting `.next/standalone/server.js` via node.
    """
    text = _effective_dev_launcher()
    assert "npm run dev" not in text, (
        "`npm run dev &` supervises the npm wrapper, not next; killing it "
        "orphans the real next process holding PORT. Start next via node."
    )
    assert "next/dist/bin/next" in text, (
        "next must be started through node so the supervised pid is the "
        "process that binds the published port"
    )


def test_dev_launcher_is_syntactically_valid_bash() -> None:
    """`bash -n` on the committed launcher.

    Part of the test suite rather than a manual step, because a syntax error in
    the one command the workspace Run button executes is a workspace that
    cannot start — the exact symptom this remediation exists to prevent.
    """
    import subprocess

    result = subprocess.run(  # noqa: S603
        ["bash", "-n", str(REPO_ROOT / "scripts" / "start_replit_dev.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


# ---------------------------------------------------------------------------
# GitHub is the source of truth
# ---------------------------------------------------------------------------


def test_replit_config_is_tracked_and_current() -> None:
    """`.replit` must be committed, because a reset restores the committed copy.

    This is the source-of-truth invariant in one assertion. An untracked
    `.replit` means the file Replit keeps resetting to is not the file anyone
    has been editing — the exact loop that produced this bug.
    """
    import subprocess

    tracked = subprocess.run(  # noqa: S603
        ["git", "ls-files", "--error-unmatch", ".replit"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, (
        ".replit is not tracked by git. A workspace reset restores the committed "
        "version, so an untracked .replit makes GitHub the wrong source of truth."
    )


def test_no_deployment_or_tenant_data_is_defined_in_replit() -> None:
    """`.replit` configures the runtime, never the data.

    Keeps the blast radius of this file honest: it is about how the app starts,
    and nothing about tenants, credentials, or provisioned resources belongs in
    it — those live in config/tenants and in Replit Secrets respectively.
    """
    effective = _effective_replit()
    for forbidden in ("widget_key", "ENCRYPTION_KEYS=", "OPENAI_API_KEY=", "organizations"):
        assert forbidden not in effective, (
            f".replit must not contain {forbidden!r}; configuration of the "
            "runtime and configuration of the data are separate concerns"
        )
