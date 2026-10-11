"""Render and dispatch logic for the multi-reviewer section of the audit page."""

from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from growth_agent.approval_workflow import (  # noqa: E402
    ApprovalWorkflow,
    load_approval_workflow,
)
from growth_agent.audit import AuditBundle, AuditProposal, AuditRequest  # noqa: E402
from growth_agent.media import copy_digest  # noqa: E402
from growth_agent.webapp import _render_workflow_section  # noqa: E402


def _seed(directory: Path, *, revised_copy: str = "Reviewed copy.") -> AuditBundle:
    request = AuditRequest(
        id="case", product_id="demo", product_name="Demo", locale="en-US",
        feature="feature", channel="social_post", original_copy="Original.", audience="creators",
    )
    bundle = AuditBundle(
        run_id="audit-ui", request=request, status="pending_review", model="scripted-test",
        proposal=AuditProposal(
            revised_copy=revised_copy,
            issues=[],
            citations=[{"quote": "Reviewed copy.", "fact_id": "f1"}],
        ),
        facts=[{"id": "f1", "source_quote": "Source passage"}],
        revised_checks={"passed": True},
    )
    (directory / "audit_bundle.json").write_text(bundle.model_dump_json(), encoding="utf-8")
    return bundle


class WebappWorkflowRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = _seed(self.root)

    def _digest(self) -> str:
        import hashlib
        return hashlib.sha256((self.root / "audit_bundle.json").read_bytes()).hexdigest()

    def test_render_when_no_workflow_offers_start_form(self) -> None:
        html = _render_workflow_section(
            self.root, self.bundle, self.bundle.proposal,
            self._digest(), copy_digest(self.bundle.proposal.revised_copy),
        )
        self.assertIn("多人审批工作台", html)
        self.assertIn("start_review", html)
        self.assertIn("开启多人审核", html)

    def test_render_after_start_review_shows_timeline_and_decide(self) -> None:
        from growth_agent.approval_workflow import (
            comment_on_audit, start_review, submit_approval_decision,
        )
        start_review(self.root, reviewer="Alice", expected_audit_digest=self._digest())
        comment_on_audit(self.root, author="Bob", text="Looks aligned")
        html = _render_workflow_section(
            self.root, self.bundle, self.bundle.proposal,
            self._digest(), copy_digest(self.bundle.proposal.revised_copy),
        )
        self.assertIn("多人审核中", html)
        self.assertIn("Alice", html)
        self.assertIn("Looks aligned", html)
        self.assertIn("/decide", html)

    def test_render_after_approval_shows_final_state(self) -> None:
        from growth_agent.approval_workflow import start_review, submit_approval_decision
        start_review(self.root, reviewer="Alice", expected_audit_digest=self._digest())
        submit_approval_decision(
            self.root, reviewer="Eve",
            expected_audit_digest=self._digest(),
            expected_version=copy_digest(self.bundle.proposal.revised_copy),
        )
        workflow: ApprovalWorkflow | None = load_approval_workflow(self.root)
        self.assertIsNotNone(workflow)
        assert workflow is not None
        self.assertEqual(workflow.state, "approved")
        html = _render_workflow_section(
            self.root, self.bundle, self.bundle.proposal,
            self._digest(), copy_digest(self.bundle.proposal.revised_copy),
        )
        self.assertIn("已经多方确认", html)
        self.assertIn("Eve", html)


if __name__ == "__main__":
    unittest.main()
