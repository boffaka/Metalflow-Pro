"""Tests for the LLM proxy in engines/assistant.py.

Verifies the SDK integration (model selection, prompt caching, error swallow)
without making real API calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import engines.assistant as assistant_module


@pytest.fixture(autouse=True)
def _reset_client():
    """Each test gets a fresh client cache."""
    assistant_module._anthropic_client = None
    yield
    assistant_module._anthropic_client = None


def _build_response(text: str = "Réponse OPEX..."):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(
        input_tokens=120,
        output_tokens=80,
        cache_creation_input_tokens=2048,
        cache_read_input_tokens=0,
    )
    return response


def test_call_llm_uses_opus_4_7_and_caches_context():
    """Model is claude-opus-4-7; project context block carries cache_control."""
    client = MagicMock()
    client.messages.create.return_value = _build_response()

    with patch.object(assistant_module, "_get_anthropic_client", return_value=client):
        result = assistant_module._call_llm(
            "Quelle est l'OPEX ?", "Project=Kokoya, TPH=913", api_key="sk-test"
        )

    assert result == "Réponse OPEX..."
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["max_tokens"] >= 1024
    system_blocks = kwargs["system"]
    assert isinstance(system_blocks, list)
    assert len(system_blocks) == 2
    assert system_blocks[0]["type"] == "text"
    assert "métallurgiste" in system_blocks[0]["text"]
    assert system_blocks[1]["type"] == "text"
    assert "Project=Kokoya" in system_blocks[1]["text"]
    assert system_blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_call_llm_returns_none_when_sdk_missing():
    with patch.object(assistant_module, "_get_anthropic_client", return_value=None):
        result = assistant_module._call_llm("Hello", "ctx", api_key="sk-test")
    assert result is None


def test_call_llm_returns_none_on_timeout():
    import anthropic

    client = MagicMock()
    client.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())

    with patch.object(assistant_module, "_get_anthropic_client", return_value=client):
        result = assistant_module._call_llm("Hello", "ctx", api_key="sk-test")
    assert result is None


def test_call_llm_returns_none_on_rate_limit():
    import anthropic

    response = MagicMock()
    response.headers = {"request-id": "req_abc", "retry-after": "10"}
    client = MagicMock()
    client.messages.create.side_effect = anthropic.RateLimitError(
        message="rate limited", response=response, body=None
    )

    with patch.object(assistant_module, "_get_anthropic_client", return_value=client):
        result = assistant_module._call_llm("Hello", "ctx", api_key="sk-test")
    assert result is None


def test_call_llm_returns_none_on_authentication_error():
    import anthropic

    response = MagicMock()
    response.headers = {}
    client = MagicMock()
    client.messages.create.side_effect = anthropic.AuthenticationError(
        message="bad key", response=response, body=None
    )

    with patch.object(assistant_module, "_get_anthropic_client", return_value=client):
        result = assistant_module._call_llm("Hello", "ctx", api_key="sk-test")
    assert result is None


def test_call_llm_returns_none_on_unexpected_error():
    client = MagicMock()
    client.messages.create.side_effect = ValueError("something weird")

    with patch.object(assistant_module, "_get_anthropic_client", return_value=client):
        result = assistant_module._call_llm("Hello", "ctx", api_key="sk-test")
    assert result is None


def test_get_anthropic_client_caches_instance():
    """The client is built once and reused — important for connection pooling."""
    with patch("engines.assistant.anthropic", create=True) if False else patch.dict(
        "sys.modules", {}
    ):
        # Just call twice and check identity via _anthropic_client global.
        c1 = assistant_module._get_anthropic_client("sk-test")
        c2 = assistant_module._get_anthropic_client("sk-test")
    assert c1 is c2
