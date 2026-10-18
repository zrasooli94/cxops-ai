"""Offline adversarial contract for the free private-repo Semgrep gate."""
from __future__ import annotations

import unittest

from scripts.check_private_sast import evaluate


def report(results=None, errors=None, scanned=None):
    return {
        "results": [] if results is None else results,
        "errors": [] if errors is None else errors,
        "paths": {"scanned": [
            "app/main.py",
            "staging/neon_jwt.py",
            "frontend/src/app/staging/page.tsx",
        ] if scanned is None else scanned},
    }


def finding(path, severity="WARNING", rule="python.security.unsafe"):
    return {"check_id": rule, "path": path, "extra": {
        "severity": severity, "lines": "never report source",
    }}


class PrivateSastGateTests(unittest.TestCase):
    def test_clean_result_passes(self):
        summary, failed = evaluate(report())
        self.assertFalse(failed)
        self.assertIn("Files scanned: **3**", summary)

    def test_error_fails_even_in_unrelated_runtime_code(self):
        _, failed = evaluate(report(results=[finding("app/integrations/unsafe.py", "ERROR")]))
        self.assertTrue(failed)

    def test_staging_warning_blocks(self):
        _, failed = evaluate(report(results=[finding("staging/neon_jwt.py")]))
        self.assertTrue(failed)

    def test_shared_auth_warning_blocks(self):
        _, failed = evaluate(report(results=[finding("app/core/auth.py")]))
        self.assertTrue(failed)

    def test_frontend_action_warning_blocks(self):
        _, failed = evaluate(report(results=[finding("frontend/src/lib/staging/neon-policy.ts")]))
        self.assertTrue(failed)

    def test_unrelated_warning_remains_visible_but_does_not_fail(self):
        summary, failed = evaluate(report(results=[finding("frontend/src/components/header.tsx")]))
        self.assertFalse(failed)
        self.assertIn("WARNING python.security.unsafe", summary)

    def test_scanner_configuration_error_fails(self):
        summary, failed = evaluate(report(errors=[{"message": "registry rules unavailable"}]))
        self.assertTrue(failed)
        self.assertIn("Scanner/config errors: **1**", summary)
        self.assertNotIn("registry rules unavailable", summary)

    def test_missing_scan_target_fails(self):
        with self.assertRaisesRegex(ValueError, "staging/"):
            evaluate(report(scanned=["app/main.py", "frontend/src/app/staging/page.tsx"]))

    def test_invalid_report_shape_fails(self):
        for item in ({}, {"results": [], "errors": []}, {"results": "oops", "errors": []}):
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    evaluate(item)

    def test_malformed_finding_fails(self):
        with self.assertRaises(ValueError):
            evaluate(report(results=[{"check_id": "x", "path": "app/main.py"}]))

    def test_unknown_severity_fails(self):
        with self.assertRaises(ValueError):
            evaluate(report(results=[finding("app/main.py", "CRITICAL_X")]))

    def test_summary_never_includes_source_snippet(self):
        summary, _ = evaluate(report(results=[finding("app/core/auth.py")]))
        self.assertNotIn("never report source", summary)


if __name__ == "__main__":
    unittest.main()
