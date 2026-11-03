"""The voice proxy: key stays server-side, failures degrade rather than break."""

import httpx
import pytest
from starlette.testclient import TestClient

from scribe.live import app
from scribe.voice import VOICE


@pytest.fixture(autouse=True)
def _reset():
    VOICE.forget_voices()
    VOICE._cache.clear()
    yield
    VOICE.forget_voices()
    VOICE._cache.clear()


def test_no_key_means_no_elevenlabs(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    client = TestClient(app)
    body = client.get("/voices").json()
    assert body == {"elevenlabs": False, "voices": []}
    assert client.get("/tts?text=hello").status_code == 503


def test_tts_requires_text(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-key")
    assert TestClient(app).get("/tts?text=").status_code == 400


def _fake_transport(handler):
    real_init = httpx.AsyncClient.__init__

    def init(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, *a, **kw)

    return init


def test_voices_are_listed_and_trimmed(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-key")

    def handler(request):
        assert request.headers["xi-api-key"] == "fake-key"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "abc",
                        "name": "Sarah",
                        "labels": {"accent": "british"},
                        "preview_url": "https://example.com/x.mp3",
                    },
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _fake_transport(handler))
    body = TestClient(app).get("/voices").json()
    assert body["elevenlabs"] is True
    assert body["voices"] == [{"id": "abc", "name": "Sarah", "labels": {"accent": "british"}}]


def test_tts_returns_audio_and_never_echoes_the_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-key")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["text"] = request.read().decode()
        return httpx.Response(200, content=b"ID3-fake-mp3", headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _fake_transport(handler))
    resp = TestClient(app).get("/tts?text=Hello+there&voice=xyz")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == b"ID3-fake-mp3"
    assert "xyz" in seen["url"]
    assert "Hello there" in seen["text"]
    assert "fake-key" not in resp.text  # credential must not leak out


def test_repeated_lines_are_served_from_cache(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-key")
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=b"audio", headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _fake_transport(handler))
    client = TestClient(app)
    client.get("/tts?text=same+line")
    client.get("/tts?text=same+line")
    assert len(calls) == 1


def test_upstream_failure_is_a_502_not_a_crash(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "bad-key")

    def handler(request):
        return httpx.Response(401, json={"detail": "invalid api key"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _fake_transport(handler))
    assert TestClient(app).get("/tts?text=hello").status_code == 502


def test_upstream_message_is_surfaced_not_just_the_status(monkeypatch):
    """A bare '400 Bad Request' hid the one thing that named the fix."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "an-api-key-id-not-a-key")

    def handler(request):
        return httpx.Response(
            400,
            json={
                "detail": {
                    "type": "authentication_error",
                    "message": "API key ID used as API key - API keys start with 'sk_'.",
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _fake_transport(handler))
    body = TestClient(app).get("/voices").json()
    assert body["elevenlabs"] is False
    assert "start with 'sk_'" in body["error"]
