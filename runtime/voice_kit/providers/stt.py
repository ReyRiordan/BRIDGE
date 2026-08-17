"""
Speech-to-text providers (streaming path).

Live, low-latency, in-memory PCM transcription for the voice pipeline. Two
providers behind one interface:

- ``AmazonTranscribeSTT`` (default) — keyless in deployed environments (the SDK
  default credential chain resolves the task role) and keeps audio inside AWS.
- ``TogetherSTT`` — Parakeet over HTTPS. Sends audio to a third party; treat it
  as a dev-only option if your data-residency posture requires all-AWS.
"""

import os
import tempfile
from abc import ABC, abstractmethod
from typing import Tuple

import aiohttp
import numpy as np
from numpy.typing import NDArray

# NOTE: `soundfile` is imported lazily inside TogetherSTT.transcribe_stream (not at
# module scope) because it dlopen()s the native libsndfile.so at import time —
# importing this module must not require libsndfile unless Together is used.
# This mirrors the lazy `amazon_transcribe` import below.


class BaseSTT(ABC):
    """Abstract base class for streaming speech-to-text services."""

    @abstractmethod
    async def transcribe_stream(
        self, audio: Tuple[int, NDArray[np.int16 | np.float32]]
    ) -> str:
        """
        Transcribe in-memory PCM to text (live, low-latency path).

        Args:
            audio: Tuple of (sample_rate, audio_array)

        Returns:
            Transcribed text

        Raises:
            Exception: If transcription fails
        """
        pass


class TogetherSTT(BaseSTT):
    """
    Parakeet STT via Together AI API.

    Transcribes audio using nvidia/parakeet-tdt-0.6b-v3 model. Language is
    hardcoded to English.
    """

    _URL = "https://api.together.ai/v1/audio/transcriptions"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def transcribe_stream(
        self, audio: Tuple[int, NDArray[np.int16 | np.float32]]
    ) -> str:
        """
        Transcribe in-memory PCM using Parakeet via Together AI (HTTP POST).

        Args:
            audio: Tuple of (sample_rate, audio_array)

        Returns:
            Transcribed text

        Raises:
            Exception: If transcription request fails
        """
        sr, arr = audio

        # Expecting mono. If shape is (1, N), squeeze to (N,)
        if arr.ndim > 1:
            arr = np.squeeze(arr, axis=0)

        # Ensure int16 PCM for WAV
        if arr.dtype != np.int16:
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767.0).astype(np.int16)

        # Imported here (not at module scope) so importing this package without
        # libsndfile present does not crash.
        import soundfile as sf

        # Mint the temp path first so the finally below covers the WAV write too —
        # a failing sf.write must not leak the file in /tmp.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name

        try:
            sf.write(temp_path, arr, sr, subtype="PCM_16")

            async with aiohttp.ClientSession() as session:
                with open(temp_path, "rb") as audio_file:
                    form_data = aiohttp.FormData()
                    form_data.add_field("file", audio_file, filename="audio.wav")
                    form_data.add_field("model", "nvidia/parakeet-tdt-0.6b-v3")
                    form_data.add_field("language", "en")

                    headers = {"Authorization": f"Bearer {self.api_key}"}

                    async with session.post(
                        self._URL, headers=headers, data=form_data
                    ) as response:
                        if response.status == 200:
                            output = await response.json()
                            return output.get("text", "")
                        else:
                            error_text = await response.text()
                            raise Exception(
                                f"Transcription failed: {response.status} - {error_text}"
                            )
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class _StaticCredentialResolver:
    """Credential resolver that returns a fixed access key and secret."""

    def __init__(self, access_key_id: str, secret_access_key: str):
        from amazon_transcribe.auth import Credentials

        self._credentials = Credentials(access_key_id, secret_access_key)

    async def get_credentials(self):
        return self._credentials


class AmazonTranscribeSTT(BaseSTT):
    """
    STT via Amazon Transcribe Streaming.

    Uses start_stream_transcription with language locked to en-US. Audio is
    fed in one pass ("one-shot") since the VAD gating upstream already buffers
    the full utterance before transcribe_stream() is called. Audio is sent via
    the SDK's AudioStream.send_audio_event() in 8192-byte PCM chunks.
    """

    def __init__(
        self,
        region: str,
        access_key: str | None = None,
        secret_key: str | None = None,
    ):
        from amazon_transcribe.client import TranscribeStreamingClient

        # Static keys are a local-dev convenience only. In deployed (keyless)
        # environments they are unset, and the SDK's default credential chain
        # resolves the task/execution role instead.
        client_kwargs = {"region": region}
        if access_key and secret_key:
            client_kwargs["credential_resolver"] = _StaticCredentialResolver(
                access_key, secret_key
            )
        self.client = TranscribeStreamingClient(**client_kwargs)

    async def transcribe_stream(
        self, audio: Tuple[int, NDArray[np.int16 | np.float32]]
    ) -> str:
        """
        Transcribe in-memory PCM using Amazon Transcribe Streaming.

        Args:
            audio: Tuple of (sample_rate, audio_array)

        Returns:
            Transcribed text

        Raises:
            Exception: If transcription fails
        """
        import asyncio

        sr, arr = audio

        if arr.ndim > 1:
            arr = np.squeeze(arr, axis=0)

        if arr.dtype != np.int16:
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767.0).astype(np.int16)

        pcm_bytes = arr.tobytes()

        try:
            stream = await self.client.start_stream_transcription(
                language_code="en-US",
                media_sample_rate_hz=sr,
                media_encoding="pcm",
            )

            async def send_audio():
                chunk_size = 8192
                for i in range(0, len(pcm_bytes), chunk_size):
                    await stream.input_stream.send_audio_event(
                        audio_chunk=pcm_bytes[i : i + chunk_size]
                    )
                await stream.input_stream.end_stream()

            parts = []

            async def collect_results():
                async for event in stream.output_stream:
                    for result in event.transcript.results:
                        if not result.is_partial:
                            parts.append(result.alternatives[0].transcript)

            await asyncio.gather(send_audio(), collect_results())
            return " ".join(parts).strip()
        except Exception as e:
            raise Exception(f"Transcription failed: {e}") from e


def get_stt_model(provider: str) -> BaseSTT:
    """
    Factory function to get STT model.

    Args:
        provider: STT provider name ("together" or "transcribe")

    Returns:
        Configured STT model instance

    Raises:
        ValueError: If provider is not supported
    """
    from ..config import settings

    if provider == "together":
        return TogetherSTT(settings.together_api_key)
    elif provider == "transcribe":
        return AmazonTranscribeSTT(
            region=settings.aws_region,
            access_key=settings.aws_access_key_id,
            secret_key=settings.aws_secret_access_key,
        )
    else:
        raise ValueError(f"Unknown STT provider: {provider}")
