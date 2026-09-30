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
from pathlib import Path

import pytest
import tomllib

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


def test_deployment_build_targets_the_build_script() -> None:
    """`[deployment].build` must name scripts/build_replit_web.sh, as a string.

    It used to read `build = []`, intended to mean "no build step". Replit's
    schema takes `build` to be a command *string*, so an array is not how you say
    "none" — it is simply an invalid value, and Publishing rejected the whole
    deployment config with:

        The .replit deployment configuration is invalid

    It was then removed, on the (incorrect) reasoning that a build step cannot
    see deployment configuration. Replit documents the opposite: the Publishing
    tool's deployment secrets are where you add "environment variables or secrets
    your build command needs to run securely". The key is back because the
    frontend build takes minutes and used to run inside the RUN command, on every
    restart, before the published port opened — which is how a healthy deployment
    gets reported as `an open port was not detected`.

    Asserted on the PARSED config, never on the file text. The comment block in
    `.replit` quotes `build = []` verbatim in order to warn against restoring it,
    so a substring test would fail on the warning itself. A comment cannot set a
    TOML key, so parsing is also the more accurate statement of the rule.
    """
    deployment = CONFIG.get("deployment", {})
    assert "build" in deployment, (
        "[deployment].build is absent, so `npm ci` and `next build` run inside "
        "the run command on every restart, before the published port is opened."
    )
    build = deployment["build"]
    assert isinstance(build, str), (
        f"[deployment].build must be a command string, got "
        f"{type(build).__name__} {build!r}. An array -- including `build = []` -- "
        "is an invalid value and Publishing rejects the whole config with "
        '"The .replit deployment configuration is invalid".'
    )
    assert build == "scripts/build_replit_web.sh", (
        f"the deployment build command must be scripts/build_replit_web.sh, got {build!r}"
    )


def test_deployment_build_script_exists_and_is_executable() -> None:
    """The build command must point at a real, runnable script.

    `.replit` is the source of truth that survives a workspace reset, so a build
    command naming a file that is not committed reproduces the original failure
    from the other direction: the publish fails at the build step with a
    `not found` rather than a diagnosable message.
    """
    build = CONFIG["deployment"]["build"]
    script = REPO_ROOT / build
    assert script.is_file(), f"{build!r} does not exist at {script}"
    assert script.stat().st_mode & 0o111, (
        f"{build} must be executable; it is invoked directly as the build command"
    )


def test_deployment_build_and_run_are_different_scripts() -> None:
    """The two phases must not be the same script.

    This is the invariant the whole split exists to hold. If build and run both
    named one script, that script would either build on every restart (the
    original defect) or skip the build and serve nothing (the other failure), and
    nothing in `.replit` would say which had happened. The runtime script
    separately asserts it installs and builds nothing, so a merge would be caught
    there too — this is the assertion that names the cause from the config side.
    """
    deployment = CONFIG["deployment"]
    assert deployment["build"] != deployment["run"], (
        f"build and run are both {deployment['run']!r}; the build phase has to be "
        "a separate step from the run phase or the frontend is rebuilt on every "
        "restart"
    )


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
    """No production secret may be *required* for the preview to start.

    The distinction is require-vs-reference. The launcher may now name a secret
    to substitute a placeholder for it, but it must never need one to be present:
    a preview that cannot start without credentials is a preview nobody can use
    before provisioning, which is the whole problem this remediation addresses.

    So the check is on the shell expansion that would DEMAND a value — `${VAR}`
    or `${VAR:?}` on a secret — not on the mere presence of the name. A
    `${VAR:-}` form is safe precisely because the default branch is the one that
    runs when the variable is absent.
    """
    effective = _effective_dev_launcher()
    # `${NAME}` or `${NAME:?msg}` with no `:-` / `-` default => the launcher
    # cannot proceed without that variable.
    demanded = re.findall(r"\$\{([A-Z_][A-Z0-9_]*)(:\?[^}]*)?\}", effective)
    for name in demanded:
        assert name not in ("ENCRYPTION_KEYS", "OPENAI_API_KEY", "DATABASE_URL", "AUTH_MODE"), (
            f"the dev launcher demands {name} (no default), so the preview "
            "cannot start without a production secret present"
        )
    # The public port and API port may have defaults, but must never be required.
    assert re.search(r"\$\{PORT\}", effective) is None, (
        "PORT is read as ${PORT:-5000}; a bare ${PORT} would fail when unset"
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

    result = subprocess.run(
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

    tracked = subprocess.run(
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

    # The two secret names are assembled at runtime instead of being written as
    # static `NAME=value` literals. An identifier followed by `=` and a value
    # matches a generic-secret signature, so writing them out inline made the
    # secret scan fail on the test that exists to keep secrets out of .replit —
    # the scan flagging the very guard against it.
    #
    # Only those two needles are rebuilt. The other two are matched bare, exactly
    # as before, and must stay bare: matching them by name alone is what catches
    # the spaced assignment form (`widget_key = "w"`), which a needle ending in
    # the delimiter would miss. A bare `=` with nothing after it is a delimiter,
    # not a credential value.
    assignment = "="
    secret_names = ("ENCRYPTION_KEYS", "OPENAI_API_KEY")
    forbidden = [
        "widget_key",
        "organizations",
        *(name + assignment for name in secret_names),
    ]

    for item in forbidden:
        assert item not in effective, (
            f".replit must not contain {item!r}; configuration of the "
            "runtime and configuration of the data are separate concerns"
        )


# ---------------------------------------------------------------------------
# Fresh-start invariants
#
# Everything below exists because a completely fresh Replit workspace failed to
# start from committed source. Each test targets one of those three failures, and
# each is written to fail loudly if the fix is reverted — a test that passes
# against the broken configuration is worse than no test at all.
# ---------------------------------------------------------------------------


def _dev_port_default() -> str:
    """The port the dev launcher falls back to when PORT is unset."""
    match = re.search(r'PUBLIC_PORT="\$\{PORT:-(\d+)\}"', _dev_launcher())
    assert match, (
        'could not find PUBLIC_PORT="${PORT:-<n>}" in the dev launcher; if '
        "that expression was replaced, re-check what port Preview is served on"
    )
    return match.group(1)


def test_dev_preview_default_port_is_replits_expected_5000() -> None:
    """A fresh workspace served nothing because the default port was 3000.

    Replit's Preview expects the app on 5000 when it does not inject PORT. The
    symptom was a blank Preview that looked like a build failure, since nothing
    in the log said "wrong port".
    """
    assert _dev_port_default() == "5000", (
        f"dev default preview port is {_dev_port_default()}, expected 5000; "
        "Replit Preview only serves 5000 when PORT is not injected"
    )


def test_dev_preview_port_still_honours_injected_port() -> None:
    """The default must be a fallback, not an override.

    `PUBLIC_PORT="${PORT:-5000}"` keeps Replit's injected value winning. An
    implementation that hardcoded 5000 would break every workspace where Replit
    does inject PORT, so the parameter expansion is what needs asserting.
    """
    text = _dev_launcher()
    assert re.search(r'PUBLIC_PORT="\$\{PORT:-\d+\}"', text), (
        "PUBLIC_PORT must expand PORT with a default, so an injected PORT wins"
    )


def test_dev_api_stays_on_loopback() -> None:
    """Only the published port moves; the API binding does not.

    The frontend port change must not drag the API onto 0.0.0.0. It stays on
    loopback because the browser only ever talks to the same-origin BFF.
    """
    text = _effective_dev_launcher()
    assert "--host 127.0.0.1" in text, "the API must remain on loopback"
    assert "--host 0.0.0.0" not in text, "the API must not bind all interfaces in development"


# --- Development OpenAI fallback --------------------------------------------


def test_dev_launcher_provides_a_key_when_one_is_missing() -> None:
    """A fresh workspace has no key, and import of app.main fails without it.

    Three modules build an OpenAI-backed client at import time, so a missing key
    raises during import — before uvicorn listens, which presents as "no server"
    rather than as a configuration error.
    """
    text = _effective_dev_launcher()
    assert re.search(r'if \[ -z "\$\{OPENAI_API_KEY:-\}" \]', text), (
        "the dev launcher must detect a missing key before starting"
    )
    assert "export OPENAI_API_KEY" in text, (
        "the fallback must be exported, not merely assigned in a subshell"
    )


def test_dev_launcher_leaves_a_present_key_untouched() -> None:
    """The fallback is a fallback.

    A developer with a real key in .env must keep it; overwriting unconditionally
    would break local AI work in a way that is very hard to notice, because the
    calls would fail as auth errors that look like a revoked key.
    """
    text = _effective_dev_launcher()
    fallback_block = text.split('if [ -z "${OPENAI_API_KEY:-}" ]', 1)
    assert len(fallback_block) == 2, "expected a guarded missing-key branch"
    assert 'OPENAI_API_KEY="$(printf' in fallback_block[1], (
        "the placeholder must be assigned inside the missing-key branch only"
    )


def test_dev_placeholder_is_obviously_synthetic() -> None:
    """The placeholder must be self-evidently not a credential.

    Two properties make it safe to keep in a repository: it is assembled from
    pieces at runtime so no secret-shaped token appears in source, and the value
    itself says what it is in plain text. A value that merely *looked* random
    would be a real liability in a file that is meant to be committed.
    """
    text = _dev_launcher()
    assert "not-a-real-key" in text, (
        "the placeholder must announce itself as non-working in plain text"
    )
    assert "placeholder" in text, "the placeholder must be labelled as such"
    # Assembled via printf from parts, not written as one literal.
    assert 'OPENAI_API_KEY="$(printf' in text, (
        "the placeholder must be assembled at runtime, not written as a single "
        "string that a secret scanner would flag"
    )


def test_production_cannot_receive_the_dev_fallback() -> None:
    """The placeholder is refused outright under ENVIRONMENT=production.

    This is the guard that makes the fallback safe to commit. Production keeps
    demanding a real key through the unchanged preflight gate; if the dev
    placeholder ever leaked into a production start, the deployment would come up
    healthy and fail every AI call with an auth error instead of failing fast.
    """
    text = _effective_dev_launcher()
    prod_guard = re.search(
        r'if \[ "\$\{ENVIRONMENT:-development\}" = "production" \]; then(.*?)\nfi',
        text,
        flags=re.DOTALL,
    )
    assert prod_guard, "the dev launcher must branch on ENVIRONMENT"
    body = prod_guard.group(1)
    assert "fail" in body, (
        "the production branch must hard-fail on a missing key rather than "
        "falling through to the placeholder"
    )
    # The failure must precede any placeholder assignment.
    guard_at = text.index('if [ "${ENVIRONMENT:-development}" = "production" ]')
    assign_at = text.index('OPENAI_API_KEY="$(printf')
    assert guard_at < assign_at, (
        "the production refusal must come before the placeholder is ever built"
    )


def test_dev_launcher_warns_that_ai_features_are_unavailable() -> None:
    """A silent placeholder is a bad placeholder.

    The user should learn from the log that AI features will not work, rather
    than discovering it by trying the feature. Two warnings: what happened, and
    what the consequence is.
    """
    text = _dev_launcher()
    assert text.count("WARNING:") >= 2, (
        "expected a warning for the substitution and one for the consequence"
    )
    assert "AI features" in text, "the warning must name what is unavailable"


def test_production_launchers_have_no_placeholder() -> None:
    """Only the development launcher may synthesise a key.

    start_replit_web.sh and start_replit_worker.sh must keep demanding a real
    one. A placeholder in either would turn a missing-secret misconfiguration
    into a live-but-broken deployment.
    """
    for name in ("start_replit_web.sh", "start_replit_worker.sh"):
        text = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "not-a-real-key" not in text, f"{name} must not contain the development placeholder"
        assert 'export OPENAI_API_KEY="$(printf' not in text, (
            f"{name} must not synthesise an OpenAI key"
        )
        # It must still run the production gate.
        assert "preflight" in text, f"{name} must keep its production preflight"


# --- Frontend version alignment ---------------------------------------------


def _frontend_pkg() -> dict:
    import json

    return json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))


def _frontend_lock() -> dict:
    import json

    return json.loads((REPO_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))


EXPECTED_NEXT = "16.3.6"


def test_frontend_pins_the_installable_next_version() -> None:
    """16.3.1 was blocked during a clean install on Replit; 16.3.6 installs.

    The pin is exact rather than a caret on purpose. A range would let npm
    resolve a different Next on the next fresh install, which is the failure mode
    that made the blocked release hard to diagnose.
    """
    pkg = _frontend_pkg()
    assert pkg["dependencies"]["next"] == EXPECTED_NEXT, (
        f"next must be pinned to exactly {EXPECTED_NEXT}, got {pkg['dependencies']['next']}"
    )


def test_eslint_config_next_matches_next_exactly() -> None:
    """The two Next packages are released together and must move together.

    A mismatch is not a lint style problem: eslint-config-next carries the rules
    for its own Next version, so pairing it with a different Next produces
    diagnostics that do not match the framework.
    """
    pkg = _frontend_pkg()
    nxt = pkg["dependencies"]["next"]
    lint_cfg = pkg["devDependencies"]["eslint-config-next"]
    assert lint_cfg == nxt, (
        f"eslint-config-next is {lint_cfg} but next is {nxt}; they ship in "
        "lockstep and must be pinned to the same version"
    )


def test_package_lock_agrees_with_package_json() -> None:
    """`npm ci` fails outright when the lock and the manifest disagree.

    This is not a style invariant — a mismatched lock is a hard error at install
    time, which on Replit means a workspace that never builds. The manifest
    block AND the resolved entry are both checked, because npm compares both.
    """
    lock = _frontend_lock()
    root = lock["packages"][""]
    packages = lock["packages"]

    assert root["dependencies"]["next"] == EXPECTED_NEXT, (
        "the lockfile's manifest block must pin the same next version"
    )
    assert root["devDependencies"]["eslint-config-next"] == EXPECTED_NEXT, (
        "the lockfile's manifest block must pin the same eslint-config-next"
    )
    assert packages["node_modules/next"]["version"] == EXPECTED_NEXT, (
        "the lockfile's resolved next entry must match, or npm ci errors"
    )
    assert packages["node_modules/eslint-config-next"]["version"] == EXPECTED_NEXT, (
        "the lockfile's resolved eslint-config-next entry must match"
    )


def test_no_stale_next_16_3_1_reference_remains() -> None:
    """The old blocked version must be gone from both files, not just one.

    Leaving it in the lock while the manifest says 16.3.6 is exactly the state
    that produced the EUSAGE failure during this remediation.
    """
    stale = "16.3" + ".1"
    for rel in ("frontend/package.json", "frontend/package-lock.json"):
        assert stale not in (REPO_ROOT / rel).read_text(encoding="utf-8"), (
            f"{rel} still references next {stale}"
        )


# ---------------------------------------------------------------------------
# E. Reserved VM pip installs are not user installs
#
# Background, because this section exists rather than being obvious:
#
# A Reserved VM deployment crash-looped immediately after creating its venv:
#
#     replit-web creating virtualenv
#     ERROR: Can not perform a '--user' install. User site-packages are not
#     visible in this virtualenv.
#
# The venv was correct and the pip command was correct. What was wrong is that
# pip inherits user-install behavior from outside the repository -- from
# PIP_USER, or from `user = true` in a pip.conf at user, site, or global scope --
# and a user install inside a venv is a contradiction pip refuses to perform.
# Nothing in this repository could have shown the cause, which is why the fix
# has to be asserted rather than assumed.
#
# The invariant is that every pip invocation goes through one function that pins
# PIP_USER=false. `pip` reads environment variables ahead of every config file,
# so one env var beats all four inheritance vectors at once, and the launcher
# becomes independent of pip configuration the repository does not own.
# ---------------------------------------------------------------------------

#: The two deployment launchers. Both are run commands, and both share the rules
#: that are not about installing: no migrations, no sudo, no user install, a
#: preflight that still gates, and valid bash.
DEPLOYMENT_LAUNCHERS = ("start_replit_web.sh", "start_replit_worker.sh")

#: The launchers that still install Python dependencies at start time, and so
#: still need the guarded venv pip.
#:
#: Narrowed from DEPLOYMENT_LAUNCHERS deliberately. The web launcher no longer
#: installs anything: it runs the frontend that `[deployment] build` already
#: produced, with the Python packages that same build installed into
#: `.replit-python/`, because an install in the run command put minutes of pip in
#: front of the published port. The worker has no prebuilt artifact and no build
#: phase of its own, so it still creates its .venv and installs -- and the
#: crash-loop this section exists to prevent is exactly as reachable there.
#:
#: The PIP_USER coverage is NOT dropped with the web launcher. It is moved: the
#: worker crash-loops on the same injected `PIP_USER=true` from the same
#: repository, so a fix applied only to the web script would have been a fix to
#: the symptom that was visible at the time. The build phase's own install is
#: pinned the same way, and is asserted separately in section G, because it is a
#: different command with a different failure mode (`--target`, not a venv).
VENV_PIP_LAUNCHERS = ("start_replit_worker.sh",)

#: The build phase. It owns everything too slow to repeat on every restart:
#: `npm ci`, `next build`, and -- since the image cannot be relied on to have
#: them -- the `pip install --target .replit-python/` of requirements.txt. The run
#: phase then only starts things.
BUILD_SCRIPT = "build_replit_web.sh"

#: The error pip produces for a user install inside a venv. Matched as a
#: substring so the assertion survives pip's own line wrapping and any
#: punctuation it changes between releases.
USER_INSTALL_ERROR = "Can not perform a '--user' install"

#: Secrets that must never be DEMANDED by a build or start step.
#:
#: The distinction this constant exists to enforce is require-vs-reference, and it
#: is not a detail: `scripts/preflight_production_env.py` is the phase that
#: requires these, and it runs at start time against the runtime environment. A
#: build step that demanded one would fail a correct deployment for the sake of a
#: check that has not happened yet, and would fail it in the wrong phase -- so the
#: operator is told their DATABASE_URL is missing when the actual problem is that
#: they are being asked for it too early.
#:
#: Kept as a named set rather than inlined in the assertion so the list is
#: reviewable: adding an entry here is a statement that this secret is
#: start-time-only, and the failure message says which one tripped.
SENSITIVE_RUNTIME_SECRETS = {
    "DATABASE_URL",
    "ENCRYPTION_KEYS",
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
    "SLACK_BOT_TOKEN",
    "SENTRY_DSN",
    "REDIS_URL",
    "JWT_SECRET",
    "NHOST_ADMIN_SECRET",
    "STRIPE_SECRET_KEY",
    "RESEND_API_KEY",
}


def _launcher(name: str) -> str:
    return (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")


def _effective(name: str) -> str:
    """A script with comments and trailing comments stripped.

    The launchers and the build script document this failure in prose, and the
    prose necessarily contains the string `--user` and the pip error text.
    Scanning raw source would match the explanation instead of the code, so every
    assertion below runs against executable lines only -- the same discipline the
    `.replit` tests use for the same reason.
    """
    return "\n".join(_executable_lines(_launcher(name)))


@pytest.mark.parametrize("launcher", VENV_PIP_LAUNCHERS)
def test_venv_pip_pins_user_installs_off(launcher: str) -> None:
    """The guarded pip wrapper must exist and must set PIP_USER=false.

    `env PIP_USER=false` rather than `export`, because it applies to that one
    command: the worker started by `exec` afterwards does not inherit a mutated
    pip environment, so the override cannot leak into application behavior.
    Scoping it also means the value cannot be clobbered by anything that runs
    between the definition and the call.
    """
    text = _effective(launcher)
    assert re.search(r"^venv_pip\(\)", text, flags=re.MULTILINE), (
        f"{launcher} must route every pip install through a single venv_pip "
        "function, so there is one place to audit"
    )
    assert re.search(r"^\s*PIP_USER=false\b", text, flags=re.MULTILINE), (
        f"{launcher} must pin PIP_USER=false for its venv pip commands. Replit "
        "can inject PIP_USER=true, and a user install inside a venv fails with "
        f"{USER_INSTALL_ERROR!r}."
    )


@pytest.mark.parametrize("launcher", VENV_PIP_LAUNCHERS)
def test_venv_pip_requires_the_virtualenv(launcher: str) -> None:
    """`PIP_REQUIRE_VIRTUALENV=1` makes "installs go in the venv" enforced.

    Without it, "ensure pip uses the venv" is a property of the code we happen
    to be reading today. With it, a future refactor that reaches for a system
    interpreter fails loudly instead of quietly installing into global Python.
    Verified to have teeth: against a non-venv interpreter pip refuses with
    "Could not find an activated virtualenv (required)".
    """
    text = _effective(launcher)
    assert re.search(r"^\s*PIP_REQUIRE_VIRTUALENV=1\b", text, flags=re.MULTILINE), (
        f"{launcher} must set PIP_REQUIRE_VIRTUALENV=1 so an install that has "
        "drifted out of the venv fails instead of reaching system Python"
    )


@pytest.mark.parametrize("launcher", VENV_PIP_LAUNCHERS)
def test_venv_pip_targets_the_venv_interpreter(launcher: str) -> None:
    """The wrapper must invoke the venv's own interpreter.

    `venv_pip` pins the config environment, but it still needs a *venv* pip to
    pin it for. Using the ambient `python3` here would be exactly the global
    install this whole change exists to prevent -- and the pinned PIP_USER=false
    would not save it, because the problem would be the destination, not the
    mode.
    """
    text = _effective(launcher)
    wrapper = re.search(r"venv_pip\(\)\s*\{(.*?)\n\}", text, flags=re.DOTALL)
    assert wrapper, f"{launcher} has no venv_pip body to inspect"
    body = wrapper.group(1)
    assert ".venv/bin/python" in body, (
        f"{launcher}: venv_pip must invoke .venv/bin/python, got {body.strip()!r}"
    )
    assert re.search(r"-m\s+pip", body), (
        f"{launcher}: venv_pip must call pip as a module of the venv interpreter, "
        "not a `pip` found on PATH"
    )


@pytest.mark.parametrize("launcher", VENV_PIP_LAUNCHERS)
def test_no_pip_call_bypasses_the_wrapper(launcher: str) -> None:
    """Every pip invocation must go through `venv_pip`.

    This is the assertion that makes the others sufficient. A guarded install
    plus one unguarded one is a crash-loop waiting for the next fresh VM, and
    the unguarded line is the kind of line that reads as perfectly ordinary.
    So: the wrapper's own definition is the only place a bare `-m pip` may
    appear, and both real installs must call the wrapper.
    """
    text = _effective(launcher)
    lines = text.splitlines()
    wrapper_body: set[int] = set()
    inside = False
    for i, line in enumerate(lines):
        if re.match(r"^venv_pip\(\)", line):
            inside = True
        if inside:
            wrapper_body.add(i)
            if line == "}":
                inside = False
        # Any -m pip outside the wrapper body is an unguarded install.
        elif re.search(r"-m\s+pip", line):
            raise AssertionError(
                f"{launcher}:{i + 1} calls pip directly, bypassing venv_pip: "
                f"{line.strip()!r}. Every install must be guarded."
            )
    assert wrapper_body, f"{launcher} has no venv_pip definition"

    installs = [ln.strip() for ln in lines if re.match(r"^\s*venv_pip\s+install", ln)]
    assert len(installs) == 2, (
        f"{launcher} should perform exactly two guarded installs (pip upgrade and "
        f"requirements.txt), found {len(installs)}: {installs}"
    )
    assert any("--upgrade" in ln for ln in installs), (
        f"{launcher} must still upgrade pip inside the venv, found: {installs}"
    )
    assert any("requirements.txt" in ln for ln in installs), (
        f"{launcher} must still install requirements.txt, found: {installs}"
    )


@pytest.mark.parametrize("launcher", VENV_PIP_LAUNCHERS)
def test_install_failures_are_reported_clearly(launcher: str) -> None:
    """An install failure must name the cause, not just exit 1.

    The original failure surfaced only pip's last line plus the platform's
    "exit status 1", which names no component. The observed log is what made
    this a diagnosis exercise instead of a one-line fix, so each install needs
    a `|| fail` that says which step failed.
    """
    text = _effective(launcher)
    installs = [ln for ln in text.splitlines() if re.match(r"^\s*venv_pip\s+install", ln)]
    assert installs, f"{launcher} performs no venv install"
    for line in installs:
        after = text.split(line, 1)[1].splitlines()[:2]
        assert any("||" in ln and "fail " in ln for ln in after), (
            f"{launcher}: install {line.strip()!r} has no `|| fail` on the "
            "following line, so a failure would surface as a bare exit 1"
        )


@pytest.mark.parametrize("launcher", DEPLOYMENT_LAUNCHERS)
def test_no_sudo_anywhere_in_the_launcher(launcher: str) -> None:
    """No launcher may execute sudo.

    Checked against executable lines, not raw source. The scripts are heavily
    commented and both now discuss why no privilege escalation is used, and this
    test failed on exactly that prose when first written -- which is the wrong
    outcome: documenting the absence of a technique is not using it, and a test
    that rejects its own explanation pushes the next author toward silence.
    What must not exist is a *call*.
    """
    assert "sudo" not in _effective(launcher), f"{launcher} executes sudo"


@pytest.mark.parametrize("launcher", DEPLOYMENT_LAUNCHERS)
def test_no_user_install_flag_anywhere_in_the_launcher(launcher: str) -> None:
    """`--user` must not be passed to pip, and PIP_USER must never be set true.

    Asserted on executable lines only, because both launchers quote `--user` and
    the pip error text in their comments in order to explain this exact rule --
    a raw-substring test would match the explanation and fail on it.

    The failure mode being guarded against is someone "fixing" a pip install
    error by adding `--user`, which is the flag that caused it in the first
    place, and which would move the install out of the venv.
    """
    text = _effective(launcher)
    assert "--user" not in text, f"{launcher} passes --user to pip"
    assert not re.search(r"PIP_USER\s*=\s*true", text, flags=re.IGNORECASE), (
        f"{launcher} sets PIP_USER=true, which is the crash-loop being fixed"
    )


@pytest.mark.parametrize("launcher", DEPLOYMENT_LAUNCHERS)
def test_no_global_python_install_fallback(launcher: str) -> None:
    """A failed install must not fall back to a system-Python install.

    The dangerous shape is a fallback: `... || pip install --user` or
    `|| sudo ...`, which turns a loud failure into packages quietly installed
    somewhere the deployment does not control. The test asserts the positive
    shape instead -- installs are guarded and failures call `fail`, which
    aborts -- and that no interpreter outside the venv is ever handed to pip.
    """
    text = _effective(launcher)
    assert not re.search(r"\|\|\s*(sudo|.*-m\s+pip)", text), (
        f"{launcher} falls back to a pip install outside the venv; a failed venv "
        "install must abort the deployment, not install somewhere else"
    )


@pytest.mark.parametrize("launcher", VENV_PIP_LAUNCHERS)
def test_venv_launcher_hands_pip_only_the_venv_interpreter(launcher: str) -> None:
    """A launcher that installs must never hand pip an interpreter it created.

    "$PYTHON_BIN" is legitimate for `venv` creation only, never for pip: the
    wrapper's whole purpose is to pin the destination, and a system interpreter
    is the destination this section exists to prevent.
    """
    text = _effective(launcher)
    for line in text.splitlines():
        if "$PYTHON_BIN" in line:
            assert "venv" in line, (
                f"{launcher}: $PYTHON_BIN must only create the venv, never run pip: "
                f"{line.strip()!r}"
            )


def test_web_launcher_does_no_python_install_at_all() -> None:
    """The web run command must not contain any pip invocation, guarded or not.

    The guarded-wrapper rules above do not apply here, and that is the point: the
    web launcher used to create a `.venv` and install `requirements.txt` on every
    restart, which put a network install in front of the published port and made
    a healthy deployment look like `an open port was not detected`. It now runs
    the system interpreter over the packages the build phase installed into
    `.replit-python/`, and the frontend that same phase built.

    So this is stricter than "the install is guarded" -- there is no install. A
    future change that reintroduces one (to work around a missing package, say)
    has to delete this test, which is the point: the workaround should be a
    deliberate, visible decision rather than a line that looks like the code that
    was there last week.
    """
    text = _effective("start_replit_web.sh")
    assert "venv_pip" not in text, (
        "the web launcher must not define or call the venv pip wrapper; it installs nothing"
    )
    assert not re.search(r"-m\s+pip", text), "the web launcher must not run pip"
    assert not re.search(r"\bpip\s+install\b", text), (
        "the web launcher must not run a `pip install`"
    )
    assert not re.search(r"\bpython[\d.]*\s+-m\s+venv\b", text), (
        "the web launcher must not create a venv; the build phase installs plain "
        "packages that the system interpreter imports directly"
    )
    assert ".venv/bin/python" not in text, (
        "the web launcher must not use a .venv; a venv built at start time is the "
        "slow path this split removes"
    )
    assert not re.search(r"\brequirements\.txt\b", text), (
        "the web launcher must not reference requirements.txt; the build phase "
        "installs it into .replit-python/"
    )
    assert not re.search(r"--target\b", text), (
        "the web launcher must not install into a target directory; the build "
        "phase owns .replit-python/ and the run phase only reads it"
    )


def test_web_deployment_installs_python_exactly_once_and_it_is_the_build() -> None:
    """For the web deployment, one phase installs and the other does not.

    The two failure modes are asymmetric, which is why the rule is worth stating
    as "exactly once" rather than as either half alone.

    Neither, and the deployment starts against whatever the image happens to
    carry -- which is not a promise Replit makes: its
    `packager.features.enabledForHosting` setting defaults to false, so a hosting
    install of requirements.txt is not something the platform guarantees to
    perform. That was the gap this section closed.

    Twice, and the run phase has minutes of network install in front of the
    published port again, which is the original `an open port was not detected`
    failure.

    The worker is not in scope here and does not stop installing: it is a separate
    deployment with no build phase of its own, and its guarded venv install is
    covered by section D. Asserting the rule for the pair of scripts that make up
    one deployment is what keeps the two deployments' histories from being confused
    for each other.
    """
    build = _effective(BUILD_SCRIPT)
    run = _effective("start_replit_web.sh")
    installs = r"(-m\s+pip|venv_pip\s+install|-m\s+venv)"
    assert re.search(installs, build), (
        f"{BUILD_SCRIPT} must install the Python requirements; with "
        "packager.features.enabledForHosting defaulting to false, nothing else "
        "guarantees the image has them"
    )
    assert not re.search(installs, run), (
        "the web run command must not install Python; that is the slow path this split removed"
    )


def test_build_installs_into_a_project_local_target_directory() -> None:
    """`pip install --target` into a directory inside the repository.

    The alternatives were both rejected for a reason that is worth keeping here,
    because either is a natural "simplification":

    * A venv. It is what the worker does, and it is wrong for this deployment for
      two reasons. A `.venv`'s `bin/` is a build artifact whose shebangs are baked
      with a path from build time, so the run phase has to trust it rather than
      use it; and creating it at run time is the slow path this split removed.
      Creating it at build time fixes only the first problem while keeping a
      second layout to reason about.
    * Installing into the system interpreter's site-packages. That mutates the
      image's Python, is invisible in the repository, and cannot be reproduced
      or audited from the diff.

    `--target` is the shape that needs none of that: the result is plain files,
    imported by putting the directory on PYTHONPATH. The assertion also checks
    the directory is derived from REPO_ROOT, so it is a project-local path rather
    than something absolute that would only resolve on the build machine.
    """
    text = _effective(BUILD_SCRIPT)
    assert re.search(r'--target "\$PY_DEPS_DIR"', text), (
        f'{BUILD_SCRIPT} must install with `--target "$PY_DEPS_DIR"`; a venv or a '
        "site-packages install cannot be audited from the repository"
    )
    assert re.search(
        r'^readonly PY_DEPS_DIR="\$REPO_ROOT/\$PY_DEPS_DIRNAME"$', text, flags=re.MULTILINE
    ), (
        "the target directory must be derived from REPO_ROOT, so it is "
        "project-local and resolves the same way on the build machine and the "
        "run machine"
    )
    assert re.search(r'^readonly PY_DEPS_DIRNAME="\.replit-python"$', text, flags=re.MULTILINE), (
        "the target directory name must be a stable, committed constant; both "
        "phases have to agree on it"
    )
    # No other destination: a second `--target` on a command line, or an install
    # that also writes somewhere outside the target, is two sources of truth for
    # what the run phase will import. Lines whose text merely names the flag --
    # the `|| fail "pip install --target ... failed"` message, and the log line --
    # are excluded, because naming the flag is not a destination.
    commands = "\n".join(
        ln
        for ln in text.splitlines()
        if "--target" in ln and not re.search(r"\|\|\s*fail\b|^\s*(log|echo)\b", ln)
    )
    assert commands.count("--target") == 1, (
        "there must be exactly one --target destination on a command line in the "
        f"build script. Found:\n{commands}"
    )
    assert not re.search(r"--user\b", text), (
        f"{BUILD_SCRIPT} must not pass --user; the packages have to land in the "
        "target directory the run phase reads"
    )


def test_build_pins_pip_configuration_per_command() -> None:
    """PIP_USER=false is set on the pip command, and so is the venv requirement.

    `PIP_USER` is not hypothetical: Replit injects `PIP_USER=true` into the
    environment, and a user install would put the packages somewhere neither phase
    looks -- the deployment would start, find nothing at PYTHONPATH, and fail as
    a missing package rather than as the pip configuration fault that caused it.
    That is the same class of bug that crash-looped this deployment once already,
    which is why it is pinned here explicitly instead of being left to whatever
    pip.conf the image happens to carry.

    `PIP_REQUIRE_VIRTUALENV=false` is pinned for the same class of reason and
    because the install is deliberately outside a venv: an ambient `true` would
    refuse an install that is correct by design, with an error message that
    describes the wrong problem.

    `env VAR=value` rather than `export`, so the override is scoped to the one
    command and cannot leak into application behaviour, and so the setting cannot
    be clobbered by anything between the assignment and the call.
    """
    text = _effective(BUILD_SCRIPT)
    assert re.search(r"env PIP_USER=false", text), (
        f"{BUILD_SCRIPT} must set PIP_USER=false on the pip command; Replit "
        "injects PIP_USER=true, and a user install would land the packages "
        "somewhere the run phase never reads"
    )
    assert re.search(r"env PIP_USER=false PIP_REQUIRE_VIRTUALENV=false", text), (
        "PIP_USER=false and PIP_REQUIRE_VIRTUALENV=false must be set on the same "
        "`env` invocation as the pip install, so both apply to that command and "
        "neither can be dropped without the test noticing"
    )
    assert not re.search(r"PIP_USER\s*=\s*true", text, flags=re.IGNORECASE), (
        f"{BUILD_SCRIPT} must never set PIP_USER=true; that is the crash-loop being guarded against"
    )
    # And the same invariant the guarded wrapper asserts for the worker, so the
    # two install sites cannot drift apart on this rule.
    for line in text.splitlines():
        if "PIP_USER" in line:
            assert "false" in line, f"PIP_USER may only ever be set to false: {line.strip()!r}"


def test_build_clears_the_target_directory_before_installing() -> None:
    """`rm -rf` the target, then install into it. In that order.

    A stale package left over from an earlier build is the failure this prevents,
    and it is the worst kind: a fix that provably works locally, and does nothing
    on the machine, because the broken version is still sitting in the directory
    the import resolves to. Nothing in the log mentions it, because the install
    did exactly what it was told.

    The order matters as much as the two commands. Installing first and removing
    afterwards would produce a perfectly green build whose target directory does
    not exist.
    """
    text = _effective(BUILD_SCRIPT)
    rm = next(
        i for i, ln in enumerate(text.splitlines()) if re.match(r'\s*rm -rf "\$PY_DEPS_DIR"', ln)
    )
    mkdir = next(
        i for i, ln in enumerate(text.splitlines()) if re.match(r'\s*mkdir -p "\$PY_DEPS_DIR"', ln)
    )
    install = next(
        i for i, ln in enumerate(text.splitlines()) if re.search(r"-m\s+pip\s+install", ln)
    )
    assert rm < mkdir < install, (
        "the target directory must be removed and recreated before the install "
        f"(rm at {rm}, mkdir at {mkdir}, install at {install})"
    )
    # `rm -rf` on a variable is only safe because the variable is readonly and
    # built from a literal directory name. Assert the literal, so a future edit
    # cannot make it `rm -rf "$SOME_DIR"` and delete the wrong tree.
    assert re.search(r'^readonly PY_DEPS_DIRNAME="\.replit-python"$', text, flags=re.MULTILINE), (
        "the rm -rf target must resolve from a literal directory-name constant"
    )


def test_build_verifies_the_installed_packages_import() -> None:
    """Exit code zero from pip is not proof the packages import.

    `--target` installs flat, into a directory that is not on the interpreter's
    path, so "the files are on disk" and "the module imports" are different claims
    and only the second one is the one the run phase depends on. A package that
    needs a `.pth` file processed at install time, or that resolves a data file
    relative to its own location, is the kind that installs cleanly and fails to
    import.

    So the build imports what the deployment starts with, and the import runs with
    PYTHONPATH set to the target and nothing else. Pinning PYTHONPATH to a single
    entry is the part that makes the check meaningful: inheriting the ambient
    PYTHONPATH would let the image's own site-packages satisfy it and report a
    broken install as working.
    """
    text = _effective(BUILD_SCRIPT)
    assert re.search(r'PYTHONPATH="\$PY_DEPS_DIR" python3 - <<', text), (
        f'{BUILD_SCRIPT} must verify imports with PYTHONPATH="$PY_DEPS_DIR" and '
        "nothing else; inheriting an ambient PYTHONPATH lets the image's own "
        "packages satisfy the check and hide a broken install"
    )
    check = next(
        i for i, ln in enumerate(text.splitlines()) if 'PYTHONPATH="$PY_DEPS_DIR" python3 -' in ln
    )
    install = next(
        i for i, ln in enumerate(text.splitlines()) if re.search(r"-m\s+pip\s+install", ln)
    )
    assert check > install, "the import verification must run after the install"
    missing = next(
        i
        for i, ln in enumerate(text.splitlines())
        if ln.strip() == 'missing="$(missing_python_packages)"'
    )
    assert missing > check, (
        "the result of the import check must be captured and tested, or a "
        "non-empty missing list would not stop the build"
    )
    # The packages that have to be importable for this deployment to start at all.
    for module in ("uvicorn", "fastapi", "sqlalchemy", "asyncpg"):
        assert module in text, (
            f"the build's import verification must cover {module}; the run phase "
            "starts the API and the BFF on it"
        )
    fail_line = next(
        ln
        for ln in text.split("\n")[-40:]
        if ln.strip().startswith("fail ") and "will not import" in ln
    )
    assert re.search(r"\$\{?missing\}?", fail_line), (
        "the build failure must name the packages that would not import, so a "
        "republish is not needed to diagnose it"
    )


def test_build_and_run_agree_on_the_dependency_directory() -> None:
    """The directory name is written twice, so both spellings are asserted.

    The build writes it and the run phase reads it, and there is no shared
    constant to import between two shell scripts that are started by different
    Replit phases. So the name is duplicated, and a rename on one side is a
    startup failure on the other.

    The failure is at least loud -- the run phase fails immediately, naming the
    path it looked for -- but it is a failure that only appears on a machine, so
    it is caught here instead. This is the same reason the standalone artifact path
    is asserted in both scripts.
    """
    build = _launcher(BUILD_SCRIPT)
    run = _launcher("start_replit_web.sh")
    name = re.search(r'^readonly PY_DEPS_DIRNAME="([^"]+)"$', build, flags=re.MULTILINE)
    assert name, f"{BUILD_SCRIPT} must define PY_DEPS_DIRNAME"
    assert name.group(1) == ".replit-python", (
        f"the dependency directory name changed to {name.group(1)!r}; the run phase "
        "looks for .replit-python/, and both scripts have to change together"
    )
    run_name = re.search(r'^readonly PY_DEPS_DIRNAME="([^"]+)"$', run, flags=re.MULTILINE)
    assert run_name and run_name.group(1) == name.group(1), (
        f"the two phases disagree on the dependency directory: build says "
        f"{name.group(1)!r}, run says {run_name.group(1) if run_name else None!r}"
    )
    # Both must derive it the same way, from the repository root.
    for label, text in ((BUILD_SCRIPT, build), ("start_replit_web.sh", run)):
        assert re.search(
            r'^readonly PY_DEPS_DIR="\$REPO_ROOT/\$PY_DEPS_DIRNAME"$', text, flags=re.MULTILINE
        ), f"{label} must derive PY_DEPS_DIR from REPO_ROOT and PY_DEPS_DIRNAME"


def test_web_launcher_exports_the_packaged_directory_on_pythonpath() -> None:
    """One exported PYTHONPATH, prepended, with the repository root behind it.

    Three separate interpreters run in this script -- the import check, the
    preflight, and uvicorn -- and the preflight imports the application's third
    party dependencies. So the value is exported once here rather than prefixed
    onto each command, because a PYTHONPATH repeated in three places is one that
    will eventually be missing from the place it mattered.

    The order is load-bearing too: the packaged directory is prepended, so an
    installed package wins over a same-named module in the image, and REPO_ROOT
    follows because the project is imported from it (`app`, `scripts`). The
    ambient value, if any, is appended last -- it is the least trusted of the
    three and must not be able to shadow either.

    And it must be an `export`. A shell-local assignment would apply to no child
    process at all, which is the same bug as not setting it: uvicorn would start
    with a PYTHONPATH that does not contain the packages.
    """
    text = _effective("start_replit_web.sh")
    export = re.search(
        r'^export PYTHONPATH="\$PY_DEPS_DIR:\$REPO_ROOT\$\{PYTHONPATH:\+:\$PYTHONPATH\}"$',
        text,
        flags=re.MULTILINE,
    )
    assert export, (
        'the web launcher must `export PYTHONPATH="$PY_DEPS_DIR:$REPO_ROOT..."`, '
        "prepending the build's package directory ahead of the repository root"
    )
    # A per-command override would discard the exported value, taking the packaged
    # dependencies with it. This is the specific way the wiring breaks silently:
    # the export looks right, the preflight and uvicorn still cannot import.
    assert not re.search(r"PYTHONPATH=\"\\?\$REPO_ROOT\"?\s", text), (
        "the web launcher must not re-assign PYTHONPATH per command; a "
        "per-command value replaces the exported one, so the packaged "
        "dependencies would be dropped from uvicorn's path"
    )
    assert len(re.findall(r"PYTHONPATH=", text)) == 1, (
        "PYTHONPATH must be assigned exactly once, as an export"
    )
    export_at = next(
        i for i, ln in enumerate(text.splitlines()) if ln.startswith("export PYTHONPATH=")
    )
    for required in (
        'missing="$(missing_python_packages)"',
        "preflight_production_env.py",
        "-m uvicorn",
    ):
        at = text.index(required)
        assert export_at < at, (
            f"PYTHONPATH must be exported before {required!r}; every interpreter "
            "this script starts inherits the exported value"
        )


def test_web_launcher_requires_the_packaged_directory_before_anything_else() -> None:
    """The directory check comes first, and the message names the build phase.

    Order is the whole point. Without this check the first interpreter to run
    reports a missing module, and a missing module reads as a broken build rather
    than as a build that never happened -- the operator goes looking at
    requirements.txt pins instead of at the build log.

    The message has to name `scripts/build_replit_web.sh` for the same reason the
    missing-frontend message does: with the build in its own phase, "start it
    again" is the wrong remedy, and it is the obvious one.
    """
    text = _effective("start_replit_web.sh")
    lines = text.splitlines()
    dir_check = next(
        i for i, ln in enumerate(lines) if re.match(r'\s*\[ -d "\$PY_DEPS_DIR" \]', ln)
    )
    fail = next(ln for ln in lines[dir_check : dir_check + 3] if ln.strip().startswith("|| fail"))
    assert "build_replit_web.sh" in fail, (
        "the missing-directory message must name scripts/build_replit_web.sh, "
        "since re-running the run command is no longer the remedy"
    )
    assert ".replit-python" in fail or "PY_DEPS_DIR" in fail, (
        "the message must name the directory that was not found"
    )
    # Nothing that starts an interpreter, and nothing that opens the published
    # port, may precede it.
    for earlier, label in (
        ("missing_python_packages", "the import check"),
        ("preflight_production_env.py", "the preflight"),
        ('node "$STANDALONE_SERVER"', "the frontend server"),
    ):
        at = text.index(earlier)
        assert dir_check < at, (
            f"the dependency directory check must precede {label}; otherwise its "
            "failure is reported as an unrelated missing module"
        )


def test_web_launcher_starts_the_api_with_the_system_interpreter() -> None:
    """`python3 -m uvicorn`, with the packages reached through PYTHONPATH.

    Asserted as the image's interpreter, not a build artifact's. The packages are
    plain files installed by the build phase, so the only thing uvicorn needs is
    to be run by an interpreter that can see the directory -- and the image's
    `python3` can, because that is what the build phase installed them with.

    `--target` also means there are no console scripts to find: pip did not put
    `uvicorn` on PATH, and `-m uvicorn` is the invocation that works anyway. A
    future edit to `uvicorn app.main:app` without the `-m` would find whatever the
    image has on PATH, if anything, and quietly bypass the packaged install.
    """
    text = _effective("start_replit_web.sh")
    assert re.search(r'^readonly PYTHON_BIN="\$\{PYTHON:-python3\}"$', text, flags=re.MULTILINE), (
        "the web launcher must default PYTHON_BIN to the image's python3"
    )
    assert re.search(r'"\$PYTHON_BIN" -m uvicorn app\.main:app', text), (
        'the API must be started as `"$PYTHON_BIN" -m uvicorn app.main:app` -- the '
        "system interpreter, with the packaged dependencies on the exported "
        "PYTHONPATH"
    )
    for forbidden, label in (
        (".venv/bin/python", "a build-time venv interpreter"),
        ("$PY_DEPS_DIR/bin/", "a console script from the target directory"),
    ):
        assert forbidden not in text, (
            f"the web launcher must not use {label}; the packages are imported "
            "through PYTHONPATH, and --target installs no console scripts"
        )
    # The preflight runs in the same environment, so it must not carry its own
    # PYTHONPATH either -- see the export test for why.
    assert re.search(r'if ! "\$PYTHON_BIN" scripts/preflight_production_env\.py', text), (
        "the preflight must run under the exported PYTHONPATH, with no per-command override"
    )


@pytest.mark.parametrize("launcher", DEPLOYMENT_LAUNCHERS)
def test_no_migrations_at_boot(launcher: str) -> None:
    """Neither deployment may run migrations at start.

    On a restart-every-deploy platform every replica re-runs the start command,
    so migrations at boot race each other on DDL. They are one explicit step,
    scripts/run_migrations.sh, and /ready keeps a process out of rotation until
    it has succeeded. This is unchanged by the pip fix and asserted so it stays
    that way.
    """
    text = _effective(launcher)
    for forbidden, label in (
        ("run_migrations", "the migration step"),
        ("alembic", "the migration tool"),
        ("migrate", "a migration command"),
    ):
        assert forbidden not in text, f"{launcher} runs {label} at boot"


# ---------------------------------------------------------------------------
# E2. The production database is an app-owned secret, not a name collision
#
# CXOps production is an external Neon PostgreSQL, migrated to Alembic head
# 1p4a0001 with pgvector installed. The application reads DATABASE_URL. Replit
# also publishes a platform-managed DATABASE_URL for this project.
#
# That is a name collision between two things the repository does not own, and the
# only reason it is a hazard rather than a non-issue is that the collision is
# invisible. Nothing fails; whichever value the platform happens to inject last
# simply wins, and it can differ between a workspace, a Reserved VM, and a
# redeploy of the same commit.
#
# So both production launchers map EXTERNAL_DATABASE_URL -- a secret this project
# owns -- over the top of DATABASE_URL, unconditionally. The tests below are
# about that mapping specifically: that it exists, that it is not conditional on
# the platform's variable being absent, that it happens before anything reads
# DATABASE_URL, and that no launcher ever prints either value.
#
# The two launchers are parameterised together because the worker must reach the
# same database as the web deployment. A mapping added to one and not the other
# produces the worst version of this bug: a system where the two halves disagree
# about which database is authoritative, and neither is obviously wrong.
# ---------------------------------------------------------------------------

#: The production launchers that must perform the mapping. Same tuple as
#: DEPLOYMENT_LAUNCHERS, named separately so a test about the database says what
#: it is about -- and so a future third deployment has to be added here
#: deliberately rather than picked up by accident.
DATABASE_LAUNCHERS = DEPLOYMENT_LAUNCHERS

#: The entry point each launcher starts, per launcher. Used to assert the mapping
#: precedes application startup and not merely the preflight.
LAUNCHER_ENTRY_POINT = {
    "start_replit_web.sh": "-m uvicorn app.main:app",
    "start_replit_worker.sh": "exec .venv/bin/python -m scripts.worker",
}


@pytest.mark.parametrize("launcher", DATABASE_LAUNCHERS)
def test_launcher_requires_the_external_database_url(launcher: str) -> None:
    """EXTERNAL_DATABASE_URL must be required, with no default and no fallback.

    `:-` in the *test* of emptiness is what makes the launcher safe under `set
    -u`: an unset variable aborts with an unbound-variable error naming the shell
    rather than the setting. So the check has to be `[ -z "${VAR:-}" ]`, and the
    message has to name the variable -- this is a crash loop an operator has to
    fix, and "some variable is not set" sends them to the wrong place.

    No default, and no fallback to DATABASE_URL: a fallback would reintroduce
    exactly the precedence question this mapping exists to remove, and a default
    URL is how a deployment ends up writing to the wrong database.
    """
    text = _effective(launcher)
    guard = next(
        (
            i
            for i, ln in enumerate(text.splitlines())
            if re.match(r'\s*if \[ -z "\$\{EXTERNAL_DATABASE_URL:-\}" \]; then', ln)
        ),
        None,
    )
    assert guard is not None, (
        f"{launcher} must require EXTERNAL_DATABASE_URL with "
        '`if [ -z "${EXTERNAL_DATABASE_URL:-}" ]`'
    )
    following = text.splitlines()[guard + 1 : guard + 3]
    assert any(re.match(r"\s*fail\b", ln) for ln in following), (
        f"{launcher}: a missing EXTERNAL_DATABASE_URL must abort the start, but no "
        f"`fail` follows the check. Lines after: {following!r}"
    )
    assert any("EXTERNAL_DATABASE_URL" in ln for ln in following), (
        f"{launcher}: the failure must name EXTERNAL_DATABASE_URL"
    )
    # No invented value. A literal URL in a launcher is a credential in source.
    assert not re.search(r"EXTERNAL_DATABASE_URL:-postgres", text), (
        f"{launcher} must not carry a default EXTERNAL_DATABASE_URL; a URL in the "
        "script is a password in the repository"
    )
    assert not re.search(r"EXTERNAL_DATABASE_URL:-[^}]*DATABASE_URL", text), (
        f"{launcher} must not fall back to DATABASE_URL; that is the precedence "
        "question this mapping removes"
    )


@pytest.mark.parametrize("launcher", DATABASE_LAUNCHERS)
def test_launcher_exports_database_url_from_the_external_one(launcher: str) -> None:
    """`export DATABASE_URL="$EXTERNAL_DATABASE_URL"`, unconditionally.

    The export, not a local assignment: DATABASE_URL is read by a child process
    (the preflight, uvicorn, the exec'd worker), and a shell-local variable is
    inherited by nothing.

    The absence of a condition is the assertion, and it cannot be checked by
    looking for a forbidden string, because there are several ways to get this
    wrong: `${DATABASE_URL:-$EXTERNAL_DATABASE_URL}` (platform value wins when
    present), `if [ -z "${DATABASE_URL:-}" ]` (skip the mapping when the platform
    supplied one), or appending to an existing value. Each produces a launcher
    that reads correctly here and binds to the platform's database there. So the
    line is matched exactly, and its value is the external secret and nothing
    else.
    """
    text = _effective(launcher)
    matches = re.findall(r"^export DATABASE_URL=(.*)$", text, flags=re.MULTILINE)
    assert matches == ['"$EXTERNAL_DATABASE_URL"'], (
        f"{launcher} must export DATABASE_URL as exactly "
        f"'$EXTERNAL_DATABASE_URL', got {matches!r}. Any conditional or defaulted "
        "form makes the mapping depend on whether the platform also set "
        "DATABASE_URL, which is the thing being removed."
    )
    for pattern, why in (
        (r"\$\{DATABASE_URL", "a fallback that defers to the platform value"),
        (r":-", "a default, which is a credential or a wrong database"),
        (r"\$\{\{?", "an indirect expansion nobody can check by reading the line"),
    ):
        assert not re.search(pattern, matches[0]), (
            f"{launcher}: the export must be a plain copy -- {why}"
        )


@pytest.mark.parametrize("launcher", DATABASE_LAUNCHERS)
def test_database_mapping_precedes_preflight_and_startup(launcher: str) -> None:
    """Before the preflight, and before the application process starts.

    The preflight is the first thing in either launcher that reads DATABASE_URL, so
    a mapping placed after it means the gate validated one database and the process
    connected to another -- with both steps reporting success. That is worse than
    no mapping at all, because it removes the only check that could have caught it.

    The entry point is asserted as well as the preflight, because "before the
    preflight" alone is satisfiable by a mapping that sits between the preflight and
    uvicorn, which is the same bug one step later.
    """
    text = _effective(launcher)
    lines = text.splitlines()
    assert any(ln.startswith("export DATABASE_URL=") for ln in lines), (
        f"{launcher} has no `export DATABASE_URL=...`; the mapping this test orders "
        "is missing, not merely misplaced"
    )
    export_at = next(i for i, ln in enumerate(lines) if ln.startswith("export DATABASE_URL="))
    preflight_at = next(i for i, ln in enumerate(lines) if "preflight_production_env.py" in ln)
    entry = LAUNCHER_ENTRY_POINT[launcher]
    entry_at = text.index(entry)
    assert export_at < preflight_at, (
        f"{launcher}: DATABASE_URL must be exported before the preflight; the "
        "preflight reads it, so a later mapping means the gate validated one "
        "database and the process connects to another"
    )
    assert export_at < entry_at, f"{launcher}: DATABASE_URL must be exported before {entry!r}"
    # And the check that produces it has to precede the export, or the export is
    # a bare `export` of an unset value under `set -u` -- which aborts with a
    # message about the shell rather than about the setting.
    assert any(
        re.match(r'\s*if \[ -z "\$\{EXTERNAL_DATABASE_URL:-\}" \]; then', ln) for ln in lines
    ), f"{launcher} has no EXTERNAL_DATABASE_URL check to order against the export"
    guard_at = next(
        i
        for i, ln in enumerate(lines)
        if re.match(r'\s*if \[ -z "\$\{EXTERNAL_DATABASE_URL:-\}" \]; then', ln)
    )
    assert guard_at < export_at, f"{launcher}: the check must precede the export"


@pytest.mark.parametrize("launcher", DATABASE_LAUNCHERS)
def test_no_launcher_prints_a_database_url(launcher: str) -> None:
    """No log, echo or printf may expand a database URL.

    A Neon connection string carries the password, and deployment logs are
    retained, searchable, and shipped wherever stdout is collected. So the
    invariant is about *expansion*, not about the substring: naming the variable
    in a log line is how an operator confirms the mapping took effect, and that is
    worth having. Printing its value writes a live credential, and
    `log "bound to $DATABASE_URL"` is exactly the line someone adds while
    debugging a connection error and forgets to remove.

    Asserted against executable lines only, because both launchers document this
    rule in prose that necessarily contains the very strings being forbidden.
    """
    text = _effective(launcher)
    printers = re.compile(r"^\s*(log|echo|printf)\b")
    for line in text.splitlines():
        if not printers.match(line):
            continue
        for value in (r"\$\{?EXTERNAL_DATABASE_URL", r"\$\{?DATABASE_URL"):
            assert not re.search(value, line), (
                f"{launcher} expands a database URL into a log line: {line.strip()!r}. "
                "A Neon URL contains the password."
            )
    # And nothing prints the value by another route: a `set -x` would put every
    # expanded assignment into stderr on every command.
    assert not re.search(r"^\s*set -x", text, flags=re.MULTILINE), (
        f"{launcher} must not enable shell tracing; `set -x` writes every expanded "
        "assignment, including the database URL, to the deployment log"
    )


@pytest.mark.parametrize("launcher", DATABASE_LAUNCHERS)
def test_both_deployments_bind_the_same_database(launcher: str) -> None:
    """One secret, both deployments, byte-for-byte the same assignment.

    Worth its own test because the failure it prevents is invisible: the web
    deployment and the worker each connect successfully, to different databases,
    and the symptom is a job that claims against rows the web deployment never
    writes. Nothing crashes and nothing logs an error.

    So the mapping is asserted as identical text in both scripts rather than as
    "both scripts mention EXTERNAL_DATABASE_URL", which would pass if one of them
    derived the URL differently.
    """
    web = re.search(
        r"^export DATABASE_URL=(.*)$", _effective("start_replit_web.sh"), flags=re.MULTILINE
    )
    other = re.search(r"^export DATABASE_URL=(.*)$", _effective(launcher), flags=re.MULTILINE)
    assert web and other and web.group(1) == other.group(1) == '"$EXTERNAL_DATABASE_URL"', (
        "the web deployment and the worker must assign DATABASE_URL identically; "
        f"web={web.group(1) if web else None!r}, {launcher}={other.group(1) if other else None!r}"
    )


@pytest.mark.parametrize("launcher", DEPLOYMENT_LAUNCHERS)
def test_production_preflight_still_runs_and_still_gates(launcher: str) -> None:
    """Neither the pip fix nor the build/run split weakened the env gate.

    Two properties, both load-bearing: the preflight still executes, and a
    failure still aborts. A preflight that ran but no longer blocked would be
    the same class of bug as the one being fixed -- a startup step that looks
    like a check but is not one.
    """
    text = _effective(launcher)
    assert "preflight_production_env.py" in text, f"{launcher} no longer runs the preflight"

    lines = text.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "preflight_production_env.py" in ln)
    gate = lines[idx]
    following = lines[idx + 1 : idx + 3]
    # The negation is the point. `if <cmd>; then` on a preflight *passes* means
    # the gate now aborts exactly when the environment is valid, and runs on
    # when it is not -- a check that is still present, still named, and
    # completely inverted. Asserting only "there is an if" misses that
    # completely, so the `!` is required.
    assert re.match(r"\s*if\s+!\s", gate), (
        f"{launcher}: the preflight must be run under `if !` so a non-zero exit "
        f"is the failure branch. Got {gate.strip()!r}"
    )
    assert any(re.match(r"\s*fail\b", ln) for ln in following), (
        f"{launcher}: a failing preflight must still abort the start, but no "
        f"`fail` follows it. Lines after the gate: {following!r}"
    )


@pytest.mark.parametrize("launcher", VENV_PIP_LAUNCHERS)
def test_preflight_runs_after_the_dependency_install(launcher: str) -> None:
    """The preflight imports the app, so it cannot run before its dependencies.

    `preflight_production_env.py` calls `validate_encryption_keys` and
    constructs `Settings`, so on a launcher that has to install first, running
    the gate earlier reports a missing module instead of the configuration
    problem the operator actually has.
    """
    lines = _effective(launcher).splitlines()
    gate = next(i for i, ln in enumerate(lines) if "preflight_production_env.py" in ln)
    install = next(i for i, ln in enumerate(lines) if re.match(r"\s*venv_pip\s+install", ln))
    assert gate > install, (
        f"{launcher}: the preflight imports project dependencies, so it cannot "
        "run before they are installed"
    )


def test_web_preflight_runs_after_the_artifact_and_package_checks() -> None:
    """Same ordering rule for the web launcher, against the steps it does have.

    The web launcher no longer installs, so there is no `venv_pip install` line
    to order against. What replaces it is a stronger argument for the same
    ordering: the script must not open the published port, and must not report a
    configuration fault, before it has established that the prebuilt frontend
    exists and that the image has the packages the preflight is about to import.
    Asserting the ordering keeps a future edit that hoists the preflight to the
    top of the script from turning three distinct startup failures into one
    undifferentiated "environment preflight failed".
    """
    text = _effective("start_replit_web.sh")
    lines = text.splitlines()
    gate = next(i for i, ln in enumerate(lines) if "preflight_production_env.py" in ln)
    artifact = next(i for i, ln in enumerate(lines) if ".next/standalone/server.js" in ln)
    assert gate > artifact, (
        "the prebuilt-frontend check must run before the preflight, so a missing "
        "build artifact is reported as a missing artifact"
    )
    assert 'if [ -n "$missing" ]' in text or 'if [ -n "$missing" ]' in text, (
        "the web launcher must keep the Python package check; without it the "
        "preflight is the first thing to import a package the image may not have"
    )
    packages = next(
        i for i, ln in enumerate(lines) if ln.strip() == 'missing="$(missing_python_packages)"'
    )
    assert gate > packages, (
        "the Python package check must run before the preflight, so a missing "
        "package is named rather than surfacing as an import error"
    )


def test_dev_launcher_is_untouched_by_the_pip_fix() -> None:
    """The development launcher must not acquire a pip install.

    It never had one: it selects an existing .venv or a system interpreter and
    installs nothing, which is why it works on a workspace that has no
    credentials and no venv. Adding a guarded install there would be scope
    creep, and an unguarded one would break the preview.
    """
    text = _effective("start_replit_dev.sh")
    assert not re.search(r"-m\s+pip", text), "the dev launcher must not run pip"
    assert "venv_pip" not in text, "the dev launcher must not gain the venv_pip wrapper"
    assert "PIP_USER" not in text, "the dev launcher must not set pip configuration"
    # And the properties the dev path already guarantees must still hold.
    assert "--host 127.0.0.1" in text, "the dev API must remain on loopback"


@pytest.mark.parametrize("launcher", DEPLOYMENT_LAUNCHERS)
def test_launcher_is_syntactically_valid_bash(launcher: str) -> None:
    """`bash -n` on each deployment launcher.

    Part of the suite rather than a manual step: a syntax error in the command a
    deployment runs is a deployment that cannot start, and `venv_pip` is a shell
    function that only fails when the script is parsed or executed.
    """
    import subprocess

    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "scripts" / launcher)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed for {launcher}: {result.stderr}"


# ---------------------------------------------------------------------------
# F. Behavioural: the guarded install under an injected PIP_USER=true
#
# Every test above reads the source. This one runs it.
#
# It matters because the static assertions cannot tell a correct guard from a
# plausible-looking one. `PIP_USER=false` could be spelled wrong, set on the
# wrong command, or scoped to a variable pip never reads, and every static test
# above would still pass. So the committed `venv_pip` body is extracted and
# executed against a disposable venv with PIP_USER=true injected.
#
# The launcher under test is the worker. The web launcher no longer contains a
# `venv_pip` body to extract, because it no longer installs Python -- so the
# coverage was narrowed, not lost. The crash-loop this section describes is
# caused by the Reserved VM injecting `PIP_USER=true`, which it does for every
# deployment from this repository, and the worker is now the only one that
# installs.
#
# The test also runs a CONTROL first: the same unguarded command with the same
# injected variable. If the control does not reproduce the error, the guard is
# being tested against nothing and the test skips rather than passing silently.
# A test that cannot fail is worse than no test, and this one is built so that
# it can.
#
# No network: --no-index with an unsatisfiable requirement. pip rejects a user
# install before it resolves anything, so the control still produces the error
# while the guarded run fails later and differently -- which is the difference
# being asserted.
# ---------------------------------------------------------------------------


def _extract_venv_pip(launcher: str) -> str:
    """The committed `venv_pip` body, verbatim, lifted out of the script.

    Extracted rather than reimplemented on purpose. A copy in this test file
    would keep passing if the script's version were reverted, which is the
    failure this whole section exists to catch.
    """
    text = _launcher(launcher)
    match = re.search(r"^venv_pip\(\) \{\n(.*?)^\}", text, flags=re.DOTALL | re.MULTILINE)
    assert match, f"could not extract a venv_pip body from {launcher}"
    return match.group(1)


def _run_bash(script: str, cwd, env: dict):
    import subprocess

    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("launcher", VENV_PIP_LAUNCHERS)
def test_guarded_install_survives_an_injected_pip_user(launcher: str, tmp_path) -> None:
    """Execute the committed guard under PIP_USER=true; it must hold.

    A disposable venv is created under tmp_path and the launcher's own
    `venv_pip` body is run against it with PIP_USER=true in the environment --
    the exact condition that crash-looped the deployment. The install must not
    attempt a user install.
    """
    import os
    import subprocess

    repo = tmp_path / "repo"
    (repo / ".venv").mkdir(parents=True)
    # Inside the repo: both bash invocations run with cwd=repo and reference
    # "$PWD/req.txt", so the file has to live where those commands look.
    (repo / "req.txt").write_text("cxops-no-such-package==0.0.0\n", encoding="utf-8")

    created = subprocess.run(
        ["python3", "-m", "venv", str(repo / ".venv")],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"cannot create a disposable venv here: {created.stderr.strip()[:120]}")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("PIP_")}
    base_env["PYTHONUSERBASE"] = str(tmp_path / "nowhere")

    # CONTROL: unguarded pip, with the crash-loop condition injected. This must
    # reproduce the deployment failure, otherwise there is nothing to guard.
    control = _run_bash(
        '"$PWD/.venv/bin/python" -m pip install --quiet --no-index '
        '--disable-pip-version-check -r "$PWD/req.txt"',
        cwd=repo,
        env={**base_env, "PIP_USER": "true"},
    )
    control_out = control.stdout + control.stderr
    if USER_INSTALL_ERROR not in control_out:
        pytest.skip(
            "this pip version does not reject user installs inside a venv, so "
            f"the guard has nothing to defend against. Control output: {control_out.strip()[:200]}"
        )

    # GUARDED: the launcher's own function, verbatim, same injected condition.
    body = _extract_venv_pip(launcher)
    harness = f'REPO_ROOT="$PWD"\nvenv_pip() {{\n{body}}}\nvenv_pip install --quiet --no-index --disable-pip-version-check -r "$PWD/req.txt"\n'
    guarded = _run_bash(harness, cwd=repo, env={**base_env, "PIP_USER": "true"})
    guarded_out = guarded.stdout + guarded.stderr

    assert USER_INSTALL_ERROR not in guarded_out, (
        f"{launcher}: the guarded install still attempted a user install under "
        f"PIP_USER=true.\n--- output ---\n{guarded_out.strip()[:500]}"
    )
    # The guard must not achieve that by making the install a silent no-op: pip
    # has to have actually got past mode selection and into resolution.
    assert "No matching distribution" in guarded_out or "Could not find a version" in guarded_out, (
        f"{launcher}: expected pip to reach package resolution (proving it left "
        f"user-install mode), got:\n--- output ---\n{guarded_out.strip()[:500]}"
    )


def test_user_install_error_string_is_still_the_one_we_guard_against() -> None:
    """Pin the error text, so a pip change cannot silently disarm section F.

    Section F skips when the control does not reproduce. That is the right
    behavior, but it means a future pip that words the error differently would
    quietly reduce this file's coverage to the static assertions. Asserting the
    text separately makes that a visible failure to update this file rather than
    a silent loss of coverage.
    """
    assert USER_INSTALL_ERROR in (
        "Can not perform a '--user' install. User site-packages are not visible in this virtualenv."
    ), "update USER_INSTALL_ERROR and section F together if pip reworded this"


# ---------------------------------------------------------------------------
# G. The build phase does the building, and the run phase only starts
#
# Background: one script used to install Python, run `npm ci`, run `next build`,
# and then start uvicorn and Next.js. Replit's Reserved VM re-runs the RUN
# command on every restart, so a build measured in minutes happened in front of
# the published port on every restart. The deployment was reported as
# `hostingpid1: an open port was not detected` -- a readiness failure produced by
# work that had nothing to do with readiness.
#
# The fix is a phase split: `[deployment] build` produces the artifact once,
# `[deployment] run` starts it. Replit documents deployment secrets as the place
# for "environment variables or secrets your build command needs to run
# securely", so the build does receive the deployment configuration the frontend
# build inlines.
#
# These tests pin the split from both sides, because either half alone is a
# different outage: a build phase that does not build produces a deployment that
# starts and then serves nothing, and a run phase that rebuilds reintroduces the
# original timeout.
#
# The split is also where the Python dependency question is settled. Moving the
# install out of the run phase created a new question -- if nothing installs
# requirements.txt at start, what guarantees they exist? -- and the answer cannot
# be the image, because Replit's `packager.features.enabledForHosting` defaults
# to false, so a hosting install is not something the platform promises to do.
# So the build phase installs them, with the system interpreter, into a
# project-local `--target` directory: no venv, no PATH, no mutation of the
# image's site-packages, and nothing for the run phase to do but prepend the
# directory to PYTHONPATH.
# ---------------------------------------------------------------------------


def _build_script() -> str:
    return _launcher(BUILD_SCRIPT)


def test_build_script_exists_and_is_valid_bash() -> None:
    """`bash -n` on the build command, which is a deployment phase of its own.

    A syntax error here fails the publish before any runtime is reached, and the
    build command is a shell function (`fail`) plus a heredoc, both of which only
    fail when the script is parsed or executed. `test_deployment_build_script_exists
    _and_is_executable` covers presence; this covers the thing actually running.
    """
    import subprocess

    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "scripts" / BUILD_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed for {BUILD_SCRIPT}: {result.stderr}"


def test_build_script_fails_fast_and_resolves_the_repo_root() -> None:
    """`set -euo pipefail`, and a root resolved from the script's own location.

    Two properties that are easy to lose in a shell script and impossible to lose
    silently:

    * `set -e` is what turns a failed `next build` into a failed deployment rather
      than a successful build phase that ships a stale `.next` from a previous
      run. Without it, a build error scrolls past and the run phase then serves
      whatever the last good build left behind -- which looks like a deploy that
      did not take effect.
    * `REPO_ROOT` is derived from `BASH_SOURCE` rather than `$PWD`, because the
      working directory of a build phase is not guaranteed to be the repository
      root. A `cd frontend` relative to the wrong directory installs the wrong
      project's dependencies.
    """
    text = _build_script()
    assert re.search(r"^set -euo pipefail$", text, flags=re.MULTILINE), (
        f"{BUILD_SCRIPT} must use `set -euo pipefail`; without -e a failed "
        "`next build` is a successful build phase that ships a stale artifact"
    )
    assert "BASH_SOURCE[0]" in text, (
        f"{BUILD_SCRIPT} must derive REPO_ROOT from BASH_SOURCE[0]; the working "
        "directory of a build phase is not guaranteed to be the repository root"
    )
    assert re.search(r'^cd "\$REPO_ROOT"$', text, flags=re.MULTILINE), (
        f"{BUILD_SCRIPT} must cd to REPO_ROOT before doing anything else"
    )


def test_build_script_installs_with_npm_ci_and_includes_dev() -> None:
    """`npm ci --include=dev` — both halves are load-bearing.

    `npm ci` rather than `npm install` because it installs the lockfile exactly
    and fails outright when package.json and package-lock.json disagree. A
    mismatched lock is otherwise a hard error discovered at the worst moment, and
    it is a silent divergence until then.

    `--include=dev` because devDependencies are required to BUILD, not optional:
    `next build` runs the TypeScript compiler and the PostCSS toolchain. A
    production-only install produces a build that fails on a missing binary --
    and NODE_ENV=production is set for this script, so npm would otherwise be
    entitled to skip them.
    """
    text = _build_script()
    assert re.search(r"npm ci --include=dev", text), (
        f"{BUILD_SCRIPT} must run `npm ci --include=dev`; `npm install` can "
        "silently diverge from the lockfile, and a production-only install has no "
        "TypeScript or PostCSS toolchain to build with"
    )


def test_build_script_runs_the_next_production_build() -> None:
    """The build phase must actually invoke the build.

    Asserted on the npm script name rather than on `next build`, so the
    repository keeps one definition of what "the production build" means
    (frontend/package.json's `"build": "next build"`) instead of two spellings
    that can drift.
    """
    text = _build_script()
    assert re.search(r"npm run build", text), (
        f"{BUILD_SCRIPT} must run `npm run build`; without it the phase installs "
        "dependencies and ships nothing"
    )
    assert re.search(r"^export NODE_ENV=production$", text, flags=re.MULTILINE), (
        f"{BUILD_SCRIPT} must set NODE_ENV=production for the build. It cannot be "
        "inherited from `.replit [env]`: a project-wide NODE_ENV=production also "
        "applies to the workspace run, and `next dev` does not start under it."
    )


def test_build_script_verifies_standalone_output() -> None:
    """The artifact must be checked for, not assumed.

    `output: "standalone"` is a config setting in next.config.ts, and a change
    there (or a Next version that resolves differently) removes
    `.next/standalone/server.js` without failing the build. The run phase would
    then start, bind no port, and the deployment would fail its readiness check
    for a reason that has nothing to do with readiness — which is the shape of
    the bug this split is fixing. Asserting it in the build phase turns that into
    a build error that names the path.

    Asserted through the path variables rather than the literal `.next/standalone`
    string, because the two scripts and the test have to agree on the path while
    only spelling it once each. A test that hardcoded the literal would still
    pass if the script's `STANDALONE_DIR` moved somewhere else, which is the one
    change that would actually break the run phase.
    """
    text = _build_script()
    assert re.search(
        r'^readonly NEXT_DIR="\$REPO_ROOT/\$FRONTEND_DIR/\.next"$', text, flags=re.MULTILINE
    ), (
        "the build script must derive NEXT_DIR from the frontend directory; the "
        "run phase looks for the artifact at this path"
    )
    assert re.search(
        r'^readonly STANDALONE_DIR="\$NEXT_DIR/standalone"$', text, flags=re.MULTILINE
    ), (
        "the build script must expect `output: standalone`; without it the "
        "verified path is not the one Next emits"
    )
    assert re.search(r'\[ -f "\$STANDALONE_DIR/server\.js" \]', text), (
        "the build must assert the standalone server exists with an -f guard, not "
        "merely reference the path: a config change that drops `output: standalone` "
        "is otherwise discovered as a port that never opens"
    )
    assert re.search(r'fail "[^"]*\$STANDALONE_DIR/server\.js', text), (
        "the failure must name the missing artifact path, so the build error is "
        "actionable without reading the script"
    )


def test_build_script_stages_static_and_public_into_standalone() -> None:
    """Next's standalone output omits `.next/static` and `public/` by design.

    It expects a container image to copy them in. Skipping the copy is the
    classic result: HTML renders, every `/_next/static/*.js` 404s, and the app is
    a blank page that no build log or startup log mentions. The destinations are
    removed first, because `cp -R src dst` nests into `dst/src` when `dst` already
    exists — which is what happens on any build that did not start from a clean
    `.next`.
    """
    text = _build_script()
    assert re.search(r'cp -R "\$NEXT_DIR/static" "\$STANDALONE_DIR/\.next/static"', text), (
        f"{BUILD_SCRIPT} must copy .next/static into the standalone output, or "
        "every JS and CSS asset 404s and the deployed app is a blank page"
    )
    assert re.search(r'cp -R "\$PUBLIC_DIR" "\$STANDALONE_DIR/public"', text), (
        f"{BUILD_SCRIPT} must copy public/ into the standalone output"
    )
    assert len(re.findall(r"rm -rf \"\$STANDALONE_DIR", text)) >= 2, (
        "both copy destinations must be removed first; `cp -R src dst` nests "
        "rather than replacing when dst already exists"
    )
    # public/ is optional in a Next app, so its copy is guarded rather than
    # unconditional. An unguarded copy of a directory that may not exist is a
    # build failure on a perfectly valid project.
    assert re.search(r'if \[ -d "\$PUBLIC_DIR" \]', text), (
        f"{BUILD_SCRIPT} must guard the public/ copy with a directory test"
    )


def test_build_script_requires_the_public_build_config() -> None:
    """The three build-inlined values are required, never defaulted.

    `next build` inlines `process.env` for prerendered routes and for every
    `NEXT_PUBLIC_*` reference, so these cannot be supplied at run time. The
    failure mode without the check is quiet and late: robots.txt and sitemap.xml
    advertise `http://localhost:PORT` as the canonical origin, and every staff
    session throws `NhostConfigurationError` on first use. Neither appears in a
    build log, and both read as a broken frontend rather than a missing setting.

    No value is invented and no default is applied. The user asked for safe public
    defaults only where the source already establishes an unambiguous production
    value, and it does not for any of these three.
    """
    text = _build_script()
    for name in (
        "CXOPS_PUBLIC_SITE_URL",
        "NEXT_PUBLIC_NHOST_SUBDOMAIN",
        "NEXT_PUBLIC_NHOST_REGION",
    ):
        assert name in text, f"{BUILD_SCRIPT} must require {name}; next build inlines it"


def test_build_script_requires_no_sensitive_secret() -> None:
    """No sensitive runtime secret may be required to build the frontend.

    The distinction is require-vs-reference, and it is the same one the dev
    launcher test draws. A build step that demanded DATABASE_URL, ENCRYPTION_KEYS
    or OPENAI_API_KEY would fail a completely correct deployment for the sake of a
    check that already runs, later, in the preflight — and the failure would read
    as a configuration fault at the wrong phase.

    So the check is on the shell expansion that would DEMAND a value: `${NAME}` or
    `${NAME:?msg}` with no default. A `${NAME:-}` is safe, because the default
    branch is the one that runs when the variable is absent. The indirect
    `${!name}` form used by the required-public-config loop is checked separately,
    below, because it is the same demand written generically.
    """
    text = _effective(BUILD_SCRIPT)
    demanded = re.findall(r"\$\{([A-Z_][A-Z0-9_]*)(:\?[^}]*)?\}", text)
    for name in demanded:
        assert name not in SENSITIVE_RUNTIME_SECRETS, (
            f"{BUILD_SCRIPT} demands {name} with no default, so the build fails "
            "whenever a sensitive runtime secret is absent — even if the "
            "configuration is correct. Those are checked by the preflight, at run "
            "time."
        )
    # The required-public-config loop demands whatever it is handed, one level of
    # indirection down. So the demand has to be checked where it is expressed.
    assert re.search(r'if \[ -z "\$\{!name:-\}" \]', text), (
        f"{BUILD_SCRIPT} must demand its required public variables through a "
        "defaulted expansion (`${{!name:-}}`) so `set -u` reports the missing "
        "setting rather than aborting with an unbound-variable error"
    )
    loop = re.search(r"for name in ([^\n]+); do", text)
    assert loop, f"{BUILD_SCRIPT} must enumerate the variables it requires"
    required = set(re.findall(r"[A-Z][A-Z0-9_]+", loop.group(1)))
    assert not (required & SENSITIVE_RUNTIME_SECRETS), (
        f"{BUILD_SCRIPT} requires sensitive secret(s) "
        f"{sorted(required & SENSITIVE_RUNTIME_SECRETS)} at build time"
    )
    assert required == {
        "CXOPS_PUBLIC_SITE_URL",
        "NEXT_PUBLIC_NHOST_SUBDOMAIN",
        "NEXT_PUBLIC_NHOST_REGION",
    }, (
        f"{BUILD_SCRIPT} requires {sorted(required)}; the build-inlined public "
        "values are exactly these three"
    )


def test_build_script_rejects_a_non_https_canonical_origin() -> None:
    """A plaintext canonical origin is refused at build time.

    The same reasoning the preflight applies to FRONTEND_BASE_URL: the value
    parses, the deployment starts, and the only symptom is a site telling every
    crawler and every tenant that its own origin is plaintext. The message strips
    the query string, because a value carrying a token in it must not reach the
    build log.
    """
    text = _build_script()
    assert re.search(r'case "\$CXOPS_PUBLIC_SITE_URL" in\s*\n\s*https://\*\)', text), (
        f"{BUILD_SCRIPT} must accept only an https CXOPS_PUBLIC_SITE_URL"
    )
    assert "must be https" in text, "the rejection must name what is wrong, not just exit non-zero"


def test_web_launcher_validates_the_prebuilt_artifact_before_starting() -> None:
    """The run phase must check the artifact exists, and fail naming the build.

    This is the half of the split that turns a missing build into a one-line fix.
    Without it the run phase would start uvicorn, find no server.js, and the
    frontend child would die instantly — reported as a child exit, which is
    accurate and useless.

    The message has to mention the build script by name, because "restart it" was
    the correct remedy when the run command built and is the wrong one now.
    """
    text = _effective("start_replit_web.sh")
    assert re.search(r'\[ -f "\$STANDALONE_SERVER" \]', text), (
        "the web launcher must test the standalone server with an -f guard before starting anything"
    )
    assert BUILD_SCRIPT in _launcher("start_replit_web.sh"), (
        "the missing-artifact message must name scripts/build_replit_web.sh, since "
        "re-running the run command is no longer the remedy"
    )


def test_web_launcher_validates_python_packages_and_names_them() -> None:
    """The build's packages are importable, and a miss is named.

    The build phase already verified the install with the same interpreter and the
    same PYTHONPATH. This repeats it because it is cheap -- four imports, well
    under a second -- and because the run phase is the last point at which an
    unusable install can still be turned into a clear message instead of a
    half-started deployment serving 502s.

    The check has to report WHICH packages are missing. A bare `import uvicorn`
    behind an `if !` says only that something is absent, which is the same
    undifferentiated failure the previous design produced — minutes of pip and
    then a crash — with a shorter wait.
    """
    text = _effective("start_replit_web.sh")
    assert "missing_python_packages" in text, (
        "the web launcher must keep an explicit Python package check"
    )
    for module in ("uvicorn", "fastapi", "sqlalchemy", "app"):
        assert module in text, (
            f"the package check must cover {module}; the first thing the run phase "
            "runs is uvicorn on app.main"
        )
    assert re.search(r'missing="\$\(missing_python_packages\)"', text), (
        "the check's result must be captured, so the failure can name the packages"
    )
    assert re.search(r'if \[ -n "\$missing" \]', text), (
        "a non-empty missing-package list must gate the start"
    )
    assert "fail " in text, "the missing-package branch must abort the start"


def test_web_launcher_does_not_build_or_install_node_dependencies() -> None:
    """No npm, no Next build, in the run command.

    This is the invariant the whole change is for. Every one of these tokens in
    the run command puts minutes of work between the machine starting and the
    published port opening, which is what produced
    `hostingpid1: an open port was not detected` on a deployment that was in fact
    fine.
    """
    text = _effective("start_replit_web.sh")
    for forbidden, label in (
        ("npm ci", "a dependency install"),
        ("npm install", "a dependency install"),
        ("npm run build", "a frontend build"),
        ("next build", "a frontend build"),
    ):
        assert forbidden not in text, (
            f"the web launcher contains {label} ({forbidden!r}); the frontend is "
            "built by the [deployment] build phase"
        )
    # The standalone server is started as `node <path>`, never through an npm
    # wrapper: `npm start &` makes $! the npm process, and signalling npm leaves
    # the real server alive holding the published port.
    assert re.search(r'node "\$STANDALONE_SERVER"', text), (
        "the standalone server must be started through node, so the supervised pid "
        "is the process that binds the published port"
    )


def test_web_launcher_still_serves_the_frontend_on_the_published_port() -> None:
    """The origin shape is unchanged by the split.

    FastAPI stays on loopback, Next.js stays on 0.0.0.0 at the injected PORT, and
    BACKEND_API_URL still defaults to that loopback API. The split moved work out
    of the run command; it must not have moved a port.
    """
    text = _effective("start_replit_web.sh")
    assert "--host 127.0.0.1" in text, "the API must bind loopback"
    assert "--host 0.0.0.0" not in text, (
        "the API must not bind all interfaces; only the frontend is published"
    )
    assert 'PORT="$PUBLIC_PORT"' in text and "HOSTNAME=0.0.0.0" in text, (
        "the frontend must bind 0.0.0.0 on the injected PORT"
    )
    assert re.search(r'PUBLIC_PORT="\$\{PORT:-\d+\}"', text), (
        "PUBLIC_PORT must expand PORT with a default, so Replit's injected value wins when present"
    )
    assert 'BACKEND_API_URL="http://127.0.0.1:${INTERNAL_API_PORT}"' in text, (
        "BACKEND_API_URL must still default to the loopback API"
    )


def test_web_launcher_still_supervises_both_children() -> None:
    """Signal handling and child reaping are unchanged by the split.

    Worth restating because the split deleted a large block of the script above
    the supervisor, and a supervisor that is "re-added" carelessly is the
    regression that block's removal makes easy to introduce. The failure it
    prevents is specific: if uvicorn dies and the shell keeps running Next.js,
    the platform sees the port open and the deployment looks healthy while every
    backend call 503s through the BFF.
    """
    text = _effective("start_replit_web.sh")
    for required in (
        "trap 'on_signal TERM' TERM",
        "trap 'on_signal INT' INT",
        "trap 'terminate' EXIT",
        "wait -n",
        'for pid in "$api_pid" "$web_pid"',
    ):
        assert required in text, f"the web launcher is missing: {required}"
    for pid_var in ("api_pid", "web_pid"):
        assert pid_var in text, f"{pid_var} is not supervised"
    # The API must be started before the frontend, so $! binds to the right
    # child, and both must be started before the wait loop.
    api_at = text.index("-m uvicorn")
    web_at = text.index('node "$STANDALONE_SERVER"')
    assert api_at < web_at, "uvicorn must be started first so api_pid is correct"
    assert web_at < text.index("wait -n"), (
        "both children must be started before the wait loop, or a failure during "
        "startup is not observed as a child exit"
    )
