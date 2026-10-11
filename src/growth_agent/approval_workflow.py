"""Multi-reviewer approval workflow layered on top of the immutable approval record.

The legacy ``approval`` module writes a single ``audit_approval.json`` file
that binds one reviewer to one final confirmation. The workflow captured here
lives next to that file in ``audit_workflow.json`` and tracks the lifecycle of
a marketing copy review: who picked it up, what comments they left, what
revisions were requested and returned, and who eventually signed off.

The original ``approve_audit`` / ``load_approved_copy`` contract is untouched.
This module is additive; consumers that do not know about it see the same
behavior as before. A reviewer flow that goes through here still ends by
delegating to ``approve_audit`` so that the immutability guarantees on
``AuditBundle`` continue to hold.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .approval import approve_audit
from .audit import AuditBundle


ApprovalState = Literal["submitted", "in_review", "changes_requested", "approved", "rejected"]

#: States in which a final approval decision is allowed.
_DECIDABLE_STATES: frozenset[str] = frozenset({"in_review"})

#: Maximum length constraints for human input.
_MAX_REVIEWER = 100
_MAX_COMMENT = 2000
_MAX_NOTES = 2000
_MAX_DIFF = 4000
_MAX_REASON = 1000


@dataclass
class ReviewComment:
    """A single comment a reviewer left while the copy was being reviewed."""

    author: str
    text: str
    created_at_utc: str
    comment_id: str
    phase: Literal["in_review", "post_approval"] = "in_review"

    def __post_init__(self) -> None:
        _text(self.author, "comment author", _MAX_REVIEWER)
        _text(self.text, "comment text", _MAX_COMMENT)
        if not isinstance(self.created_at_utc, str) or not self.created_at_utc:
            raise ValueError("comment created_at_utc must be a non-empty ISO-8601 string")
        _identifier(self.comment_id, "comment_id")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "ReviewComment":
        _check_fields(record, {"author", "text", "created_at_utc", "comment_id"}, {"phase"})
        return cls(
            author=str(record["author"]),
            text=str(record["text"]),
            created_at_utc=str(record["created_at_utc"]),
            comment_id=str(record["comment_id"]),
            phase=str(record.get("phase", "in_review")),
        )


@dataclass
class ModificationEntry:
    """Captures a revision request and the matching author-side change."""

    requested_by: str
    reason: str
    before_text: str
    after_text: str
    at_utc: str

    def __post_init__(self) -> None:
        _text(self.requested_by, "modification reviewer", _MAX_REVIEWER)
        _text(self.reason, "modification reason", _MAX_REASON)
        _text(self.before_text, "modification before_text", _MAX_DIFF)
        _text(self.after_text, "modification after_text", _MAX_DIFF)
        if not isinstance(self.at_utc, str) or not self.at_utc:
            raise ValueError("modification at_utc must be a non-empty ISO-8601 string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "ModificationEntry":
        _check_fields(record, {"requested_by", "reason", "before_text", "after_text", "at_utc"})
        return cls(
            requested_by=str(record["requested_by"]),
            reason=str(record["reason"]),
            before_text=str(record["before_text"]),
            after_text=str(record["after_text"]),
            at_utc=str(record["at_utc"]),
        )


@dataclass
class ApprovalWorkflow:
    """A multi-reviewer lifecycle for a single audit run."""

    run_id: str
    audit_digest: str
    state: ApprovalState = "submitted"
    reviewers: list[str] = field(default_factory=list)
    comments: list[ReviewComment] = field(default_factory=list)
    modifications: list[ModificationEntry] = field(default_factory=list)
    opened_at_utc: str = ""
    closed_at_utc: str | None = None
    final_reviewer: str | None = None
    final_notes: str = ""
    decision_scope: str = (
        "Multi-reviewer editorial approval recorded by the local demo; "
        "identity is not authenticated."
    )

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "run_id": self.run_id,
            "audit_digest": self.audit_digest,
            "state": self.state,
            "reviewers": list(self.reviewers),
            "comments": [comment.to_dict() for comment in self.comments],
            "modifications": [entry.to_dict() for entry in self.modifications],
            "opened_at_utc": self.opened_at_utc,
            "closed_at_utc": self.closed_at_utc,
            "final_reviewer": self.final_reviewer,
            "final_notes": self.final_notes,
            "decision_scope": self.decision_scope,
        }
        return record

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "ApprovalWorkflow":
        if not isinstance(record, dict):
            raise ValueError("Workflow record must be a JSON object")
        _check_fields(
            record,
            required={"run_id", "audit_digest", "state"},
            optional={
                "reviewers",
                "comments",
                "modifications",
                "opened_at_utc",
                "closed_at_utc",
                "final_reviewer",
                "final_notes",
                "decision_scope",
            },
        )
        reviewers = record.get("reviewers", [])
        if not isinstance(reviewers, list) or any(not isinstance(item, str) for item in reviewers):
            raise ValueError("reviewers must be a list of strings")
        comments = [ReviewComment.from_dict(item) for item in record.get("comments", [])]
        modifications = [ModificationEntry.from_dict(item) for item in record.get("modifications", [])]
        return cls(
            run_id=str(record["run_id"]),
            audit_digest=str(record["audit_digest"]),
            state=str(record["state"]),  # type: ignore[arg-type]
            reviewers=list(reviewers),
            comments=comments,
            modifications=modifications,
            opened_at_utc=str(record.get("opened_at_utc", "")),
            closed_at_utc=record.get("closed_at_utc"),
            final_reviewer=record.get("final_reviewer"),
            final_notes=str(record.get("final_notes", "")),
            decision_scope=str(record.get("decision_scope", cls.decision_scope)),
        )


def _check_fields(record: Any, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(record, dict):
        raise ValueError("Workflow record must be a JSON object")
    missing = required - set(record.keys())
    if missing:
        raise ValueError(f"Workflow record missing required field(s): {sorted(missing)}")
    if optional is not None:
        unexpected = set(record.keys()) - required - optional
        if unexpected:
            raise ValueError(f"Workflow record has unknown field(s): {sorted(unexpected)}")


def _identifier(value: Any, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}\Z", value):
        raise ValueError(f"{label} must be 1 to 80 chars of letters, digits, underscore or hyphen")


def _text(value: Any, label: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be a non-empty string of at most {maximum} characters")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workflow_path(run_dir: Path) -> Path:
    return run_dir / "audit_workflow.json"


def _bundle(run_dir: Path) -> tuple[AuditBundle, str]:
    bundle_path = run_dir / "audit_bundle.json"
    raw = bundle_path.read_bytes()
    bundle = AuditBundle.model_validate(json.loads(raw))
    if bundle.status != "pending_review" or bundle.proposal is None or bundle.revised_checks.get("passed") is not True:
        raise ValueError(
            "Only a successful pending_review audit can enter the multi-reviewer workflow"
        )
    return bundle, hashlib.sha256(raw).hexdigest()


def _load_or_init(run_dir: Path, *, audit_digest: str, run_id: str) -> ApprovalWorkflow:
    """Load existing workflow if it matches the current snapshot; else start fresh."""
    path = _workflow_path(run_dir)
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        workflow = ApprovalWorkflow.from_dict(record)
        if workflow.audit_digest != audit_digest or workflow.run_id != run_id:
            raise ValueError(
                "The audit content or evidence changed; reopen the workflow with the new snapshot"
            )
        return workflow
    workflow = ApprovalWorkflow(
        run_id=run_id,
        audit_digest=audit_digest,
        state="submitted",
        opened_at_utc=_now_iso(),
    )
    return workflow


def _persist(run_dir: Path, workflow: ApprovalWorkflow) -> Path:
    path = _workflow_path(run_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_approval_workflow(run_dir: Path) -> ApprovalWorkflow | None:
    """Return the recorded workflow for ``run_dir`` or ``None`` if absent."""
    path = _workflow_path(run_dir)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    return ApprovalWorkflow.from_dict(record)


def start_review(
    run_dir: Path,
    *,
    reviewer: str,
    expected_audit_digest: str | None = None,
) -> ApprovalWorkflow:
    """Open or reopen a workflow as ``in_review``, with the named reviewer onboard."""
    bundle, snapshot = _bundle(run_dir)
    if expected_audit_digest is not None and expected_audit_digest != snapshot:
        raise ValueError("The audit or its sources changed; reopen with the new snapshot")
    _text(reviewer, "reviewer", _MAX_REVIEWER)
    reviewer = reviewer.strip()
    workflow = _load_or_init(run_dir, audit_digest=snapshot, run_id=bundle.run_id)
    if workflow.state in {"approved"}:
        raise ValueError("Cannot reopen a workflow that has already been approved")
    workflow.state = "in_review"
    if reviewer not in workflow.reviewers:
        workflow.reviewers.append(reviewer)
    _persist(run_dir, workflow)
    return workflow


def comment_on_audit(
    run_dir: Path,
    *,
    author: str,
    text: str,
) -> ApprovalWorkflow:
    """Append a comment to the workflow timeline."""
    bundle, snapshot = _bundle(run_dir)
    _text(author, "comment author", _MAX_REVIEWER)
    _text(text, "comment text", _MAX_COMMENT)
    workflow = _load_or_init(run_dir, audit_digest=snapshot, run_id=bundle.run_id)
    comment = ReviewComment(
        author=author.strip(),
        text=text.strip(),
        created_at_utc=_now_iso(),
        comment_id=f"c-{uuid.uuid4().hex[:12]}",
        phase="post_approval" if workflow.state == "approved" else "in_review",
    )
    workflow.comments.append(comment)
    _persist(run_dir, workflow)
    return workflow


def record_modification(
    run_dir: Path,
    *,
    requested_by: str,
    reason: str,
    before_text: str,
    after_text: str,
) -> ApprovalWorkflow:
    """Record a change request alongside the revised wording the author returned.

    ``before_text`` is the wording the reviewer questioned; ``after_text`` is the
    wording the author returned (and which will become the next audit's
    revised_copy after a fresh audit run). They do not need to byte-match the
    current ``bundle.proposal.revised_copy`` — the audit snapshot itself already
    pins the wording that was approved.
    """
    bundle, snapshot = _bundle(run_dir)
    _text(requested_by, "requested_by", _MAX_REVIEWER)
    _text(reason, "reason", _MAX_REASON)
    _text(before_text, "before_text", _MAX_DIFF)
    _text(after_text, "after_text", _MAX_DIFF)
    if before_text == after_text:
        raise ValueError(
            "before_text and after_text cannot be identical; record a real change"
        )
    workflow = _load_or_init(run_dir, audit_digest=snapshot, run_id=bundle.run_id)
    entry = ModificationEntry(
        requested_by=requested_by.strip(),
        reason=reason.strip(),
        before_text=before_text,
        after_text=after_text,
        at_utc=_now_iso(),
    )
    workflow.modifications.append(entry)
    if workflow.state in {"submitted", "in_review", "changes_requested"}:
        workflow.state = "changes_requested"
    _persist(run_dir, workflow)
    return workflow


def request_changes(
    run_dir: Path,
    *,
    reviewer: str,
    notes: str = "",
    expected_audit_digest: str | None = None,
) -> ApprovalWorkflow:
    """Mark the workflow as ``changes_requested`` without adding a modification entry."""
    bundle, snapshot = _bundle(run_dir)
    if expected_audit_digest is not None and expected_audit_digest != snapshot:
        raise ValueError("The audit or its sources changed; reopen with the new snapshot")
    _text(reviewer, "reviewer", _MAX_REVIEWER)
    if not isinstance(notes, str) or len(notes) > _MAX_NOTES:
        raise ValueError(f"notes must be a string of at most {_MAX_NOTES} characters")
    workflow = _load_or_init(run_dir, audit_digest=snapshot, run_id=bundle.run_id)
    if workflow.state in {"approved"}:
        raise ValueError("Cannot request changes after final approval")
    workflow.state = "changes_requested"
    if reviewer.strip() not in workflow.reviewers:
        workflow.reviewers.append(reviewer.strip())
    _persist(run_dir, workflow)
    return workflow


def reopen_for_review(run_dir: Path) -> ApprovalWorkflow:
    """Move a ``changes_requested`` workflow back to ``in_review``."""
    bundle, snapshot = _bundle(run_dir)
    workflow = _load_or_init(run_dir, audit_digest=snapshot, run_id=bundle.run_id)
    if workflow.state not in {"changes_requested"}:
        raise ValueError("Only a changes_requested workflow can be reopened")
    workflow.state = "in_review"
    _persist(run_dir, workflow)
    return workflow


def submit_approval_decision(
    run_dir: Path,
    *,
    reviewer: str,
    notes: str = "",
    expected_version: str | None = None,
    expected_audit_digest: str | None = None,
    force: bool = False,
) -> ApprovalWorkflow:
    """Close the workflow as ``approved`` and persist the legacy approval record.

    When ``force`` is False (the default), the workflow state must currently be
    ``in_review``. ``approved`` is final and irreversible without invalidating
    the audit snapshot.
    """
    bundle, snapshot = _bundle(run_dir)
    if expected_audit_digest is not None and expected_audit_digest != snapshot:
        raise ValueError("The audit or its sources changed; reopen with the new snapshot")
    _text(reviewer, "reviewer", _MAX_REVIEWER)
    if not isinstance(notes, str) or len(notes) > _MAX_NOTES:
        raise ValueError(f"notes must be a string of at most {_MAX_NOTES} characters")
    workflow = _load_or_init(run_dir, audit_digest=snapshot, run_id=bundle.run_id)
    if workflow.state == "approved":
        raise ValueError("Workflow is already approved; idempotency is enforced by snapshot")
    if not force and workflow.state not in _DECIDABLE_STATES:
        raise ValueError(
            f"Workflow state {workflow.state!r} cannot be decided; "
            "call start_review or reopen_for_review first"
        )
    approve_audit(
        run_dir,
        reviewer=reviewer,
        notes=notes,
        expected_version=expected_version,
        expected_audit_digest=expected_audit_digest,
    )
    workflow.state = "approved"
    workflow.final_reviewer = reviewer.strip()
    workflow.final_notes = notes
    workflow.closed_at_utc = _now_iso()
    if reviewer.strip() not in workflow.reviewers:
        workflow.reviewers.append(reviewer.strip())
    _persist(run_dir, workflow)
    return workflow


__all__ = [
    "ApprovalState",
    "ApprovalWorkflow",
    "ModificationEntry",
    "ReviewComment",
    "comment_on_audit",
    "load_approval_workflow",
    "record_modification",
    "request_changes",
    "start_review",
    "reopen_for_review",
    "submit_approval_decision",
]
