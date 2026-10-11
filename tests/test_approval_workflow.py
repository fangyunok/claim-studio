"""Multi-reviewer approval workflow layered on a successful pending_review audit."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from growth_agent.approval_workflow import (  # noqa: E402
    ApprovalWorkflow,
    ReviewComment,
    load_approval_workflow,
    record_modification,
    reopen_for_review,
    request_changes,
    start_review,
    submit_approval_decision,
    comment_on_audit,
)
from growth_agent.audit import AuditBundle, AuditProposal, AuditRequest  # noqa: E402


def _seed_bundle(root: Path, *, revised_copy: str = "Reviewed copy.") -> AuditBundle:
    request = AuditRequest(
        id="case", product_id="demo", product_name="Demo", locale="en-US",
        feature="feature", channel="social_post", original_copy="Original.", audience="creators",
    )
    bundle = AuditBundle(
        run_id="audit-case", request=request, status="pending_review", model="scripted-test",
        proposal=AuditProposal(revised_copy=revised_copy, issues=[], citations=[{"quote": "Reviewed copy.", "fact_id": "f1"}]),
        facts=[{"id": "f1", "source_quote": "Source passage"}], revised_checks={"passed": True},
    )
    (root / "audit_bundle.json").write_text(bundle.model_dump_json(), encoding="utf-8")
    return bundle


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = _seed_bundle(self.root)

    def _audit_digest(self) -> str:
        import hashlib
        return hashlib.sha256((self.root / "audit_bundle.json").read_bytes()).hexdigest()

    def test_full_lifecycle_submitted_to_approved(self) -> None:
        started = start_review(self.root, reviewer="Alice")
        self.assertEqual(started.state, "in_review")
        self.assertIn("Alice", started.reviewers)
        self.assertTrue(started.opened_at_utc)
        commented = comment_on_audit(self.root, author="Bob", text="Looks aligned with the source")
        self.assertEqual(len(commented.comments), 1)
        self.assertEqual(commented.comments[0].author, "Bob")
        self.assertEqual(commented.comments[0].phase, "in_review")
        modified = record_modification(
            self.root, requested_by="Alice", reason="Too strong a guarantee",
            before_text="Reviewed copy.", after_text="Reviewed copy, lightly revised.",
        )
        self.assertEqual(modified.state, "changes_requested")
        self.assertEqual(len(modified.modifications), 1)
        reopened = reopen_for_review(self.root)
        self.assertEqual(reopened.state, "in_review")
        decision = submit_approval_decision(
            self.root, reviewer="Eve", notes="All sources check out",
            expected_audit_digest=self._audit_digest(),
        )
        self.assertEqual(decision.state, "approved")
        self.assertEqual(decision.final_reviewer, "Eve")
        self.assertTrue(decision.closed_at_utc)
        self.assertIn("Eve", decision.reviewers)
        # legacy approval file is also written
        self.assertTrue((self.root / "audit_approval.json").is_file())

    def test_load_round_trips_workflow(self) -> None:
        start_review(self.root, reviewer="Alice")
        comment_on_audit(self.root, author="Bob", text="ok")
        loaded = load_approval_workflow(self.root)
        assert loaded is not None
        self.assertEqual(loaded.state, "in_review")
        self.assertEqual(len(loaded.comments), 1)

    def test_request_changes_does_not_require_modification_entry(self) -> None:
        start_review(self.root, reviewer="Alice")
        result = request_changes(self.root, reviewer="Alice", notes="need a hedge on latency")
        self.assertEqual(result.state, "changes_requested")
        self.assertEqual(result.modifications, [])

    def test_blank_inputs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            start_review(self.root, reviewer="   ")
        with self.assertRaises(ValueError):
            comment_on_audit(self.root, author="A", text="   ")
        with self.assertRaises(ValueError):
            record_modification(
                self.root, requested_by="Alice", reason="",
                before_text="x", after_text="y",
            )
        with self.assertRaises(ValueError):
            record_modification(
                self.root, requested_by="Alice", reason="diff",
                before_text="x", after_text="x",
            )

    def test_decision_requires_review_state(self) -> None:
        # fresh audit: workflow.state == "submitted"
        with self.assertRaises(ValueError):
            submit_approval_decision(
                self.root, reviewer="Eve",
                expected_audit_digest=self._audit_digest(),
            )

    def test_snapshot_change_invalidates_inflight_workflow(self) -> None:
        start_review(self.root, reviewer="Alice")
        # mutate sources after the workflow opened
        self.bundle.facts[0]["source_quote"] = "Mutated passage"
        (self.root / "audit_bundle.json").write_text(self.bundle.model_dump_json(), encoding="utf-8")
        with self.assertRaises(ValueError):
            submit_approval_decision(
                self.root, reviewer="Eve",
                expected_audit_digest=self._audit_digest(),
            )

    def test_legacy_approval_path_still_works(self) -> None:
        # The pre-existing approval module keeps its single-reviewer behaviour.
        from growth_agent.approval import approve_audit, load_approved_copy
        from growth_agent.media import copy_digest

        path = approve_audit(self.root, reviewer="Solo", expected_version=copy_digest("Reviewed copy."))
        self.assertTrue(path.is_file())
        approved = load_approved_copy(self.root)
        self.assertEqual(approved.reviewer, "Solo")

    def test_post_approval_comment_is_flagged(self) -> None:
        start_review(self.root, reviewer="Alice")
        submit_approval_decision(
            self.root, reviewer="Eve",
            expected_audit_digest=self._audit_digest(),
        )
        noted = comment_on_audit(self.root, author="Frank", text="Locked.")
        self.assertEqual(noted.comments[0].phase, "post_approval")

    def test_reopen_rejects_non_changes_requested_state(self) -> None:
        start_review(self.root, reviewer="Alice")
        with self.assertRaises(ValueError):
            reopen_for_review(self.root)

    def test_workflow_json_shape_is_stable(self) -> None:
        start_review(self.root, reviewer="Alice")
        comment_on_audit(self.root, author="Bob", text="Looks good")
        raw = json.loads((self.root / "audit_workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["state"], "in_review")
        self.assertIn("audit_digest", raw)
        self.assertEqual(len(raw["comments"]), 1)
        self.assertIn("comment_id", raw["comments"][0])
        self.assertEqual(raw["comments"][0]["phase"], "in_review")

    def test_idempotency_rejects_double_approval(self) -> None:
        start_review(self.root, reviewer="Alice")
        submit_approval_decision(
            self.root, reviewer="Eve",
            expected_audit_digest=self._audit_digest(),
        )
        with self.assertRaises(ValueError):
            submit_approval_decision(
                self.root, reviewer="Eve",
                expected_audit_digest=self._audit_digest(),
            )


if __name__ == "__main__":
    unittest.main()
