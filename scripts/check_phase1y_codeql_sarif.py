"""Fail closed on CodeQL findings touching Phase 1Y or shared security code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SECURITY_SCOPE = (
    "app/api/routes/staging_ticket_action.py",
    "app/core/auth.py",
    "app/api/deps.py",
    "app/core/rbac.py",
    "app/core/principal.py",
    "app/core/database.py",
    "app/main.py",
    "app/models/organization.py",
    "app/models/organization_membership.py",
    "app/models/ticket.py",
    "app/repositories/organization_membership_repository.py",
    "app/services/authorization_service.py",
    "app/services/tenant_service.py",
    "staging/",
    "frontend/src/app/staging/",
    "frontend/src/lib/staging/",
    "frontend/src/proxy.ts",
    "frontend/src/lib/tenant/",
)


def _path(location: dict[str, Any]) -> str:
    physical = location.get("physicalLocation") or {}
    return (physical.get("artifactLocation") or {}).get("uri", "unknown")


def _all_paths(result: dict[str, Any]) -> list[str]:
    locations = list(result.get("locations") or [])
    locations.extend(result.get("relatedLocations") or [])
    for flow in result.get("codeFlows") or []:
        for thread in flow.get("threadFlows") or []:
            locations.extend(
                entry.get("location") or {} for entry in thread.get("locations") or []
            )
    for stack in result.get("stacks") or []:
        locations.extend(
            frame.get("location") or {} for frame in stack.get("frames") or []
        )
    return sorted({_path(location) for location in locations}) or ["unknown"]


def evaluate(report: dict[str, Any], language: str) -> tuple[str, bool]:
    if not isinstance(report, dict):
        raise TypeError("SARIF report must be an object")
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("SARIF report has no analysis runs; fail closed")
    findings: list[tuple[str, list[str], float | None, str, str]] = []
    for run in runs:
        if not isinstance(run, dict):
            raise TypeError("SARIF run is malformed")
        driver = (run.get("tool") or {}).get("driver")
        if not isinstance(driver, dict) or not driver.get("name"):
            raise ValueError("SARIF run has no analyzer identity")
        results = run.get("results")
        if not isinstance(results, list):
            raise TypeError("SARIF run has no result list")
        rules = {
            rule.get("id"): rule
            for rule in driver.get("rules", [])
            if isinstance(rule, dict)
        }
        for result in results:
            if not isinstance(result, dict):
                raise TypeError("SARIF result is malformed")
            rule_id = result.get("ruleId", "unknown")
            rule = rules.get(rule_id, {})
            properties = rule.get("properties") or {}
            raw_score = properties.get(
                "security-severity",
                (result.get("properties") or {}).get("security-severity"),
            )
            try:
                score = float(raw_score) if raw_score is not None else None
            except (TypeError, ValueError):
                score = None
            message = ((result.get("message") or {}).get("text") or "").replace(
                "\n", " "
            )
            findings.append(
                (
                    rule_id,
                    _all_paths(result),
                    score,
                    result.get("level", "warning"),
                    message[:240],
                )
            )

    scoped = [
        finding
        for finding in findings
        if any(
            path.removeprefix("file://").lstrip("/").endswith(prefix)
            or prefix.endswith("/")
            and prefix in path.removeprefix("file://").lstrip("/")
            for path in finding[1]
            for prefix in SECURITY_SCOPE
        )
    ]
    blocking = [
        finding
        for finding in scoped
        if (finding[2] is not None and finding[2] >= 7.0)
        or (finding[2] is None and finding[3] in {"error", "warning"})
    ]
    lines = [
        f"### CodeQL security-extended: {language}",
        f"- SARIF results: **{len(findings)}**",
        f"- Staging/shared-security findings requiring review: **{len(scoped)}**",
        f"- Blocking high/critical or unscored warnings: **{len(blocking)}**",
        "- A successful automated scan is not an independent human security approval.",
    ]
    if blocking:
        lines.append("#### Blocking staging/shared-security findings")
        selected = blocking[:25]
    else:
        lines.append("#### All findings")
        selected = sorted(
            findings, key=lambda item: (item not in scoped, item[1], item[0])
        )[:30]
    for rule_id, paths, score, level, message in selected:
        severity = f"{score:.1f}" if score is not None else "unscored"
        lines.append(
            f"- {rule_id}: `{', '.join(paths[:5])}` ({severity}; {level}) - {message}"
        )
    return "\n".join(lines) + "\n", bool(blocking)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("language")
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    if not args.report.is_file():
        parser.error("CodeQL SARIF missing; refuse to certify an unseen scan")
    summary, blocking = evaluate(
        json.loads(args.report.read_text(encoding="utf-8")), args.language
    )
    print(summary, end="")
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(summary)
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
