"""Offline, synthetic-only claim-evidence review workbench for CXOps AI.

Unlike the existing RAG evaluation's term/citation matching, this creates an
independent per-claim human review sheet, validates reviewer evidence excerpts,
and compares its *self-attested* adjudicated labels with the existing C5 lexical
guard. It NEVER calls a model/provider, touches a database, handles customers,
or claims to verify the identity or independence of real reviewers.

The JSON scorecard deliberately never contains claim/source text, reviewer
names, or notes. A GitHub Actions passing test is NOT approval to auto-publish.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.public_answer_evidence import contains_direct_source_contradiction

SCHEMA = "cxops-claim-review-dataset-v1"
REPORT_SCHEMA = "cxops-claim-review-report-v1"
LABELS = frozenset({"supported", "contradicted", "unsupported", "unclear"})
ID = re.compile(r"[a-z][a-z0-9-]{0,79}\Z")
SOURCE_ID = re.compile(r"S[1-9][0-9]*\Z")
CITE = re.compile(r"\[S[1-9][0-9]*\]")
MAX_CASES = 100
MAX_CLAIMS = 500
MAX_SOURCE_CHARS = 5_000

SHEET_FIELDS = [
    "dataset_id", "review_key", "fingerprint", "tenant_id", "case_id",
    "claim_id", "claim_text", "answer_text", "cited_source_ids",
    "cited_source_passages", "reviewer_1", "reviewer_1_label",
    "reviewer_1_evidence", "reviewer_1_note", "reviewer_2",
    "reviewer_2_label", "reviewer_2_evidence", "reviewer_2_note",
    "adjudicator", "adjudicated_label", "adjudication_note",
]
EDITABLE = SHEET_FIELDS[SHEET_FIELDS.index("reviewer_1"):]


class ReviewValidationError(ValueError):
    """Untrusted worksheet/dataset violates the bounded review contract."""


def _reject(message: str) -> None:
    # Never echo claim bodies, source excerpts or free-text reviewer fields.
    raise ReviewValidationError(message)


def _nonempty(value: object, *, maximum: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _reject(f"{field}: missing or outside size limit")
    return value.strip()


def _digest(data: object) -> str:
    canonical = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _json_no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            _reject("dataset JSON contains duplicate object keys")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    _reject("dataset contains non-finite JSON number")


def load_dataset(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, OSError, json.JSONDecodeError) as exc:
        raise ReviewValidationError("cannot read valid UTF-8 dataset JSON") from exc

    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        _reject("unknown claim-review dataset schema")
    if data.get("dataset_kind") != "synthetic":
        _reject("only synthetic datasets are permitted in this offline tool")
    dataset_id = _nonempty(data.get("dataset_id"), maximum=80, field="dataset_id")
    if not ID.fullmatch(dataset_id):
        _reject("dataset_id must be a safe slug")

    cases = data.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        _reject("dataset case count out of bounds")

    seen_cases: set[str] = set()
    claimed_total = 0
    for case in cases:
        if not isinstance(case, dict):
            _reject("case must be an object")
        case_id = _nonempty(case.get("case_id"), maximum=80, field="case_id")
        tenant_id = _nonempty(case.get("tenant_id"), maximum=80, field="tenant_id")
        if not ID.fullmatch(case_id) or case_id in seen_cases:
            _reject("duplicate or malformed case_id")
        if not ID.fullmatch(tenant_id) or not tenant_id.endswith("-synthetic"):
            _reject("tenant must be a fictional synthetic-only slug")
        seen_cases.add(case_id)
        _nonempty(case.get("question"), maximum=1_000, field="question")
        answer = _nonempty(case.get("answer"), maximum=5_000, field="answer")

        sources = case.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
            _reject("each case needs 1-20 versioned sources")
        sources_by_id: dict[str, dict] = {}
        for source in sources:
            if not isinstance(source, dict):
                _reject("source must be an object")
            source_id = _nonempty(
                source.get("source_id"), maximum=12, field="source_id"
            )
            if not SOURCE_ID.fullmatch(source_id) or source_id in sources_by_id:
                _reject("invalid or duplicated source_id")
            _nonempty(source.get("revision"), maximum=100, field="revision")
            _nonempty(source.get("text"), maximum=MAX_SOURCE_CHARS, field="source_text")
            sources_by_id[source_id] = source

        claims = case.get("claims")
        if not isinstance(claims, list) or not 1 <= len(claims) <= 30:
            _reject("each case needs 1-30 individually reviewable claims")
        seen_claims: set[str] = set()
        answer_without_citations = " ".join(CITE.sub("", answer).split())
        for claim in claims:
            if not isinstance(claim, dict):
                _reject("claim must be an object")
            claim_id = _nonempty(
                claim.get("claim_id"), maximum=80, field="claim_id"
            )
            claim_text = _nonempty(
                claim.get("text"), maximum=1_000, field="claim_text"
            )
            if not ID.fullmatch(claim_id) or claim_id in seen_claims:
                _reject("duplicate or malformed claim_id")
            seen_claims.add(claim_id)
            if " ".join(claim_text.split()) not in answer_without_citations:
                _reject("claim text is not an exact excerpt of this answer")
            cited = claim.get("cited_source_ids")
            if (
                not isinstance(cited, list)
                or not 1 <= len(cited) <= 10
                or any(
                    not isinstance(source_id, str)
                    or source_id not in sources_by_id
                    or f"[{source_id}]" not in answer
                    for source_id in cited
                )
                or len(set(cited)) != len(cited)
            ):
                _reject("claim has missing, duplicate or foreign citations")
            claimed_total += 1
            if claimed_total > MAX_CLAIMS:
                _reject("dataset exceeds claim budget")
    return data


def _csv_safe(value: str) -> str:
    # Excel/Sheets evaluate cells starting with these characters as formulas.
    # CSV quoting alone does not stop spreadsheet formula injection.
    if value and value[0] in "=+-@\t\r":
        return "'" + value
    return value


def _cited_sources(case: dict, claim: dict) -> list[dict]:
    by_id = {source["source_id"]: source for source in case["sources"]}
    return [by_id[source_id] for source_id in claim["cited_source_ids"]]


def _worksheet_rows(data: dict) -> list[dict[str, str]]:
    rows = []
    for case in data["cases"]:
        for claim in case["claims"]:
            sources = _cited_sources(case, claim)
            passages = "\n\n".join(
                f"[{source['source_id']}] REV {source['revision']}\n{source['text']}"
                for source in sources
            )
            digest = _digest({
                "dataset_id": data["dataset_id"],
                "tenant_id": case["tenant_id"],
                "case_id": case["case_id"],
                "question": case["question"],
                "answer": case["answer"],
                "claim": claim,
                "sources": sources,
            })
            row = {
                "dataset_id": data["dataset_id"],
                "review_key": f"{case['case_id']}:{claim['claim_id']}",
                "fingerprint": digest,
                "tenant_id": case["tenant_id"],
                "case_id": case["case_id"],
                "claim_id": claim["claim_id"],
                "claim_text": _csv_safe(claim["text"]),
                "answer_text": _csv_safe(case["answer"]),
                "cited_source_ids": ",".join(claim["cited_source_ids"]),
                "cited_source_passages": _csv_safe(passages),
            }
            row.update(dict.fromkeys(EDITABLE, ""))
            rows.append(row)
    return rows


def prepare_sheet(data: dict, path: Path) -> int:
    # NEVER overwrite a partly filled worksheet, even in a test environment.
    expected = _worksheet_rows(data)
    with path.open("x", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(expected)
    return len(expected)


def _as_review_label(value: str, field: str) -> str:
    if value and value not in LABELS:
        _reject(f"{field}: unrecognized claim label")
    return value


def _validate_reviewer(
    row: dict, number: int, passages: list[str],
) -> str | None:
    prefix = f"reviewer_{number}"
    identity = row[prefix].strip()
    label = _as_review_label(row[f"{prefix}_label"].strip(), prefix)
    evidence = row[f"{prefix}_evidence"].strip()
    note = row[f"{prefix}_note"].strip()

    if not identity and any((label, evidence, note)):
        _reject(f"{prefix}: annotations without reviewer identity")
    if identity and len(identity) > 80:
        _reject(f"{prefix}: identity too long")
    if (evidence and len(evidence) > 1000) or len(note) > 500:
        _reject(f"{prefix}: review note/evidence outside size limit")
    if not label:
        return None
    if not identity:
        _reject(f"{prefix}: missing reviewer identity")
    if label in {"supported", "contradicted"}:
        if len(evidence) < 8 or not any(
            evidence in passage for passage in passages
        ):
            _reject(f"{prefix}: evidence must quote a cited-source excerpt")
    elif not note or len(note) < 10:
        _reject(f"{prefix}: unsupported or unclear needs a rationale")
    return label


def _read_verified_rows(data: dict, sheet: Path) -> list[dict[str, str]]:
    expected = _worksheet_rows(data)
    expected_by_id = {row["review_key"]: row for row in expected}
    try:
        # Bound memory while reading an untrusted (possibly spreadsheet-edited)
        # file. Review material is synthetic and at most 500 small claims.
        if sheet.stat().st_size > 12_000_000:
            _reject("worksheet exceeds offline file-size budget")
        with sheet.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != SHEET_FIELDS:
                _reject("worksheet columns differ from the expected review schema")
            actual = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ReviewValidationError("cannot read review worksheet") from exc

    if len(actual) != len(expected):
        _reject("worksheet does not contain the exact expected claim count")
    seen: set[str] = set()
    for row in actual:
        if None in row or any(v is None for v in row.values()):
            _reject("worksheet row has an unexpected number of columns")
        key = row["review_key"]
        if key not in expected_by_id or key in seen:
            _reject("worksheet has unexpected or repeated claim identifiers")
        seen.add(key)
        reference = expected_by_id[key]
        if any(
            row[field] != reference[field]
            for field in SHEET_FIELDS
            if field not in EDITABLE
        ):
            _reject("worksheet dataset/fingerprint/source content was modified")
    return actual


def combine_sheets(
    data: dict, reviewer_one: Path, reviewer_two: Path, output: Path,
) -> int:
    """Join separately completed reviewer slots, never overwrite either.

    Reviewers see no other reviewer's annotations when they work from
    separate *copies* of the initially blank template. The join checks
    every source fingerprint and refuses cross-slot edits or adjudication.
    A human resolves disputes on the combined copy afterward.
    """
    left = _read_verified_rows(data, reviewer_one)
    right = _read_verified_rows(data, reviewer_two)
    one_by_id = {row["review_key"]: row for row in left}
    two_by_id = {row["review_key"]: row for row in right}
    combined = []
    for key in one_by_id:
        a, b = one_by_id[key], two_by_id[key]
        slot_1 = [name for name in EDITABLE if name.startswith("reviewer_1")]
        slot_2 = [name for name in EDITABLE if name.startswith("reviewer_2")]
        other = [name for name in EDITABLE if name not in slot_1 + slot_2]
        if any(a[name] for name in slot_2 + other):
            _reject("reviewer-one worksheet edits fields outside its slot")
        if any(b[name] for name in slot_1 + other):
            _reject("reviewer-two worksheet edits fields outside its slot")
        sources = _cited_sources(
            next(c for c in data["cases"] if c["case_id"] == a["case_id"]),
            next(
                claim
                for claim in next(
                    c for c in data["cases"] if c["case_id"] == a["case_id"]
                )["claims"]
                if claim["claim_id"] == a["claim_id"]
            ),
        )
        passages = [source["text"] for source in sources]
        if _validate_reviewer(a, 1, passages) is None:
            _reject("reviewer-one worksheet contains unfinished labels")
        if _validate_reviewer(b, 2, passages) is None:
            _reject("reviewer-two worksheet contains unfinished labels")
        if a["reviewer_1"].casefold() == b["reviewer_2"].casefold():
            _reject("same claimed identity on independently reviewed sheets")
        merged = dict(a)
        merged.update({field: b[field] for field in slot_2})
        combined.append(merged)
    with output.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(combined)
    return len(combined)


def score_sheet(data: dict, sheet: Path) -> dict[str, Any]:
    expected = _worksheet_rows(data)
    actual = _read_verified_rows(data, sheet)
    labels: Counter[str] = Counter()
    confusion: Counter[str] = Counter()
    pending = 0
    disputed = 0
    unclear = 0
    judged = 0
    candidate_risk_keys: list[str] = []
    for row in actual:
        key = row["review_key"]
        case_id, claim_id = key.split(":")
        case = next(x for x in data["cases"] if x["case_id"] == case_id)
        claim = next(x for x in case["claims"] if x["claim_id"] == claim_id)
        sources = _cited_sources(case, claim)
        passages = [source["text"] for source in sources]

        first = _validate_reviewer(row, 1, passages)
        second = _validate_reviewer(row, 2, passages)
        reviewer1 = row["reviewer_1"].strip()
        reviewer2 = row["reviewer_2"].strip()
        if reviewer1 and reviewer2 and reviewer1.casefold() == reviewer2.casefold():
            _reject("reviewers cannot self-attest as the same identity")

        resolved: str | None = None
        adjudicator = row["adjudicator"].strip()
        adjudicated = _as_review_label(
            row["adjudicated_label"].strip(), "adjudicated_label"
        )
        resolution_note = row["adjudication_note"].strip()
        if not first or not second:
            if any((adjudicator, adjudicated, resolution_note)):
                _reject("adjudication cannot precede both independent labels")
            pending += 1
            continue
        if first == second:
            if any((adjudicator, adjudicated, resolution_note)):
                _reject("matching labels do not need an adjudicator")
            resolved = first
        elif not (adjudicator and adjudicated and resolution_note.strip()):
            disputed += 1
            continue
        else:
            if (
                adjudicator.casefold() in {reviewer1.casefold(), reviewer2.casefold()}
                or len(adjudicator) > 80
                or len(resolution_note) > 500
            ):
                _reject("adjudicator must be distinct and explanation bounded")
            resolved = adjudicated

        labels[resolved] += 1
        if resolved == "unclear":
            unclear += 1
            continue
        judged += 1
        # The C5 deterministic guard can only detect direct contradictions.
        # A *not-blocked* candidate is NOT proven supported.
        guard_answer = claim["text"] + " " + " ".join(
            f"[{source_id}]" for source_id in claim["cited_source_ids"]
        )
        blocked = contains_direct_source_contradiction(
            guard_answer,
            [{"source_id": x["source_id"], "content": x["text"]} for x in sources],
        )
        bad = resolved in {"contradicted", "unsupported"}
        if bad and blocked:
            confusion["unsafe_blocked"] += 1
        elif bad:
            confusion["unsafe_candidate"] += 1
            candidate_risk_keys.append(key)
        elif blocked:
            confusion["supported_blocked"] += 1
        else:
            confusion["supported_candidate"] += 1

    complete = pending == disputed == 0
    unsafe_total = confusion["unsafe_blocked"] + confusion["unsafe_candidate"]
    supported_total = confusion["supported_blocked"] + confusion["supported_candidate"]
    return {
        "schema": REPORT_SCHEMA,
        "dataset_id": data["dataset_id"],
        "dataset_digest": _digest(data),
        "review_state": (
            "self_attested_complete_not_certified" if complete
            else "incomplete_or_disputed"
        ),
        "independent_human_identity_verified": False,
        "production_certified": False,
        "counts": {
            "claims": len(expected),
            "resolved": sum(labels.values()),
            "pending": pending,
            "disputed": disputed,
            "unclear": unclear,
            "scored": judged,
            "labels": {name: labels[name] for name in sorted(LABELS)},
        },
        "guard_comparison": {
            "unsafe_blocked": confusion["unsafe_blocked"],
            "unsafe_candidate": confusion["unsafe_candidate"],
            "supported_blocked": confusion["supported_blocked"],
            "supported_candidate": confusion["supported_candidate"],
            "unsafe_miss_rate": (
                round(confusion["unsafe_candidate"] / unsafe_total, 4)
                if unsafe_total else None
            ),
            "supported_false_block_rate": (
                round(confusion["supported_blocked"] / supported_total, 4)
                if supported_total else None
            ),
            "unsafe_candidate_claim_keys": sorted(candidate_risk_keys),
        },
        "limitations": [
            "Reviewer identities are free-text and cannot be independently authenticated.",
            "Scores describe this synthetic worksheet, not real AI-model accuracy.",
            "The guard checks only direct English polarity; no semantic entailment proof.",
            "No score automatically authorizes publication or production release.",
        ],
    }


def _command_line(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/chatbot_claim_review_synthetic.json"),
        help="Versioned synthetic-only claim+source fixture; no customer content",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Validate exact synthetic dataset contract")
    prepare = sub.add_parser("prepare", help="Create non-overwriting, empty human review CSV")
    prepare.add_argument("--out", type=Path, required=True)
    combine = sub.add_parser(
        "combine", help="Merge separate reviewer A/B sheets, no labels inferred"
    )
    combine.add_argument("--reviewer-one", type=Path, required=True)
    combine.add_argument("--reviewer-two", type=Path, required=True)
    combine.add_argument("--out", type=Path, required=True)
    score = sub.add_parser("score", help="Check self-attested reviews and score C5 guard")
    score.add_argument("--sheet", type=Path, required=True)
    score.add_argument("--report", type=Path)
    score.add_argument(
        "--require-complete", action="store_true",
        help="Exit 2 until every claim has two reviewers and disputes adjudicated",
    )
    args = parser.parse_args(argv)
    try:
        data = load_dataset(args.dataset)
        if args.command == "validate":
            print(json.dumps({
                "dataset_id": data["dataset_id"],
                "synthetic": True,
                "cases": len(data["cases"]),
                "claims": sum(len(c["claims"]) for c in data["cases"]),
                "human_review_certified": False,
            }, sort_keys=True))
            return 0
        if args.command == "prepare":
            count = prepare_sheet(data, args.out)
            print(f"Created {count} unlabelled review rows; NOT CERTIFIED")
            return 0
        if args.command == "combine":
            count = combine_sheets(
                data, args.reviewer_one, args.reviewer_two, args.out
            )
            print(f"Merged {count} self-attested review rows; NOT CERTIFIED")
            return 0
        report = score_sheet(data, args.sheet)
        payload = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        if args.report:
            with args.report.open("x", encoding="utf-8") as output:
                output.write(payload)
        else:
            print(payload, end="")
        if args.require_complete and report["review_state"] != "self_attested_complete_not_certified":
            return 2
        return 0
    except (ReviewValidationError, FileExistsError) as exc:
        print(f"Claim review refused: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_command_line())
