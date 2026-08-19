"""
Shared data types for the voice pipeline kit.

These are the generic replacements for the source app's domain models: roles are
the chat-API-native ``user`` / ``assistant`` (so no role mapping is needed at
the LLM boundary), and the TTS voice is a plain config object instead of a
domain entity.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel


class TranscriptMessage(BaseModel):
    """Single message in the conversation transcript."""

    role: Literal["user", "assistant"]
    content: str  # Transcribed (user) or generated (assistant) text
    timestamp: datetime


class IceServerConfig(BaseModel):
    """A single ICE server (KVS managed-TURN) for the browser's RTCPeerConnection."""

    urls: List[str]
    username: Optional[str] = None
    credential: Optional[str] = None


@dataclass
class VoiceConfig:
    """TTS voice selection for one session.

    Args mirror ``get_tts_model``: ``provider`` is ``"inworld"`` or ``"polly"``,
    ``voice`` is the provider's voice ID, ``model`` is Inworld-only, and
    ``speed`` is the Inworld speaking rate (Polly ignores it).
    """

    provider: str
    voice: str
    model: Optional[str] = None
    speed: float = 1.0
