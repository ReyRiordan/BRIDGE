"""
Text-to-speech providers.

Two providers behind one synchronous streaming interface (the pipeline runs
the sync generator in a worker thread so the event loop never blocks):

- ``PollyTTS`` (default) — generative engine over StartSpeechSynthesisStream,
  24 kHz PCM; keyless in deployed environments.
- ``InworldTTS`` — LINEAR16 at 48 kHz over Inworld's streaming HTTP API;
  requires INWORLD_API_KEY.
"""

import base64
import json
import logging
from abc import ABC, abstractmethod
from typing import Iterator, Tuple

import boto3
import numpy as np
import requests
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


class BaseTTS(ABC):
    """Abstract base class for text-to-speech services."""

    @abstractmethod
    def stream_tts_sync(self, text: str) -> Iterator[Tuple[int, NDArray]]:
        """
        Synchronously stream TTS audio.

        Args:
            text: Text to synthesize

        Yields:
            Tuples of (sample_rate, audio_chunk)
        """
        pass


class InworldTTS(BaseTTS):
    """
    Inworld TTS implementation.

    Streams audio from Inworld API in LINEAR16 format at 48kHz.
    """

    def __init__(self, api_key: str, voice: str, model: str, speed: float = 1.0):
        """
        Initialize Inworld TTS client.

        Args:
            api_key: Inworld API key (Basic auth)
            voice: Voice ID (e.g., "Dennis", "Ashley")
            model: Model ID (e.g., "inworld-tts-1.5-mini")
            speed: Speaking rate (default: 1.0)
        """
        self.api_key = api_key
        self.voice = voice
        self.model = model
        self.speed = speed
        self.url = "https://api.inworld.ai/tts/v1/voice:stream"

    def stream_tts_sync(self, text: str) -> Iterator[Tuple[int, NDArray]]:
        """
        Stream audio from Inworld TTS.

        Args:
            text: Text to synthesize

        Yields:
            Tuples of (sample_rate, audio_chunk as float32 numpy array)

        Raises:
            requests.HTTPError: If API request fails
        """
        payload = {
            "text": text,
            "voiceId": self.voice,
            "modelId": self.model,
            "audio_config": {
                "audio_encoding": "LINEAR16",
                "sample_rate_hertz": 48000,
                "speakingRate": self.speed,
            },
        }

        headers = {
            "Authorization": f"Basic {self.api_key}",
            "Content-Type": "application/json",
        }

        response = requests.post(self.url, json=payload, headers=headers, stream=True)
        response.raise_for_status()

        sample_rate = payload["audio_config"]["sample_rate_hertz"]

        for line in response.iter_lines():
            if not line:
                continue
            try:
                # Decode if bytes
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                chunk = json.loads(line)

                audio_chunk = base64.b64decode(chunk["result"]["audioContent"])

                # Skip WAV header (44 bytes) — each streamed Inworld chunk is a
                # standalone WAV carrying its own header.
                if len(audio_chunk) > 44:
                    pcm = audio_chunk[44:]
                    # Convert raw bytes (16-bit signed) to numpy array
                    waveform = (
                        np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    )
                    yield (sample_rate, waveform)
                else:
                    logger.debug(
                        "Inworld TTS: dropping %d-byte chunk (header-only, no PCM)",
                        len(audio_chunk),
                    )

            except json.JSONDecodeError as e:
                logger.warning(
                    "Inworld TTS: JSON decode error on stream line: %s (line: %.200s)",
                    e,
                    line,
                )
                continue
            except Exception as e:
                logger.error(
                    "Inworld TTS: error processing chunk: %s (line: %.200s)",
                    e,
                    line,
                    exc_info=True,
                )
                continue


class PollyTTS(BaseTTS):
    """Amazon Polly TTS using StartSpeechSynthesisStream (generative engine, PCM 24kHz)."""

    SAMPLE_RATE = 24000

    def __init__(
        self,
        region: str,
        voice: str,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ):
        self.voice = voice
        # Static keys are a local-dev convenience only. In deployed (keyless)
        # environments they are unset, and boto3's default credential chain
        # resolves the task/execution role instead.
        client_kwargs = {"region_name": region}
        if access_key_id and secret_access_key:
            client_kwargs["aws_access_key_id"] = access_key_id
            client_kwargs["aws_secret_access_key"] = secret_access_key
        self.client = boto3.client("polly", **client_kwargs)

    def stream_tts_sync(self, text: str) -> Iterator[Tuple[int, NDArray]]:
        def action_stream():
            yield {"TextEvent": {"Text": text}}
            yield {"CloseStreamEvent": {}}

        # NOTE: this is the generative streaming action — IAM must grant
        # polly:StartSpeechSynthesisStream (NOT polly:SynthesizeSpeech).
        response = self.client.start_speech_synthesis_stream(
            Engine="generative",
            LanguageCode="en-US",
            OutputFormat="pcm",
            SampleRate=str(self.SAMPLE_RATE),
            VoiceId=self.voice,
            ActionStream=action_stream(),
        )

        for event in response["EventStream"]:
            if "AudioEvent" in event:
                raw = event["AudioEvent"]["AudioChunk"]
                waveform = (
                    np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                )
                yield (self.SAMPLE_RATE, waveform)


def get_tts_model(provider: str, voice: str, model: str = None, **kwargs) -> BaseTTS:
    """
    Factory function to get TTS model.

    Args:
        provider: TTS provider ("inworld" or "polly")
        voice: Voice ID
        model: Model ID (Inworld only)
        **kwargs: Additional options (speed, lang, etc.)

    Returns:
        Configured TTS model instance

    Raises:
        ValueError: If provider is not supported
    """
    from ..config import settings

    if provider == "inworld":
        if not settings.inworld_api_key:
            raise ValueError("Inworld API key not configured")
        return InworldTTS(
            api_key=settings.inworld_api_key,
            voice=voice,
            model=model,
            speed=kwargs.get("speed", 1.0),
        )
    elif provider == "polly":
        return PollyTTS(
            region=settings.aws_region,
            voice=voice,
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key,
        )
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")
