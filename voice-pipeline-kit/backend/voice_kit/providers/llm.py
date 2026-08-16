"""
LLM providers (chat-completions path).

Two providers behind one ``BaseLLM.chat`` interface:

- ``OpenRouterChat`` — bearer-token auth against OpenRouter's
  ``/chat/completions``; supports provider-routing preferences.
- ``BedrockChat`` — AWS SigV4 request signing against the OpenAI-compatible
  ``bedrock-mantle`` ``/chat/completions`` endpoint; keyless (execution role).
  Provider-routing preferences are silently ignored here.
"""

import json
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import aiohttp
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import get_session

from ..config import ReasoningEffort, settings
from ..errors import UpstreamServiceError

# Cached at module scope so the credential-provider chain (env -> shared config ->
# container/IMDS execution role) is resolved once per warm process, not per request.
# The resolved Credentials object refreshes its own frozen creds when role creds
# rotate. Tests patch this symbol to inject static credentials.
_botocore_session = get_session()


async def _raise_for_upstream_error(
    response: aiohttp.ClientResponse, provider: str
) -> None:
    """
    Raise ``UpstreamServiceError`` if the LLM provider returned a non-2xx status.

    Unlike ``response.raise_for_status()``, this reads the response body so the
    provider's actual error message (e.g. OpenRouter's "Reasoning is mandatory...")
    is preserved — both logged for diagnosis and surfaced to the client with CORS
    headers (see ``UpstreamServiceError``).
    """
    if response.status < 400:
        return

    body = await response.text()
    message = f"{provider} request failed with status {response.status}"
    print(f"{message}: {body}")
    raise UpstreamServiceError(
        message=f"The AI service returned an error (HTTP {response.status}).",
        details={"provider": provider, "status": response.status, "body": body[:2000]},
        upstream_status=response.status,
    )


def _extract_chat_content(data: Dict, provider: str) -> str:
    """
    Pull ``choices[0].message.content`` out of a chat-completions payload.

    A 200 with a non-standard body (missing/empty ``choices``, no ``message``,
    non-string ``content``) would otherwise raise a bare ``KeyError``/``IndexError``
    that escapes as a CORS-less 500. Raised as a retriable (502-equivalent)
    ``UpstreamServiceError`` instead: a malformed 200 is transient from the
    caller's perspective.
    """
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise UpstreamServiceError(
            message="The AI service returned a malformed response.",
            details={
                "provider": provider,
                "error": f"malformed provider response: {e!r}",
            },
            upstream_status=502,
        )
    if not isinstance(content, str):
        raise UpstreamServiceError(
            message="The AI service returned a malformed response.",
            details={
                "provider": provider,
                "error": f"malformed provider response: content is {type(content).__name__}",
            },
            upstream_status=502,
        )
    return content


class BaseLLM(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat(self, messages: List[Dict], system_prompt: str) -> str:
        """
        Generate a response from the LLM.

        Args:
            messages: Conversation history as list of {"role": str, "content": str}
            system_prompt: System instructions

        Returns:
            Generated response text
        """
        ...


class OpenRouterChat(BaseLLM):
    """
    OpenRouter LLM integration.

    Uses OpenRouter's API to generate responses based on conversation
    history and system prompts.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "anthropic/claude-haiku-4.5",
        reasoning_effort: ReasoningEffort = "none",
        timeout_seconds: int = 120,
        providers: Optional[List[str]] = None,
    ):
        self.api_key = api_key
        self.url = f"{base_url}/chat/completions"
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.providers = providers

    async def chat(self, messages: List[Dict], system_prompt: str) -> str:
        """
        Generate response using OpenRouter LLM.

        Args:
            messages: Conversation history as list of {"role": str, "content": str}
            system_prompt: System instructions

        Returns:
            Generated response text

        Raises:
            aiohttp.ClientError: If API request fails
        """
        payload = {
            "model": self.model,
            "reasoning": {"effort": self.reasoning_effort},
            "messages": [],
        }

        if self.providers:
            payload["provider"] = {"order": self.providers}

        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})

        payload["messages"].extend(messages)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.url, json=payload, headers=headers, timeout=self.timeout
            ) as response:
                await _raise_for_upstream_error(response, "OpenRouter")
                data = await response.json()
                return _extract_chat_content(data, "OpenRouter")


class _BedrockSigV4Mixin:
    """
    AWS SigV4 request signing for the OpenAI-compatible ``bedrock-mantle``
    endpoints.

    Authenticates with the runtime's IAM execution role (no static keys, no bearer
    token). The signing is endpoint-agnostic — it signs whatever ``(url, body)`` it
    is given.
    """

    def _init_signing(self, region: str, sigv4_service: str) -> None:
        """Store the signing scope and resolve credentials once from the chain."""
        self.region = region
        self.sigv4_service = sigv4_service
        # Resolve once from the credential-provider chain. Returns None when nothing
        # is configured (e.g. offline/tests) WITHOUT raising — signing then raises in
        # chat(), so the get_llm_model factory can be unit-tested AWS-free.
        self._credentials = _botocore_session.get_credentials()

    def _sign(self, url: str, body: bytes) -> Dict[str, str]:
        """Produce SigV4 headers for a POST of exactly ``body`` to ``url``."""
        # Only Content-Type is attached pre-signing, so it is the only non-host
        # header that becomes part of SignedHeaders and must reach aiohttp verbatim.
        # botocore derives the host from the URL; do NOT set it manually (a mismatched
        # host is the classic SigV4 break). bedrock-mantle is not S3, so no
        # X-Amz-Content-SHA256 header is added — the payload hash goes into the
        # canonical request only.
        request = AWSRequest(
            method="POST",
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        # Per-request frozen creds: for role credentials this returns a fresh
        # ReadOnlyCredentials and transparently refreshes when they near expiry.
        frozen = self._credentials.get_frozen_credentials()
        SigV4Auth(frozen, self.sigv4_service, self.region).add_auth(request)
        return dict(request.headers)


class BedrockChat(_BedrockSigV4Mixin, BaseLLM):
    """
    AWS Bedrock LLM integration via the OpenAI-compatible ``bedrock-mantle``
    ``/chat/completions`` endpoint.

    Authenticates with AWS SigV4 request signing using the runtime's IAM execution
    role (no static keys, no bearer token). Always sends a ``reasoning_effort`` level.
    Ignores OpenRouter-specific fields (e.g. providers). Supports the broad set of
    chat-completions models (e.g. ``openai.gpt-oss-120b``); note the GPT-5 family is
    served only on the Responses API surface, which this kit does not carry — the
    voice path always uses chat-completions.
    """

    def __init__(
        self,
        base_url: str,
        region: str,
        model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        reasoning_effort: ReasoningEffort = "none",
        timeout_seconds: int = 120,
        sigv4_service: str = "bedrock-mantle",
    ):
        self.url = f"{base_url}/chat/completions"
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._init_signing(region, sigv4_service)

    async def chat(self, messages: List[Dict], system_prompt: str) -> str:
        """
        Generate response using AWS Bedrock LLM.

        Args:
            messages: Conversation history as list of {"role": str, "content": str}
            system_prompt: System instructions

        Returns:
            Generated response text

        Raises:
            aiohttp.ClientError: If API request fails
        """
        payload: Dict = {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "messages": [],
        }

        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})

        payload["messages"].extend(messages)

        # Serialize ONCE. The exact bytes we sign must be the exact bytes we send —
        # re-serializing (json=payload) would change spacing/ordering and invalidate
        # the signature (403 InvalidSignature). So data=body, not json=.
        body = json.dumps(payload).encode("utf-8")
        headers = self._sign(self.url, body)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.url, data=body, headers=headers, timeout=self.timeout
            ) as response:
                await _raise_for_upstream_error(response, "Bedrock")
                data = await response.json()
                return _extract_chat_content(data, "Bedrock")


def get_llm_model(
    provider: str = "openrouter",
    model: str = "anthropic/claude-haiku-4.5",
    reasoning_effort: ReasoningEffort = "none",
    timeout_seconds: int = 120,
    providers: Optional[List[str]] = None,
) -> BaseLLM:
    """
    Factory that returns the appropriate LLM provider instance.

    Args:
        provider: Provider name — "openrouter" or "bedrock"
        model: Model identifier (format depends on provider)
        reasoning_effort: Reasoning effort level passed through to the provider
        timeout_seconds: Request timeout in seconds
        providers: OpenRouter-specific provider routing list (ignored by BedrockChat)

    Returns:
        BaseLLM: Configured provider instance

    Raises:
        ValueError: If provider name is not recognized
    """
    if provider == "openrouter":
        return OpenRouterChat(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            providers=providers,
        )
    elif provider == "bedrock":
        return BedrockChat(
            base_url=settings.aws_bedrock_base_url,
            region=settings.aws_region,
            sigv4_service=settings.aws_bedrock_sigv4_service,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
        )
    else:
        raise ValueError(
            f"Unknown LLM provider: {provider!r}. Must be 'openrouter' or 'bedrock'."
        )
