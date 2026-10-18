"""Offline security tests. No real backend, provider, or network access."""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from uuid import UUID

from scripts.worker_clock_protocol import (
    CLOCK_PATH,
    InvalidClockTrigger,
    verify_clock_trigger,
)

NOW_MS = 1_800_000_000_000
NONCE = "11111111-1111-4111-8111-111111111111"
KEY = "a" * 40
KEY_ID = "current"
SLOT = NOW_MS // 300_000
BODY = json.dumps({"version": 1, "slot": SLOT}, separators=(",", ":")).encode("utf-8")


def signed_headers(*, secret=KEY, body=BODY, nonce=NONCE, timestamp=NOW_MS):
    canonical = "\n".join(
        (
            "cxops-clock-v1",
            "POST",
            CLOCK_PATH,
            str(timestamp),
            nonce,
            KEY_ID,
            hashlib.sha256(body).hexdigest(),
        )
    )
    signature = hmac.new(
        secret.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "x-cxops-clock-time": str(timestamp),
        "x-cxops-clock-nonce": nonce,
        "x-cxops-clock-key-id": KEY_ID,
        "x-cxops-clock-signature": "v1=" + signature,
    }


def validate(
    *,
    headers=None,
    body=BODY,
    method="POST",
    path=CLOCK_PATH,
    secret=KEY,
    now_ms=NOW_MS,
):
    return verify_clock_trigger(
        method=method,
        path=path,
        headers=headers or signed_headers(),
        body=body,
        secret=secret,
        now_ms=now_ms,
    )


class ClockTriggerProtocolTests(unittest.TestCase):
    def test_signed_valid_scheduled_trigger(self):
        self.assertEqual(validate(), SLOT)

    def test_known_vector_matches_node(self):
        self.assertEqual(
            signed_headers()["x-cxops-clock-signature"],
            "v1=20f6df674101e7d22b157201ed800a838215bb5e062714819547c5d04ded5d41",
        )

    def test_wrong_method_or_path_rejected(self):
        for change in (
            {"method": "GET"},
            {"path": "/internal/worker-clock/tick/"},
            {"path": CLOCK_PATH + "?force=true"},
        ):
            with self.subTest(change=change), self.assertRaises(InvalidClockTrigger):
                validate(**change)

    def test_tampering_signed_body_or_headers_rejected(self):
        for change in (
            {"body": b'{"version":1,"slot":1}'},
            {
                "headers": {
                    **signed_headers(),
                    "x-cxops-clock-time": str(NOW_MS - 300_000),
                }
            },
            {"headers": {**signed_headers(), "x-cxops-clock-nonce": str(UUID(int=1))}},
            {
                "headers": {
                    **signed_headers(),
                    "x-cxops-clock-signature": "v1=" + "0" * 64,
                }
            },
        ):
            with (
                self.subTest(change=list(change)),
                self.assertRaises(InvalidClockTrigger),
            ):
                validate(**change)

    def test_invalid_key_and_clock_window_rejected(self):
        for change in (
            {"secret": "short"},
            {"now_ms": NOW_MS + 901_000},
            {"now_ms": NOW_MS - 61_000},
        ):
            with self.subTest(change=change), self.assertRaises(InvalidClockTrigger):
                validate(**change)

    def test_reject_duplicate_case_variant_signature_header(self):
        headers = signed_headers()
        headers["X-CXOps-Clock-Signature"] = headers["x-cxops-clock-signature"]
        with self.assertRaises(InvalidClockTrigger):
            validate(headers=headers)

    def test_reject_duplicate_json_field_and_boolean_slot(self):
        for body in (
            f'{{"version":1,"slot":{SLOT},"slot":{SLOT}}}'.encode(),
            b'{"version":1,"slot":true}',
            b"{}",
        ):
            with self.subTest(body=body), self.assertRaises(InvalidClockTrigger):
                validate(body=body, headers=signed_headers(body=body))

    def test_replay_requires_durable_slot_ledger_in_future_handler(self):
        # Signature verifies for a duplicate request! The future API must
        # atomically claim (clock_id, slot) in PostgreSQL before any worker job.
        headers = signed_headers()
        self.assertEqual(validate(headers=headers), SLOT)
        self.assertEqual(validate(headers=headers), SLOT)
        # This documents that HMAC is NOT itself an exactly-once guarantee.
