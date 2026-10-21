"""C6 synthetic reviewer workbench: no real reviewers or provider data.

The fixture reviewers below are fabricated *test doubles* exclusively for
checking scoring, adjudication and release-hold behavior. Their fields are
NOT real or independently verified human evidence labels.
"""
from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.chatbot_claim_review import (
    EDITABLE,
    SHEET_FIELDS,
    ReviewValidationError,
    combine_sheets,
    _command_line,
    _worksheet_rows,
    load_dataset,
    prepare_sheet,
    score_sheet,
)

DATA_PATH = Path(__file__).resolve().parents[1] / "evals/chatbot_claim_review_synthetic.json"
EXPECT_LABELS = {
    "aster-direct-contradiction:c1": "contradicted",
    "aster-paraphrase:c1": "contradicted",
    "aster-brake-checks:c1": "supported",
    "aster-unsupported-sale:c1": "unsupported",
    "birch-refund-reversed:c1": "contradicted",
    "birch-refund-supported:c1": "supported",
    "aster-hours:c1": "supported",
    "aster-farsi-incorrect:c1": "contradicted",
    "aster-two-supported-claims:c1": "supported",
    "aster-two-supported-claims:c2": "supported",
    "aster-uncited-other-doc:c1": "unclear",
    "birch-unclear-staff-time:c1": "unsupported",
}
# No human-labelled accuracy claim follows from author-written fixture labels.
assert len(EXPECT_LABELS) == 12


def _data() -> dict:
    return load_dataset(DATA_PATH)


def _rows(sheet: Path) -> list[dict[str, str]]:
    with sheet.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _write(sheet: Path, rows: list[dict[str, str]]) -> None:
    with sheet.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _complete_fake_reviews(rows: list[dict[str, str]]) -> None:
    for row in rows:
        expected = EXPECT_LABELS[row["review_key"]]
        row["reviewer_1"] = "fixture-only-reviewer-A"
        row["reviewer_2"] = "fixture-only-reviewer-B"
        row["reviewer_1_label"] = expected
        row["reviewer_2_label"] = expected
        if expected in {"supported", "contradicted"}:
            # A quote proves the human actually supplied a string that exists
            # in a cited source passage; it does NOT prove label correctness.
            cited = row["cited_source_passages"].split("\n", 1)
            quote = cited[1][:80] if len(cited) == 2 else ""
            row["reviewer_1_evidence"] = quote
            row["reviewer_2_evidence"] = quote
        else:
            row["reviewer_1_note"] = (
                "No supporting cited passage for this precise claim."
            )
            row["reviewer_2_note"] = (
                "No supporting cited passage for this precise claim."
            )


def test_dataset_is_exclusively_fictional_and_claim_bound() -> None:
    data = _data()
    assert data["dataset_kind"] == "synthetic"
    assert len(data["cases"]) == 11
    assert len(_worksheet_rows(data)) == len(EXPECT_LABELS)
    assert {row["review_key"] for row in _worksheet_rows(data)} == set(EXPECT_LABELS)
    assert all(case["tenant_id"].endswith("-synthetic") for case in data["cases"])


def test_prepare_exports_unreviewed_nonoverwriting_csv(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    count = prepare_sheet(_data(), path)
    assert count == 12
    rows = _rows(path)
    assert len(rows) == 12
    assert list(rows[0]) == SHEET_FIELDS
    assert all(not row[field] for row in rows for field in EDITABLE)
    assert all(len(row["fingerprint"]) == 64 for row in rows)
    assert all(row["cited_source_passages"] for row in rows)
    with pytest.raises(FileExistsError):
        prepare_sheet(_data(), path)


def test_empty_review_never_reports_quality_certification(tmp_path: Path) -> None:
    sheet = tmp_path / "review.csv"
    prepare_sheet(_data(), sheet)
    report = score_sheet(_data(), sheet)
    assert report["review_state"] == "incomplete_or_disputed"
    assert report["production_certified"] is False
    assert report["independent_human_identity_verified"] is False
    assert report["counts"]["claims"] == 12
    assert report["counts"]["pending"] == 12
    assert report["counts"]["resolved"] == 0
    assert report["counts"]["scored"] == 0
    assert report["guard_comparison"]["unsafe_miss_rate"] is None


def test_two_fake_reviews_are_scored_but_never_certified(tmp_path: Path) -> None:
    sheet = tmp_path / "review.csv"
    prepare_sheet(_data(), sheet)
    rows = _rows(sheet)
    _complete_fake_reviews(rows)
    _write(sheet, rows)
    report = score_sheet(_data(), sheet)
    assert report["review_state"] == "self_attested_complete_not_certified"
    assert report["production_certified"] is False
    assert report["independent_human_identity_verified"] is False
    assert report["counts"]["resolved"] == 12
    assert report["counts"]["pending"] == 0
    assert report["counts"]["disputed"] == 0
    assert report["counts"]["unclear"] == 1
    assert report["counts"]["scored"] == 11
    assert report["counts"]["labels"]["supported"] == 5
    assert report["counts"]["labels"]["contradicted"] == 4
    assert report["counts"]["labels"]["unsupported"] == 2
    assert report["guard_comparison"]["unsafe_blocked"] == 2
    assert report["guard_comparison"]["unsafe_candidate"] == 4
    assert report["guard_comparison"]["supported_candidate"] == 5
    assert report["guard_comparison"]["supported_blocked"] == 0
    assert report["guard_comparison"]["unsafe_miss_rate"] == 0.6667
    assert report["guard_comparison"]["supported_false_block_rate"] == 0.0
    safe_report = json.dumps(report, ensure_ascii=False)
    assert "fixture-only-reviewer" not in safe_report
    assert "Aster bicycle repairs are" not in safe_report
    assert "Refund approval is" not in safe_report


def test_agreement_dispute_must_be_resolved_by_distinct_adjudicator(
    tmp_path: Path,
) -> None:
    sheet = tmp_path / "review.csv"
    prepare_sheet(_data(), sheet)
    rows = _rows(sheet)
    _complete_fake_reviews(rows)
    rows[0]["reviewer_1_label"] = "supported"
    _write(sheet, rows)
    report = score_sheet(_data(), sheet)
    assert report["counts"]["disputed"] == 1
    assert report["review_state"] == "incomplete_or_disputed"

    rows[0]["adjudicator"] = "fixture-only-adjudicator-C"
    rows[0]["adjudicated_label"] = "contradicted"
    rows[0]["adjudication_note"] = "The fictional cited policy explicitly negates free repair."
    _write(sheet, rows)
    adjudicated = score_sheet(_data(), sheet)
    assert adjudicated["review_state"] == "self_attested_complete_not_certified"
    assert adjudicated["guard_comparison"]["unsafe_blocked"] == 2


@pytest.mark.parametrize(
    "mutator",
    [
        lambda rows: rows[0].update({"fingerprint": "0" * 64}),
        lambda rows: rows[0].update({"cited_source_passages": "tampered"}),
        lambda rows: rows[0].update({"dataset_id": "other-dataset"}),
        lambda rows: rows[0].update({"claim_text": "tampered"}),
        lambda rows: rows.append(rows[0].copy()),
        lambda rows: rows.pop(),
    ],
    ids=["stale-hash", "tampered-source", "wrong-dataset", "tampered-claim",
         "extra-row", "missing-row"],
)
def test_stale_or_modified_source_worksheet_is_refused(
    tmp_path: Path, mutator,
) -> None:
    sheet = tmp_path / "review.csv"
    prepare_sheet(_data(), sheet)
    rows = _rows(sheet)
    mutator(rows)
    _write(sheet, rows)
    with pytest.raises(ReviewValidationError):
        score_sheet(_data(), sheet)


def test_duplicate_claim_identifier_even_with_correct_row_count_is_refused(
    tmp_path: Path,
) -> None:
    sheet = tmp_path / "review.csv"
    prepare_sheet(_data(), sheet)
    rows = _rows(sheet)
    rows[1] = rows[0].copy()
    _write(sheet, rows)
    with pytest.raises(ReviewValidationError):
        score_sheet(_data(), sheet)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewer_2", "fixture-only-reviewer-A"),
        ("reviewer_1_label", "anything"),
        ("reviewer_1_evidence", "invented provider receipt"),
        ("reviewer_1_note", "x" * 501),
    ],
)
def test_unverifiable_reviewer_identity_or_evidence_is_rejected(
    tmp_path: Path, field: str, value: str,
) -> None:
    sheet = tmp_path / "review.csv"
    prepare_sheet(_data(), sheet)
    rows = _rows(sheet)
    _complete_fake_reviews(rows)
    rows[0][field] = value
    _write(sheet, rows)
    with pytest.raises(ReviewValidationError):
        score_sheet(_data(), sheet)


def test_unsupported_claim_requires_an_actual_review_rationale(
    tmp_path: Path,
) -> None:
    sheet = tmp_path / "review.csv"
    prepare_sheet(_data(), sheet)
    rows = _rows(sheet)
    _complete_fake_reviews(rows)
    target = next(x for x in rows if x["review_key"] == "aster-unsupported-sale:c1")
    target["reviewer_2_note"] = ""
    _write(sheet, rows)
    with pytest.raises(ReviewValidationError):
        score_sheet(_data(), sheet)


def test_no_review_claims_can_be_self_certified_by_changing_dataset_kind(
    tmp_path: Path,
) -> None:
    data = deepcopy(_data())
    data["dataset_kind"] = "customer"
    path = tmp_path / "customer.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReviewValidationError, match="only synthetic"):
        load_dataset(path)


def test_unknown_foreign_source_reference_is_rejected(tmp_path: Path) -> None:
    data = deepcopy(_data())
    data["cases"][0]["claims"][0]["cited_source_ids"] = ["S99"]
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReviewValidationError, match="foreign citations"):
        load_dataset(path)


def test_duplicate_json_key_is_not_silently_overridden(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema": "x", "schema": "y"}', encoding="utf-8")
    with pytest.raises(ReviewValidationError, match="duplicate object keys"):
        load_dataset(path)


def test_formula_like_cells_are_escaped_in_spreadsheet_output() -> None:
    data = deepcopy(_data())
    case = data["cases"][0]
    case["answer"] = "=HYPERLINK(\"https://bad.invalid\") [S1]"
    case["claims"][0]["text"] = "=HYPERLINK(\"https://bad.invalid\")"
    row = next(x for x in _worksheet_rows(data) if x["case_id"] == case["case_id"])
    assert row["claim_text"].startswith("'=")
    assert row["answer_text"].startswith("'=")


def test_cli_incomplete_reviews_exit_nonzero_in_strict_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    sheet = tmp_path / "review.csv"
    assert _command_line(["--dataset", str(DATA_PATH), "validate"]) == 0
    assert _command_line([
        "--dataset", str(DATA_PATH), "prepare", "--out", str(sheet)
    ]) == 0
    assert _command_line([
        "--dataset", str(DATA_PATH), "score", "--sheet", str(sheet),
        "--require-complete",
    ]) == 2
    captured = capsys.readouterr().out
    assert "incomplete_or_disputed" in captured
    assert '"production_certified": false' in captured
    assert "fixture-only-reviewer" not in captured


def test_cli_report_never_overwrites_prior_review_or_score(tmp_path: Path) -> None:
    sheet = tmp_path / "review.csv"
    report = tmp_path / "summary.json"
    prepare_sheet(_data(), sheet)
    assert _command_line([
        "--dataset", str(DATA_PATH), "score", "--sheet", str(sheet),
        "--report", str(report),
    ]) == 0
    with pytest.raises(FileExistsError):
        prepare_sheet(_data(), sheet)
    assert _command_line([
        "--dataset", str(DATA_PATH), "score", "--sheet", str(sheet),
        "--report", str(report),
    ]) == 2



def _separate_mock_reviewer_sheets(
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Entirely fictional reviewer assignments. They are NOT real humans."""
    left, right, merged = (
        tmp_path / "reviewer-one.csv",
        tmp_path / "reviewer-two.csv",
        tmp_path / "combined.csv",
    )
    prepare_sheet(_data(), left)
    prepare_sheet(_data(), right)
    for path, forbidden_prefix in [(left, "reviewer_2"), (right, "reviewer_1")]:
        rows = _rows(path)
        _complete_fake_reviews(rows)
        for row in rows:
            for field in EDITABLE:
                if field.startswith(forbidden_prefix):
                    row[field] = ""
        _write(path, rows)
    return left, right, merged


def test_separate_private_reviewer_sheets_combine_without_label_leak(
    tmp_path: Path,
) -> None:
    left, right, merged = _separate_mock_reviewer_sheets(tmp_path)
    assert all(not row["reviewer_2"] for row in _rows(left))
    assert all(not row["reviewer_1"] for row in _rows(right))
    assert combine_sheets(_data(), left, right, merged) == 12
    report = score_sheet(_data(), merged)
    assert report["review_state"] == "self_attested_complete_not_certified"
    assert report["production_certified"] is False
    with pytest.raises(FileExistsError):
        combine_sheets(_data(), left, right, merged)


def test_combine_rejects_cross_reviewer_edits(tmp_path: Path) -> None:
    left, right, merged = _separate_mock_reviewer_sheets(tmp_path)
    rows = _rows(left)
    rows[0]["reviewer_2"] = "not-independent"
    _write(left, rows)
    with pytest.raises(ReviewValidationError, match="outside its slot"):
        combine_sheets(_data(), left, right, merged)
    assert not merged.exists()


def test_combine_rejects_missing_and_stale_reviewer_data(tmp_path: Path) -> None:
    left, right, merged = _separate_mock_reviewer_sheets(tmp_path)
    rows = _rows(right)
    rows[0]["reviewer_2_label"] = ""
    _write(right, rows)
    with pytest.raises(ReviewValidationError, match="unfinished"):
        combine_sheets(_data(), left, right, merged)
    assert not merged.exists()

    rows = _rows(right)
    rows[0]["reviewer_2_label"] = "contradicted"
    rows[0]["fingerprint"] = "0" * 64
    _write(right, rows)
    with pytest.raises(ReviewValidationError, match="fingerprint"):
        combine_sheets(_data(), left, right, merged)
    assert not merged.exists()


def test_actual_independence_is_not_cryptographically_established(
    tmp_path: Path,
) -> None:
    left, right, merged = _separate_mock_reviewer_sheets(tmp_path)
    rows = _rows(right)
    rows[0]["reviewer_2"] = "fixture-only-reviewer-A"
    _write(right, rows)
    with pytest.raises(ReviewValidationError, match="same claimed identity"):
        combine_sheets(_data(), left, right, merged)
    # Different names only provide self-attestation. The tool cannot tell
    # whether two spreadsheets were really completed by two different people.
