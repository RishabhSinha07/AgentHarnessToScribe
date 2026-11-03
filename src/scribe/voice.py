"""Optional ElevenLabs narration.

The key stays on the server. The viewer asks this process for audio and never
sees the credential, which also keeps every request same-origin.

Without a key, nothing here is reachable and the viewer falls back to the
browser's built-in speech synthesis.
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from pathlib import Path

import httpx
from dotenv import load_dotenv

# A .env beside the project is the easy way to hold the key. It is gitignored;
# override=False means a real environment variable always wins.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
load_dotenv(override=False)

API = "https://api.elevenlabs.io/v1"

# Turbo is the right default for a live lesson: lower latency matters more than
# the last few percent of quality when the drawing is waiting on the voice.
DEFAULT_MODEL = "eleven_turbo_v2_5"
DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"  # Sarah -- clear, unhurried, good for teaching

_CACHE_LIMIT = 256


def _raise_for_status(resp: httpx.Response) -> None:
    """Raise with what ElevenLabs actually said, not just the status code.

    Their errors are genuinely useful -- a bare "400 Bad Request" hides
    messages like "API key ID used as API key", which names the exact fix.
    """
    if resp.status_code < 400:
        return
    detail = ""
    try:
        body = resp.json().get("detail", {})
        if isinstance(body, dict):
            detail = body.get("message") or body.get("status") or ""
        elif isinstance(body, str):
            detail = body
    except Exception:  # noqa: BLE001 - fall back to the raw text
        detail = resp.text[:300]
    raise RuntimeError(f"ElevenLabs {resp.status_code}: {detail or resp.text[:200] or 'no detail'}")


class Voice:
    """Thin ElevenLabs client with an in-memory cache of rendered lines."""

    def __init__(self) -> None:
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._voices: list[dict] | None = None

    # --- configuration -------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        return key or None

    @property
    def available(self) -> bool:
        return self.api_key is not None

    @property
    def model(self) -> str:
        return os.environ.get("ELEVENLABS_MODEL", DEFAULT_MODEL)

    @property
    def default_voice(self) -> str:
        return os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE)

    # --- api -----------------------------------------------------------------
    async def list_voices(self) -> list[dict]:
        """Voices on the account, trimmed to what the picker needs."""
        if not self.available:
            return []
        if self._voices is not None:
            return self._voices

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{API}/voices", headers={"xi-api-key": self.api_key})
            _raise_for_status(resp)
            data = resp.json()

        self._voices = [
            {
                "id": v["voice_id"],
                "name": v.get("name", "unnamed"),
                "labels": v.get("labels", {}),
            }
            for v in data.get("voices", [])
        ]
        return self._voices

    async def synthesize(self, text: str, voice_id: str | None = None) -> bytes:
        """Render one line to MP3. Repeats of the same line are served from cache."""
        if not self.available:
            raise RuntimeError("No ELEVENLABS_API_KEY set.")

        voice_id = voice_id or self.default_voice
        key = hashlib.sha256(f"{voice_id}|{self.model}|{text}".encode()).hexdigest()

        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                f"{API}/text-to-speech/{voice_id}",
                headers={"xi-api-key": self.api_key, "accept": "audio/mpeg"},
                json={
                    "text": text,
                    "model_id": self.model,
                    "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
                },
            )
            _raise_for_status(resp)
            audio = resp.content

        self._cache[key] = audio
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return audio

    def forget_voices(self) -> None:
        """Drop the cached voice list, e.g. after the key changes."""
        self._voices = None


VOICE = Voice()
