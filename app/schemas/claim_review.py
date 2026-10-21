"""C11 synthetic-only tenant-private reviewer API schemas.

No consumer-provided tenant ID, actor ID, database foreign keys, arbitrary
dataset JSON, AI prompts or customer messages. All response shapes are
explicit and exclude evidence quotations/rationale, reviewer subjects,
source text, release approval status or other reviewers' private labels.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReviewLabel = Literal["supported", "contradicted", "unsupported", "unclear"]


class SyntheticBatchRead(BaseModel):
    id: int
    dataset_id: Literal["c6-fictional-faq-20261021"]
    item_count: Literal[12]
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class ReviewerAssignmentCreate(BaseModel):
    reviewer_one_subject: str = Field(min_length=1, max_length=255)
    reviewer_two_subject: str = Field(min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid")


class ReviewerAssignmentResult(BaseModel):
    batch_id: int
    assigned_slots: Literal[2]

    model_config = ConfigDict(extra="forbid")


class AssignedClaimRead(BaseModel):
    review_key: str
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    completed: bool

    model_config = ConfigDict(extra="forbid")


class AssignedClaimQueue(BaseModel):
    batch_id: int
    items: list[AssignedClaimRead]
    total: int

    model_config = ConfigDict(extra="forbid")


class ReviewJudgmentCreate(BaseModel):
    label: ReviewLabel
    evidence_excerpt: str | None = Field(default=None, max_length=1000)
    rationale: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


class ClaimJudgmentRecorded(BaseModel):
    id: int
    batch_id: int
    review_key: str
    event_kind: Literal["review", "adjudication"]
    label: ReviewLabel
    created_at: datetime

    model_config = ConfigDict(extra="forbid")
