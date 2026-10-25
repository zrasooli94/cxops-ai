from __future__ import annotations

import io

import pytest

from app.services.model_eval_operator_secret import (
    OperatorSecretInputBlocked,
    operator_entered_api_key,
)
from scripts.preflight_controlled_eval_secret_entry import main


class _Stream(io.StringIO):
    def __init__(self, *, terminal: bool):
        super().__init__()
        self.terminal = terminal

    def isatty(self) -> bool:
        return self.terminal


def test_non_tty_fails_before_prompt_and_never_leaks(capsys):
    sentinel = "synthetic-secret-sentinel"

    def prompt(*_args, **_kwargs):
        raise AssertionError(sentinel)

    with (
        pytest.raises(OperatorSecretInputBlocked, match="interactive terminal"),
        operator_entered_api_key(
            input_stream=_Stream(terminal=False),
            output_stream=_Stream(terminal=True),
            prompt_fn=prompt,
        ),
    ):
        pass
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err


def test_prompt_error_is_redacted_and_secret_never_printed(capsys):
    sentinel = "synthetic-secret-sentinel"

    def prompt(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    with (
        pytest.raises(OperatorSecretInputBlocked) as caught,
        operator_entered_api_key(
            input_stream=_Stream(terminal=True),
            output_stream=_Stream(terminal=True),
            prompt_fn=prompt,
        ),
    ):
        pass
    assert sentinel not in str(caught.value)
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err


def test_secret_exists_only_inside_caller_scope_and_is_not_echoed(capsys):
    sentinel = "synthetic-secret-sentinel"
    output = _Stream(terminal=True)
    observed = None
    with operator_entered_api_key(
        input_stream=_Stream(terminal=True),
        output_stream=output,
        prompt_fn=lambda *_args, **_kwargs: sentinel,
    ) as secret:
        observed = secret
    assert observed == sentinel
    assert sentinel not in output.getvalue()
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err


def test_preflight_never_prompts_installs_provider_or_enables_flags(capsys):
    main()
    payload = capsys.readouterr().out
    assert '"ready_for_secret_entry": false' in payload
    assert '"secret_prompt_invoked": false' in payload
    assert '"network_request_performed": false' in payload
    assert '"provider_install_attempted": false' in payload
    assert '"model_eval_openai_source_enabled": false' in payload
    assert '"public_chat_model_eval_enabled": false' in payload
