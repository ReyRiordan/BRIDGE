"""
Configuration for the voice pipeline kit.

Uses pydantic-settings to load and validate environment variables (from the
environment or a local .env file next to this package). Env-var names match the
CDK block in infra/voice-runtime.ts, so the infra module injects config that
this module reads with zero mapping.

Secrets in deployed environments arrive via SSM Parameter Store (see
``_export_ssm_secrets``), never as plain-text runtime env values.
"""

import os
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Canonical reasoning-effort levels — the union across current LLM models. Not every
# model supports every value; matching value↔model↔provider is the operator's
# responsibility (consistent with the no-validation philosophy for temperature/max_tokens).
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


class VoiceKitSettings(BaseSettings):
    """
    Voice-pipeline settings loaded from environment variables.

    All settings can be configured via .env file or environment variables.
    """

    # App Configuration
    env: str = Field(
        default="development",
        description="Environment: development, staging, production",
    )

    # LLM Configuration
    llm_provider: str = Field(
        default="openrouter", description="LLM provider: openrouter or bedrock"
    )
    openrouter_api_key: Optional[str] = Field(
        default=None, description="OpenRouter API key"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", description="OpenRouter base URL"
    )
    aws_bedrock_base_url: Optional[str] = Field(
        default=None, description="AWS Bedrock OpenAI-compatible base URL"
    )
    aws_bedrock_sigv4_service: str = Field(
        default="bedrock-mantle",
        description="SigV4 service name used to sign bedrock-mantle requests. "
        "Override only if the signing service string changes.",
    )
    llm_model: str = Field(
        default="anthropic/claude-haiku-4.5",
        description="LLM model to use for real-time conversation turns",
    )
    llm_reasoning: ReasoningEffort = Field(
        default="none", description="Reasoning effort level for the conversation LLM"
    )
    llm_providers: Optional[List[str]] = Field(
        default=None,
        description="Comma-separated list of OpenRouter providers to prioritize "
        "(silently ignored when llm_provider is bedrock)",
    )

    @field_validator("llm_providers", mode="before")
    @classmethod
    def parse_llm_providers(cls, v):
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return None
            return [p.strip() for p in stripped.split(",") if p.strip()]
        return v

    # STT Configuration
    stt_provider: str = Field(
        default="transcribe",
        description="STT provider for real-time audio transcription (transcribe or together)",
    )
    together_api_key: Optional[str] = Field(
        default=None, description="Together AI API key for STT"
    )
    stt_preroll_ms: int = Field(
        default=300,
        description="Milliseconds of pre-speech audio retained before VAD start so the utterance onset (first word) is transcribed.",
    )

    # TTS Configuration
    tts_provider: str = Field(
        default="polly", description="TTS provider: inworld or polly"
    )
    tts_voice: str = Field(
        default="Ruth",
        description="TTS voice ID (a Polly voice like 'Ruth', or an Inworld voice like 'Ashley')",
    )
    tts_model: Optional[str] = Field(
        default=None,
        description="TTS model ID (Inworld only, e.g. 'inworld-tts-1.5-mini'; unused by Polly)",
    )
    inworld_api_key: Optional[str] = Field(default=None, description="Inworld API key")

    # AWS Configuration
    # Static keys are a LOCAL-DEV convenience only. In deployed environments they
    # must stay unset so boto3's default chain resolves the task/execution role —
    # see infra/voice-runtime.ts and docs/05-gotchas.md #1.
    aws_access_key_id: Optional[str] = Field(
        default=None, description="AWS access key ID for Polly and Transcribe (local dev only)"
    )
    aws_secret_access_key: Optional[str] = Field(
        default=None, description="AWS secret access key for Polly and Transcribe (local dev only)"
    )
    aws_region: str = Field(
        default="us-east-1", description="AWS region for Polly, Transcribe, KVS, and Bedrock"
    )

    # Kinesis Video Streams (voice runtime ICE/TURN)
    # The Pipecat voice runtime on AgentCore lazily calls GetIceServerConfig against
    # this signaling channel to obtain KVS-managed TURN credentials (relay-only).
    kvs_channel_name: Optional[str] = Field(
        default=None,
        description="KVS signaling channel name for managed TURN (GetIceServerConfig)",
    )

    # AgentCore Voice Runtime (control-plane signaling proxy)
    # The /signal endpoint proxies WebRTC signaling to this runtime via
    # invoke_agent_runtime (IAM/SigV4). Set on the API host, not the runtime.
    voice_runtime_arn: Optional[str] = Field(
        default=None,
        description="AgentCore Runtime ARN for the voice pipeline (invoke_agent_runtime target)",
    )
    # AgentCore's invoke_agent_runtime requires runtimeSessionId length 33-256
    # (validated CLIENT-SIDE by botocore); uuid4().hex is only 32, so the prefix
    # lifts it to 40 and makes ids self-identifying in AgentCore logs.
    runtime_session_id_prefix: str = Field(
        default="voicekit-",
        description="Prefix for minted AgentCore runtime session ids (prefix + 32 hex chars must be 33-256 long)",
    )

    # Session Configuration
    session_time_limit_minutes: int = Field(
        default=30,
        description="Conversation cap in minutes, applied as the PipelineWorker idle timeout "
        "(the runtime's self-termination backstop). Keep any frontend timer aligned.",
    )

    # Default system prompt for the built-in static context provider. Host apps
    # that register their own context provider (voice_kit.context) can ignore both.
    system_prompt: Optional[str] = Field(
        default=None,
        description="System prompt used by the default context provider",
    )
    system_prompt_path: Optional[str] = Field(
        default=None,
        description="Path to a file whose contents become the default system prompt "
        "(used when system_prompt is unset)",
    )

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def _export_ssm_secrets() -> None:
    """
    Populate secret env vars from SSM Parameter Store at cold start.

    Deployed runtimes (see infra/voice-runtime.ts) receive SECRETS_FROM_SSM
    (comma-separated secret names) and SECRETS_SSM_PREFIXES (comma-separated
    parameter path prefixes, most specific first) instead of plain-text values,
    so keys never appear in Lambda/AgentCore configuration. Each name is
    resolved with ssm:GetParameter (WithDecryption) and exported into os.environ
    BEFORE Settings loads — field resolution is unchanged for the app.

    No-op locally: without SECRETS_FROM_SSM, secrets come from .env as before.
    An env var that is already set always wins over SSM. A name found under no
    prefix is left unset — the Settings fields are Optional, so the owning
    feature degrades exactly as it would with a missing secret today.
    """
    names = [n for n in os.environ.get("SECRETS_FROM_SSM", "").split(",") if n]
    if not names:
        return
    prefixes = [p for p in os.environ.get("SECRETS_SSM_PREFIXES", "").split(",") if p]

    ssm = None
    for name in names:
        if os.environ.get(name):
            continue
        if ssm is None:
            import boto3  # deferred: not a dependency of local .env-based runs

            ssm = boto3.client("ssm")
        for prefix in prefixes:
            try:
                param = ssm.get_parameter(Name=f"{prefix}/{name}", WithDecryption=True)
            except ssm.exceptions.ParameterNotFound:
                continue
            os.environ[name] = param["Parameter"]["Value"]
            break


_export_ssm_secrets()

# Global settings instance
settings = VoiceKitSettings()


def configure(**overrides) -> None:
    """
    Programmatically override settings in place (alternative to env vars).

    For hosts that prefer code-driven config, e.g.::

        from voice_kit import configure
        configure(tts_provider="inworld", tts_voice="Ashley", tts_model="inworld-tts-1.5-mini")

    Call once at startup, before any pipeline/router is built. Unknown names
    raise AttributeError-equivalent ValueError so typos fail loudly.
    """
    for name, value in overrides.items():
        if name not in VoiceKitSettings.model_fields:
            raise ValueError(f"Unknown voice_kit setting: {name!r}")
        setattr(settings, name, value)
