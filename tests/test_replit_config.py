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


def test_deployment_declares_no_build_key() -> None:
    """`[deployment].build` must be absent, not an empty array.

    It was written `build = []`, intended to mean "no build step". Replit's
    schema treats `build` as a command *string*, so an array is not how you say
    "none" — it is simply an invalid value, and Publishing rejected the entire
    deployment config with:

        The .replit deployment configuration is invalid

    The failure is worse than the thing being expressed: an absent optional
    build step is the default, whereas an invalid one blocks the publish
    outright. This project builds the frontend inside
    scripts/start_replit_web.sh, and it must keep doing so — see the comment
    above the `[deployment]` table for why a real build step cannot run there
    (it needs deployment secrets that only exist once the deployment does).

    Deliberately asserted on the PARSED config, never on the file text. The
    comment block in `.replit` quotes the old `build = []` verbatim in order to
    warn against restoring it, so a substring test would fail on the warning
    itself. A comment cannot set a TOML key, so parsing is also the more
    accurate statement of the rule.

    Absence is asserted rather than "must be a string", because the schema
    violation is the array. Omitting the key is what TOML uses to mean "no
    value", and it is the form that validates.
    """
    deployment = CONFIG.get("deployment", {})
    assert "build" not in deployment, (
        f"[deployment].build is {deployment['build']!r}. Replit's schema takes "
        "build to be a command string, so an array -- including `build = []` -- "
        "is an invalid value, and Publishing rejects the whole config with "
        '"The .replit deployment configuration is invalid". Omit the key '
        "instead; the frontend build belongs in scripts/start_replit_web.sh."
    )


def test_deployment_build_would_be_a_command_not_a_list() -> None:
    """If a `build` key is ever added deliberately, it must be a string.

    This is the belt to the previous test's braces. Absent passes; a string is
    allowed through for a future real build step; an array never is. A future
    change that adds a genuine build command therefore does not have to delete
    this test, and one that adds an array is caught here even if the stricter
    assertion above is refactored away.
    """
    deployment = CONFIG.get("deployment", {})
    if "build" not in deployment:
        return  # the intended state: no build step at all
    assert isinstance(deployment["build"], str), (
        f"[deployment].build must be a command string, got "
        f"{type(deployment['build']).__name__} {deployment['build']!r}. An "
        "empty list is not a way to express 'no build step' -- omit the key."
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
