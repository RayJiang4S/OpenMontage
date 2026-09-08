from tools.audio.elevenlabs_tts import ElevenLabsTTS


class FakeResponse:
    def __init__(self, content=b"fake audio"):
        self.content = content

    def raise_for_status(self):
        return None


def test_elevenlabs_v3_tts_includes_audio_tags_and_voice_settings(monkeypatch, tmp_path):
    calls = []

    def fake_post(url, headers, json, params, timeout):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "params": params,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr("requests.post", fake_post)

    output_path = tmp_path / "eleven-v3.mp3"
    result = ElevenLabsTTS().execute(
        {
            "text": "This scene is ready.",
            "model_id": "eleven_v3",
            "audio_tags": ["whispers", "[excited]"],
            "speed": 1.05,
            "use_speaker_boost": True,
            "output_path": str(output_path),
        }
    )

    assert result.success
    assert output_path.read_bytes() == b"fake audio"
    request = calls[0]
    assert request["url"] == "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
    assert request["headers"]["xi-api-key"] == "test-key"
    assert request["json"]["model_id"] == "eleven_v3"
    assert request["json"]["text"] == "[whispers] [excited] This scene is ready."
    assert request["json"]["voice_settings"]["speed"] == 1.05
    assert request["json"]["voice_settings"]["use_speaker_boost"] is True
    assert result.data["audio_tags"] == ["[whispers]", "[excited]"]


def test_elevenlabs_text_to_dialogue_posts_turns(monkeypatch, tmp_path):
    calls = []

    def fake_post(url, headers, json, params, timeout):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "params": params,
                "timeout": timeout,
            }
        )
        return FakeResponse(content=b"dialogue audio")

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr("requests.post", fake_post)

    output_path = tmp_path / "dialogue.mp3"
    result = ElevenLabsTTS().execute(
        {
            "operation": "text_to_dialogue",
            "dialogue_inputs": [
                {"text": "Ready for the briefing?", "voice_id": "voice-a"},
                {"text": "Yes, keep it concise.", "voice_id": "voice-b"},
            ],
            "seed": 42,
            "output_path": str(output_path),
        }
    )

    assert result.success
    assert output_path.read_bytes() == b"dialogue audio"
    request = calls[0]
    assert request["url"] == "https://api.elevenlabs.io/v1/text-to-dialogue"
    assert request["json"]["model_id"] == "eleven_v3"
    assert request["json"]["seed"] == 42
    assert request["json"]["inputs"] == [
        {"text": "Ready for the briefing?", "voice_id": "voice-a"},
        {"text": "Yes, keep it concise.", "voice_id": "voice-b"},
    ]
    assert result.data["operation"] == "text_to_dialogue"
    assert result.data["dialogue_turns"] == 2
    assert result.data["unique_voice_count"] == 2


def test_elevenlabs_dialogue_validation_rejects_too_many_unique_voices():
    inputs = {
        "dialogue_inputs": [
            {"text": f"Turn {index}", "voice_id": f"voice-{index}"}
            for index in range(11)
        ]
    }

    try:
        ElevenLabsTTS._dialogue_inputs(inputs)
    except ValueError as exc:
        assert "10 unique voice IDs" in str(exc)
    else:
        raise AssertionError("Expected dialogue validation to reject too many voices")
