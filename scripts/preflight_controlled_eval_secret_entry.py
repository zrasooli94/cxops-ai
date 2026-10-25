"""Report the disabled credential-entry boundary without requesting a secret."""

from __future__ import annotations

import json

from app.core.config import settings


def main() -> None:
    print(
        json.dumps(
            {
                "mode": "offline_secret_entry_preflight",
                "ready_for_secret_entry": False,
                "secret_prompt_invoked": False,
                "credential_source": "future_interactive_terminal_prompt_only",
                "credential_persistence": "none",
                "provider_install_attempted": False,
                "model_eval_openai_source_enabled": settings.model_eval_openai_source_enabled,
                "public_chat_model_eval_enabled": settings.public_chat_model_eval_enabled,
                "network_request_performed": False,
                "next_gate": "separate action-time credential configuration approval",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
