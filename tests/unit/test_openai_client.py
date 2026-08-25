"""
Unit tests for app.adapters.openai_client.OpenAIClient.transcribe_audio().

All tests inject a mocked openai.OpenAI client — no real API calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import openai
import pytest

from app.adapters.openai_client import OpenAIClient, OpenAIError
from app.config.settings import ConfigError, Settings


def _settings(**overrides) -> Settings:
    defaults = dict(
        openai_api_key="test-openai-key",
        openai_retry_max=1,
        openai_timeout_seconds=5,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _mock_transcription_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


def test_transcribe_audio_returns_text():
    s = _settings()
    mock_oai = MagicMock()
    mock_oai.audio.transcriptions.create.return_value = _mock_transcription_response("Hello, this is a test.")
    client = OpenAIClient(settings=s, _client=mock_oai)

    result = client.transcribe_audio(b"fake-audio-bytes")

    assert result == "Hello, this is a test."


def test_transcribe_audio_uses_configured_default_model():
    s = _settings(openai_model_call_transcription="whisper-1")
    mock_oai = MagicMock()
    mock_oai.audio.transcriptions.create.return_value = _mock_transcription_response("text")
    client = OpenAIClient(settings=s, _client=mock_oai)

    client.transcribe_audio(b"data")

    call_kwargs = mock_oai.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-1"


def test_transcribe_audio_model_override():
    s = _settings()
    mock_oai = MagicMock()
    mock_oai.audio.transcriptions.create.return_value = _mock_transcription_response("text")
    client = OpenAIClient(settings=s, _client=mock_oai)

    client.transcribe_audio(b"data", model="gpt-4o-transcribe")

    call_kwargs = mock_oai.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-transcribe"


def test_transcribe_audio_sets_filename():
    s = _settings()
    mock_oai = MagicMock()
    mock_oai.audio.transcriptions.create.return_value = _mock_transcription_response("text")
    client = OpenAIClient(settings=s, _client=mock_oai)

    client.transcribe_audio(b"data", filename="call-123.wav")

    call_kwargs = mock_oai.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["file"].name == "call-123.wav"


def test_transcribe_audio_requires_api_key():
    client = OpenAIClient(settings=Settings(_env_file=None), _client=MagicMock())
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        client.transcribe_audio(b"data")


def test_transcribe_audio_raises_on_authentication_error():
    s = _settings()
    mock_oai = MagicMock()
    mock_oai.audio.transcriptions.create.side_effect = openai.AuthenticationError(
        "bad key", response=MagicMock(status_code=401), body=None
    )
    client = OpenAIClient(settings=s, _client=mock_oai)

    with pytest.raises(OpenAIError, match="authentication failed"):
        client.transcribe_audio(b"data")


def test_transcribe_audio_retries_on_timeout_then_succeeds():
    s = _settings(openai_retry_max=1)
    mock_oai = MagicMock()
    mock_oai.audio.transcriptions.create.side_effect = [
        openai.APITimeoutError(request=MagicMock()),
        _mock_transcription_response("recovered"),
    ]
    client = OpenAIClient(settings=s, _client=mock_oai)

    result = client.transcribe_audio(b"data", _retry_delay=0.0)

    assert result == "recovered"
    assert mock_oai.audio.transcriptions.create.call_count == 2


def test_transcribe_audio_raises_after_exhausting_timeout_retries():
    s = _settings(openai_retry_max=1)
    mock_oai = MagicMock()
    mock_oai.audio.transcriptions.create.side_effect = openai.APITimeoutError(request=MagicMock())
    client = OpenAIClient(settings=s, _client=mock_oai)

    with pytest.raises(OpenAIError, match="timeout"):
        client.transcribe_audio(b"data", _retry_delay=0.0)

    assert mock_oai.audio.transcriptions.create.call_count == 2  # initial + 1 retry
