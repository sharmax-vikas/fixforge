from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


JobState = Literal[
    "intake_running",
    "awaiting_analysis_approval",
    "generation_running",
    "awaiting_patch_approval",
    "evaluation_running",
    "awaiting_selection",
    "awaiting_pr_approval",
    "pr_running",
    "needs_reproduction",
    "completed",
    "failed",
]


class CreateJobRequest(BaseModel):
    issue_url: HttpUrl


class ApprovalRequest(BaseModel):
    action: Literal["generate_patches", "evaluate_patches", "select_best_patch", "create_draft_pr"]
    candidate_id: str | None = None


class Issue(BaseModel):
    url: str
    number: int
    title: str
    body: str
    repository: str
    labels: list[str] = Field(default_factory=list)


class RepositoryContext(BaseModel):
    fork: str
    upstream: str
    default_branch: str
    revision: str
    checkout: str
    structure: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    id: str
    summary: str
    root_cause: str
    confidence: int = Field(ge=0, le=100)
    patch: str
    test_command: str
    status: Literal["proposed", "applied", "tested", "rejected", "selected"] = "proposed"
    modified_files: int = 0
    test_passed: bool | None = None
    test_output: str = ""
    score: float | None = None
    workspace: str = ""


class PullRequestDraft(BaseModel):
    title: str
    body: str
    url: str | None = None


class Metrics(BaseModel):
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    token_usage: int | None = None
    elapsed_seconds: float | None = None


class Job(BaseModel):
    id: str
    state: JobState = "intake_running"
    issue: Issue | None = None
    repository: RepositoryContext | None = None
    suspected_root_cause: str = ""
    root_cause_confidence: int = 0
    candidates: list[Candidate] = Field(default_factory=list)
    selected_candidate_id: str | None = None
    pull_request: PullRequestDraft | None = None
    activity: str = "Queued"
    error: str | None = None
    metrics: Metrics = Field(default_factory=Metrics)
