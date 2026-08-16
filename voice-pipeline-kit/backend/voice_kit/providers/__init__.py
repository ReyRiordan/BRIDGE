"""
Provider wrappers: STT (Transcribe/Together), LLM (OpenRouter/Bedrock),
TTS (Inworld/Polly), each behind a small ABC + factory.

Selection is plain string dispatch from settings — no automatic fallback: an
unknown provider raises ValueError, a missing key raises at construction.
"""

from .llm import BaseLLM, get_llm_model
from .stt import BaseSTT, get_stt_model
from .tts import BaseTTS, get_tts_model

__all__ = [
    "BaseLLM",
    "BaseSTT",
    "BaseTTS",
    "get_llm_model",
    "get_stt_model",
    "get_tts_model",
]
