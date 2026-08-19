"""
Structured-output passthrough: OpenRouter sends response_format plus the
require_parameters routing preference; Bedrock accepts and ignores it.
"""

import asyncio
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_kit.providers import llm as llm_module  # noqa: E402
from voice_kit.providers.llm import BedrockChat, OpenRouterChat  # noqa: E402

SCHEMA = {
    "type": "json_schema",
    "json_schema": {"name": "actions", "schema": {"type": "object"}},
}

REPLY = {"choices": [{"message": {"content": "ok"}}]}


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Captures the single POST the provider makes."""

    last_call = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        FakeSession.last_call = {"url": url, **kwargs}
        return FakeResponse(REPLY)


@pytest.fixture
def captured_post(monkeypatch):
    FakeSession.last_call = None
    monkeypatch.setattr(llm_module.aiohttp, "ClientSession", FakeSession)
    return FakeSession


def test_openrouter_sends_response_format_and_require_parameters(captured_post):
    client = OpenRouterChat(
        api_key="k",
        base_url="https://openrouter.test/api/v1",
        providers=["anthropic"],
        require_parameters=True,
    )

    asyncio.run(client.chat([{"role": "user", "content": "hi"}], "sys", SCHEMA))

    payload = captured_post.last_call["json"]
    assert payload["response_format"] == SCHEMA
    # Merged with the existing order preference, not replacing it.
    assert payload["provider"] == {"order": ["anthropic"], "require_parameters": True}


def test_openrouter_omits_both_knobs_by_default(captured_post):
    client = OpenRouterChat(api_key="k", base_url="https://openrouter.test/api/v1")

    asyncio.run(client.chat([{"role": "user", "content": "hi"}], "sys"))

    payload = captured_post.last_call["json"]
    assert "response_format" not in payload
    assert "provider" not in payload


def test_bedrock_ignores_response_format_with_a_warning(
    captured_post, monkeypatch, caplog
):
    client = BedrockChat.__new__(BedrockChat)
    client.url = "https://bedrock.test/chat/completions"
    client.model = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    client.reasoning_effort = "none"
    client.timeout = None
    monkeypatch.setattr(BedrockChat, "_sign", lambda self, url, body: {})

    with caplog.at_level(logging.WARNING, logger=llm_module.__name__):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}], "sys", SCHEMA))

    body = captured_post.last_call["data"]
    assert b"response_format" not in body
    assert "ignores response_format" in caplog.text
