"""Private-repository-compatible Semgrep CE gate; never prints source snippets.

Run against the Community Edition p/security-audit rules. Unlike CodeQL this
does not claim inter-file dataflow completeness or replace a human review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# The shared-security scope mirrors the review-sensitive paths gated by CodeQL.
SECURITY_DIRS = (
    "staging/",
    "app/api/",
    "app/core/",
    "app/repositories/",
    "frontend/src/app/staging/",
    "frontend/src/lib/staging/",
    "frontend/src/lib/auth/",
    "frontend/src/lib/session/",
    "frontend/src/lib/tenant/",
)
SECURITY_FILES = {
    "app/main.py",
    "app/models/organization.py",
    "app/models/organization_membership.py",
    "app/models/ticket.py",
    "app/services/authorization_service.py",
    "app/services/tenant_service.py",
    "frontend/src/proxy.ts",
}


# Four known TSX parser limitations outside staging/shared-auth paths.
# Re-review after Semgrep grammar changes; no security-code exceptions.
APPROVED_PARTIAL_PARSE_PATHS = frozenset({
    "frontend/src/app/(control-center)/dashboard/page.tsx",
    "frontend/src/app/(control-center)/knowledge/page.tsx",
    "frontend/src/app/(control-center)/observability/page.tsx",
    "frontend/src/app/(control-center)/transformation/page.tsx",
})


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Semgrep result must identify a source path")
    return value.replace("\\", "/").removeprefix("./")


def _security_scope(path: str) -> bool:
    return path in SECURITY_FILES or any(path.startswith(p) for p in SECURITY_DIRS)


def _error_kind(error: dict[str, Any]) -> str:
    kind = error.get("type")
    if isinstance(kind, list) and kind and isinstance(kind[0], str):
        return kind[0]
    return kind if isinstance(kind, str) else "unknown"


def _approved_partial_parse(error: Any) -> bool:
    if not isinstance(error, dict) or _error_kind(error) != "PartialParsing":
        return False
    spans = error.get("spans")
    if not isinstance(spans, list) or not spans:
        return False
    paths = {
        _path(span["file"])
        for span in spans
        if isinstance(span, dict) and isinstance(span.get("file"), str)
    }
    return bool(paths) and paths.issubset(APPROVED_PARTIAL_PARSE_PATHS)


def evaluate(report: dict[str, Any]) -> tuple[str, bool]:
    """Fail closed on scan errors, errors anywhere, and scoped warnings."""
    if not isinstance(report, dict):
        raise TypeError("Semgrep report must be a JSON object")
    results, errors = report.get("results"), report.get("errors")
    if not isinstance(results, list) or not isinstance(errors, list):
        raise TypeError("Semgrep report has no validated results/errors lists")
    paths = report.get("paths")
    if not isinstance(paths, dict) or not isinstance(paths.get("scanned"), list):
        raise TypeError("Semgrep report has no scanned-file inventory")
    scanned = [_path(p) for p in paths["scanned"]]
    for target in ("app/", "staging/", "frontend/src/"):
        if not any(p.startswith(target) for p in scanned):
            raise ValueError(f"Required scan target missing: {target}")

    findings: list[tuple[str, str, str, bool]] = []
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("extra"), dict):
            raise TypeError("Malformed Semgrep result")
        path = _path(item.get("path"))
        severity = item["extra"].get("severity")
        if severity not in ("ERROR", "WARNING", "INFO", "INVENTORY", "EXPERIMENT"):
            raise ValueError("Unknown Semgrep finding severity; fail closed")
        rule = item.get("check_id")
        if not isinstance(rule, str) or not rule:
            raise ValueError("Missing Semgrep rule ID")
        blocked = severity == "ERROR" or (
            severity == "WARNING" and _security_scope(path)
        )
        findings.append((rule[:140], path[:240], severity, blocked))

    blocked_findings = [f for f in findings if f[3]]
    approved_partial = [e for e in errors if _approved_partial_parse(e)]
    unexpected_errors = [e for e in errors if not _approved_partial_parse(e)]
    if len(approved_partial) > len(APPROVED_PARTIAL_PARSE_PATHS):
        raise ValueError("Unexpected quantity of approved partial parse warnings")
    error_categories = sorted({
        _error_kind(e) for e in errors if isinstance(e, dict)
    })
    error_files = sorted({
        str(span.get("file"))[:180]
        for e in errors if isinstance(e, dict)
        for span in (e.get("spans") or [])
        if isinstance(span, dict) and span.get("file")
    })
    lines = [
        "### Free private-repo SAST: Semgrep Community Edition",
        f"- Files scanned: **{len(scanned)}**",
        f"- Findings: **{len(findings)}**",
        f"- Blocking findings: **{len(blocked_findings)}**",
        f"- Scanner/config errors: **{len(errors)}**",
        f"- Approved non-staging TSX parser warnings: **{len(approved_partial)}**",
        f"- Unexpected scanner/config errors: **{len(unexpected_errors)}**",
        "- Scanner error categories: " + (", ".join(error_categories) or "none"),
        "- Scanner diagnostic paths: " + (", ".join(error_files[:8]) or "none"),
        "- No source code, snippets, credentials, or raw Semgrep JSON are uploaded.",
        "- Community Edition is not equivalent to CodeQL or a human review.",
    ]
    for rule, path, severity, _ in (blocked_findings or findings)[:30]:
        lines.append(f"- {severity} {rule}: `{path}`")
    return "\n".join(lines) + "\n", bool(blocked_findings or unexpected_errors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    if not args.report.is_file():
        parser.error("Semgrep report missing; fail closed")
    summary, blocked = evaluate(json.loads(args.report.read_text(encoding="utf-8")))
    print(summary, end="")
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as out:
            out.write(summary)
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
