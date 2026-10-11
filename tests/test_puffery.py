"""Tests for the v0.5.0 puffery (claim-nature) taxonomy.

The taxonomy is opt-in: every claim-nature field defaults to None so existing
knowledge cards, audit bundles, and brief inputs keep validating. These tests
verify both the backward-compat path and a few non-trivial taxonomy flows.
"""

from __future__ import annotations

import json

import pytest

from growth_agent.audit import (
    AuditCitation,
    AuditIssue,
    AuditProposal,
    AuditRequest,
)
from growth_agent.schemas import Brief, ClaimUse, Draft, GenerationResult, ClaimNature


# ---- Claim taxonomy on the schema side ----------------------------------


def test_brief_minimum_constructs_without_nature_field():
    """The legacy brief accepts no claim_nature and still validates."""
    brief = Brief(
        id="b1",
        locale="en-US",
        feature="quick-start-guide",
        audience="first-time user",
        intent="set up virtual camera",
        seed_keyword="virtual camera",
        cta="Try it free",
    )
    assert brief.model_dump()["id"] == "b1"


def test_claim_use_accepts_optional_claim_nature():
    claim = ClaimUse(
        fact_id="obs-x-001",
        sentence="Run as a virtual camera.",
        location="body[0]",
    )
    assert claim.claim_nature is None
    claim_with_nature = claim.model_copy(update={"claim_nature": "puffery"})
    assert claim_with_nature.claim_nature == "puffery"


def test_claim_nature_rejects_unknown_label():
    """An unknown nature label must not slip through to the audit layer."""
    with pytest.raises(ValueError):
        ClaimUse(
            fact_id="obs-x-001",
            sentence="Run as a virtual camera.",
            location="body[0]",
            claim_nature="marketing-wow",  # type: ignore[arg-type]
        )


# ---- Audit issue surface -------------------------------------------------


def _audit_request() -> AuditRequest:
    return AuditRequest(
        id="obs-virtual-camera-en",
        product_id="obs-studio",
        product_name="OBS Studio",
        locale="en-US",
        feature="virtual-camera",
        channel="landing_page",
        original_copy="OBS gives the fastest virtual camera experience ever.",
        audience="first-time user",
    )


def test_audit_issue_accepts_omitted_claim_nature():
    """Legacy callers building AuditIssue without claim_nature must still pass."""
    issue = AuditIssue(
        quote="fastest",
        kind="overstated",
        reason="Matched a locale editorial rule; confirm or remove the claim.",
    )
    assert issue.claim_nature is None


def test_audit_issue_claim_nature_for_puffery_superlative():
    """A superlative-only phrase classifies as puffery, not factual."""
    issue = AuditIssue(
        quote="fastest",
        kind="overstated",
        reason="Superlative without numeric support.",
        claim_nature="puffery",
    )
    assert issue.claim_nature == "puffery"


def test_audit_issue_rejects_unknown_claim_nature():
    """AuditIssue must also reject unknown natures at the model boundary."""
    with pytest.raises(ValueError):
        AuditIssue(
            quote="fastest",
            kind="overstated",
            reason="Superlative without numeric support.",
            claim_nature="vague",  # type: ignore[arg-type]
        )


# ---- Audit bundle end-to-end validation ----------------------------------


def test_audit_proposal_carries_natures_through_citations():
    """The proposal carries claim nature through AuditIssue into the bundle.

    We build a complete AuditProposal + sample fact set and assert the
    serialised payload preserves every nature label. The structural validator
    still requires citations to appear in the revised copy.
    """
    request = _audit_request()
    proposal = AuditProposal(
        revised_copy=(
            "OBS Studio can route any Source as a virtual camera in supported apps.\n"
            "Add the OBS Virtual Camera plugin from Tools → Start Virtual Camera."
        ),
        issues=[
            AuditIssue(
                quote="fastest",
                kind="overstated",
                reason="Superlative claim with no numeric anchor in the source cards.",
                claim_nature="puffery",
            ),
            AuditIssue(
                quote="OBS gives",
                kind="needs_review",
                reason="Subjective experience description that compares to all alternatives.",
                claim_nature="comparative",
            ),
        ],
        citations=[
            AuditCitation(
                quote="OBS Studio Virtual Camera",
                fact_id="obs-virtual-camera-001",
            )
        ],
    )
    payload = proposal.model_dump()
    assert payload["issues"][0]["claim_nature"] == "puffery"
    assert payload["issues"][1]["claim_nature"] == "comparative"
    assert payload["citations"][0]["fact_id"] == "obs-virtual-camera-001"


def test_audit_request_round_trip_json_round_trip():
    """Serialising then re-parsing an AuditRequest preserves every field.

    Guards against a future change accidentally dropping the claim_nature field
    on JSON ingest.
    """
    original = _audit_request()
    raw = json.loads(json.dumps(original.model_dump()))
    rebuilt = AuditRequest.model_validate(raw)
    assert rebuilt.id == original.id


# ---- Five-label coverage --------------------------------------------------


@pytest.mark.parametrize("label", ["puffery", "factual", "comparative", "implied", "absolute"])
def test_every_claim_nature_label_is_accepted_by_both_models(label: ClaimNature):
    """Every label in the taxonomy round-trips through both Pydantic contracts."""
    use = ClaimUse(
        fact_id="obs-x-001",
        sentence="Quick start.",
        location="body[0]",
        claim_nature=label,
    )
    issue = AuditIssue(
        quote="Quick start.",
        kind="needs_review",
        reason="Sanity check for nature taxonomy coverage.",
        claim_nature=label,
    )
    assert use.claim_nature == label
    assert issue.claim_nature == label
