import pytest

from scripts.check_phase1y_codeql_sarif import evaluate


def _sarif(*, paths, score=None, level="warning", stack_paths=()):
    locations = [{"physicalLocation": {"artifactLocation": {"uri": paths[0]}}}]
    related = [
        {"physicalLocation": {"artifactLocation": {"uri": path}}} for path in paths[1:2]
    ]
    flows = [
        {
            "threadFlows": [
                {
                    "locations": [
                        {
                            "location": {
                                "physicalLocation": {"artifactLocation": {"uri": path}}
                            }
                        }
                        for path in paths[2:]
                    ]
                }
            ]
        }
    ]
    properties = {} if score is None else {"security-severity": score}
    stacks = [
        {
            "frames": [
                {"location": {"physicalLocation": {"artifactLocation": {"uri": path}}}}
                for path in stack_paths
            ]
        }
    ]
    return {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "CodeQL",
                        "rules": [{"id": "test/rule", "properties": properties}],
                    }
                },
                "results": [
                    {
                        "ruleId": "test/rule",
                        "level": level,
                        "message": {"text": "test finding"},
                        "locations": locations,
                        "relatedLocations": related,
                        "codeFlows": flows,
                        "stacks": stacks,
                    }
                ],
            }
        ]
    }


def test_blocks_unscored_warning_in_related_shared_security_location():
    summary, blocking = evaluate(
        _sarif(paths=["other.py", "app/core/rbac.py"]), "python"
    )
    assert blocking is True
    assert "unscored" in summary


def test_blocks_high_severity_dataflow_into_staging_code():
    _summary, blocking = evaluate(
        _sarif(
            paths=["source.py", "sink.py", "frontend/src/lib/staging/auth.ts"],
            score="9.1",
        ),
        "javascript-typescript",
    )
    assert blocking is True


def test_non_security_info_result_is_reported_but_not_blocking():
    summary, blocking = evaluate(
        _sarif(paths=["docs/example.py"], level="note"), "python"
    )
    assert blocking is False
    assert "SARIF results: **1**" in summary


def test_stack_frame_security_location_is_gated():
    _summary, blocking = evaluate(
        _sarif(paths=["source.py"], stack_paths=["app/api/deps.py"]), "python"
    )
    assert blocking is True


@pytest.mark.parametrize("report", [{}, {"runs": []}, {"runs": [{}]}])
def test_missing_or_malformed_analysis_fails_closed(report):
    with pytest.raises((TypeError, ValueError)):
        evaluate(report, "python")
