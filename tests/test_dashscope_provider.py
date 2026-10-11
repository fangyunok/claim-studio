"""Tests for the v0.5.0 DashScope / DeepSeek / generic-provider dispatch."""

from __future__ import annotations

import json
import os
import sys
import types
from typing import Any
from unittest import mock

import pytest

from growth_agent.generation import (
    CompatibleApiGenerator,
    DASHSCOPE_PRICING_PER_1K_TOKENS,
    DashScopeApiGenerator,
    DeepSeekApiGenerator,
    _estimate_cost,
)


# ---- Provider-factory dispatch -------------------------------------------


def test_factory_defaults_to_generic_provider(monkeypatch):
    monkeypatch.delenv("GROWTH_API_PROVIDER", raising=False)
    monkeypatch.setenv("GROWTH_API_BASE", "https://api.example.com/v1")
    monkeypatch.setenv("GROWTH_MODEL", "third-party-model")
    monkeypatch.setenv("GROWTH_API_KEY", "sk-test")
    generator = CompatibleApiGenerator.from_environment()
    assert isinstance(generator, CompatibleApiGenerator)
    assert not isinstance(generator, DashScopeApiGenerator)
    assert not isinstance(generator, DeepSeekApiGenerator)
    assert generator.base_url == "https://api.example.com/v1"
    assert generator.model == "third-party-model"


def test_factory_returns_dashscope_when_provider_marker_set(monkeypatch):
    monkeypatch.setenv("GROWTH_API_PROVIDER", "dashscope")
    monkeypatch.setenv("GROWTH_DASHSCOPE_API_KEY", "sk-dash")
    monkeypatch.delenv("GROWTH_QWEN_MODEL", raising=False)
    monkeypatch.setenv("GROWTH_MODEL", "qwen-plus")
    generator = CompatibleApiGenerator.from_environment()
    assert isinstance(generator, DashScopeApiGenerator)
    assert generator.base_url == DashScopeApiGenerator.DEFAULT_BASE_URL
    assert generator.api_key == "sk-dash"
    assert generator.model == "qwen-plus"


def test_factory_returns_deepseek_when_provider_marker_set(monkeypatch):
    monkeypatch.setenv("GROWTH_API_PROVIDER", "deepseek")
    monkeypatch.setenv("GROWTH_DEEPSEEK_API_KEY", "sk-deep")
    generator = CompatibleApiGenerator.from_environment()
    assert isinstance(generator, DeepSeekApiGenerator)
    assert generator.base_url == DeepSeekApiGenerator.DEFAULT_BASE_URL
    assert generator.api_key == "sk-deep"
    assert generator.model == "deepseek-chat"


def test_factory_rejects_blank_provider_marker(monkeypatch):
    monkeypatch.setenv("GROWTH_API_PROVIDER", "   ")
    monkeypatch.setenv("GROWTH_API_BASE", "https://api.example.com/v1")
    monkeypatch.setenv("GROWTH_MODEL", "third-party")
    generator = CompatibleApiGenerator.from_environment()
    assert type(generator) is CompatibleApiGenerator


# ---- Cost estimation contract --------------------------------------------


@pytest.mark.parametrize(
    "model,prompt,completion,expected",
    [
        ("qwen-plus", 1000, 500, 0.0004 + 0.5 * 0.0012),
        ("qwen-turbo", 2000, 1000, 2 * 0.0003 + 1 * 0.0006),
        ("qwen-long", 500, 200, 0.5 * 0.0005 + 0.2 * 0.0020),
    ],
)
def test_dashscope_cost_estimator_for_known_models(model, prompt, completion, expected):
    cost = _estimate_cost(model, DASHSCOPE_PRICING_PER_1K_TOKENS, prompt, completion)
    assert cost is not None
    assert abs(cost - round(expected, 6)) < 1e-9


def test_dashscope_cost_estimator_returns_none_for_unknown_model():
    """An unknown model name must produce None, not a fabricated number."""
    cost = _estimate_cost("qwen-not-a-real-model", DASHSCOPE_PRICING_PER_1K_TOKENS, 100, 50)
    assert cost is None


def test_dashscope_cost_estimator_returns_none_when_pricing_table_missing():
    cost = _estimate_cost("qwen-plus", None, 100, 50)
    assert cost is None


def test_dashscope_cost_estimator_rejects_negative_tokens():
    """A negative token count would silently produce negative cost."""
    assert _estimate_cost("qwen-plus", DASHSCOPE_PRICING_PER_1K_TOKENS, -1, 100) is None
    assert _estimate_cost("qwen-plus", DASHSCOPE_PRICING_PER_1K_TOKENS, 100, -1) is None


def test_dashscope_subclass_estimate_cost_wires_through():
    """The subclass hook must dispatch via the published pricing table."""
    generator = DashScopeApiGenerator(
        base_url="https://example.invalid/v1",
        model="qwen-plus",
        api_key="placeholder",
    )
    cost = generator._estimate_cost(1_000, 0)
    assert cost is not None
    # 1k prompt tokens on qwen-plus must produce a positive, finite USD value
    assert cost > 0


def test_deepseek_subclass_estimate_cost_leaves_blank_by_design():
    """DeepSeek has no built-in pricing table in v0.5.0 — cost stays None."""
    generator = DeepSeekApiGenerator(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="placeholder",
    )
    assert generator._estimate_cost(1_000, 0) is None


def test_base_class_estimate_cost_returns_none():
    """The base class must NOT fabricate a cost estimate."""
    generator = CompatibleApiGenerator(
        base_url="https://example.invalid/v1",
        model="third-party",
        api_key="placeholder",
    )
    assert generator._estimate_cost(1_000, 0) is None


# ---- End-to-end generation under a stub HTTP layer ----------------------


class _StubReply:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self._sent_body: bytes = b""

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _valid_draft_payload(model: str) -> dict[str, Any]:
    return {
        "id": f"stub-{model}",
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "title": f"Title ({model})",
                            "meta_description": "desc",
                            "h1": "h1",
                            "intro": "intro",
                            "body": ["body1"],
                            "social_posts": ["sp1", "sp2"],
                            "claim_uses": [
                                {
                                    "fact_id": "stub-1",
                                    "sentence": "body1",
                                    "location": "body[0]",
                                    "claim_nature": "puffery",
                                }
                            ],
                        }
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
    }


def _valid_fact() -> dict[str, Any]:
    return {
        "id": "stub-1",
        "product_id": "obs-studio",
        "feature": "x",
        "source_url": "https://example.com/source",
        "source_quote": "q",
        "source_title": "t",
        "checked_at": "2026-10-04",
        "availability_note": "",
        "localized_statement": {"en-US": "body1", "es-ES": "body1"},
    }


def _stub_brief() -> Any:
    from growth_agent.schemas import Brief

    return Brief(
        id="b1",
        locale="en-US",
        feature="x",
        audience="any",
        intent="audit test",
        seed_keyword="x",
        cta="cta",
    )


def _stub_rules() -> dict[str, Any]:
    return {"forbidden_phrases": [], "cta": "cta", "tone": ""}


def test_dashscope_generation_attaches_cost_and_claim_nature(monkeypatch):
    """A full generate() call against DashScope must tag cost + claim_nature."""
    from growth_agent.schemas import Draft  # noqa: F401  (sanity import)

    generator = DashScopeApiGenerator(
        base_url="https://example.invalid/v1",
        model="qwen-plus",
        api_key="placeholder",
    )
    payload = _valid_draft_payload("qwen-plus")
    with mock.patch.object(generator, "_request", return_value=payload):
        result = generator.generate(_stub_brief(), [_valid_fact()], _stub_rules())
    assert result.model == "qwen-plus"
    assert result.input_tokens == 1000
    assert result.output_tokens == 500
    assert result.estimated_cost_usd is not None
    assert result.estimated_cost_usd > 0
    # The puffery taxonomy flows through into the draft's claim_uses entry.
    assert result.draft.claim_uses[0].claim_nature == "puffery"


def test_generic_provider_leaves_cost_unset(monkeypatch):
    """The generic ``api`` provider must keep ``estimated_cost_usd`` None."""
    generator = CompatibleApiGenerator(
        base_url="https://example.invalid/v1",
        model="third-party",
        api_key="placeholder",
    )
    payload = _valid_draft_payload("third-party")
    with mock.patch.object(generator, "_request", return_value=payload):
        result = generator.generate(_stub_brief(), [_valid_fact()], _stub_rules())
    assert result.estimated_cost_usd is None


# ---- Pricing table is part of the public API ----------------------------


def test_pricing_table_does_not_silently_change_signatures():
    """V0.5.0 frozen pricing keys must remain callable."""
    expected_keys = {"qwen-plus", "qwen-turbo", "qwen-max", "qwen-long"}
    assert set(DASHSCOPE_PRICING_PER_1K_TOKENS) == expected_keys
    for model, rates in DASHSCOPE_PRICING_PER_1K_TOKENS.items():
        assert set(rates) == {"prompt", "completion"}
        assert rates["prompt"] >= 0
        assert rates["completion"] >= 0


# ---- Import surface sanity check ------------------------------------------


def test_module_public_api_does_not_regress():
    """Providers must remain reachable from ``growth_agent.generation``."""
    module = sys.modules["growth_agent.generation"]
    for name in (
        "CompatibleApiGenerator",
        "QwenOllamaGenerator",
        "DashScopeApiGenerator",
        "DeepSeekApiGenerator",
        "DASHSCOPE_PRICING_PER_1K_TOKENS",
    ):
        assert hasattr(module, name), f"missing exported name: {name}"
