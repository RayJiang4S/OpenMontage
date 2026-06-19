"""Google Gemini API text-to-speech provider tool."""

from __future__ import annotations

import base64
import os
import time
import wave
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class GoogleTTS(BaseTool):
    name = "google_tts"
    version = "0.5.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "google_tts"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set GOOGLE_API_KEY or GEMINI_API_KEY for Gemini API text-to-speech.\n"
        "Get a key at https://aistudio.google.com/apikey"
    )
    fallback = "openai_tts"
    fallback_tools = ["openai_tts", "elevenlabs_tts", "piper_tts"]
    agent_skills = ["text-to-speech"]

    capabilities = [
        "text_to_speech",
        "voice_selection",
        "style_prompting",
        "expressive_audio_tags",
        "multi_speaker",
        "gemini_api_tts",
        "delivery_presets",
        "duration_guidance",
    ]
    supports = {
        "voice_cloning": False,
        "multilingual": True,
        "offline": False,
        "native_audio": True,
        "ssml": False,
        "style_prompting": True,
        "expressive_audio_tags": True,
        "gemini_api_key_auth": True,
        "multi_speaker": True,
        "delivery_presets": True,
        "duration_guidance": True,
    }
    best_for = [
        "latest Gemini API TTS narration with API-key setup",
        "prompt-directed tone, pace, accent, and emotion",
        "Gemini 3.1 expressive audio tags such as [whispering] or [excited]",
        "single-speaker or two-speaker dialogue audio",
    ]
    not_good_for = [
        "voice cloning",
        "fully offline production",
    ]

    DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
    DEFAULT_VOICE = "Kore"
    MODEL_PROFILES = {
        "gemini-3.1-flash-tts-preview": (
            "Recommended default: latest low-latency Gemini TTS with natural output, "
            "steerable prompts, expressive audio tags, and multi-speaker support."
        ),
        "gemini-2.5-pro-preview-tts": (
            "Quality fallback for structured long-form workflows such as podcasts or audiobooks."
        ),
        "gemini-2.5-flash-preview-tts": (
            "Older cost-efficient fallback for low-latency Gemini TTS compatibility checks."
        ),
    }
    VOICE_OPTIONS = {
        "Achernar": "Soft",
        "Achird": "Friendly",
        "Algenib": "Gravelly",
        "Algieba": "Smooth",
        "Alnilam": "Firm",
        "Aoede": "Breezy",
        "Autonoe": "Bright",
        "Callirrhoe": "Easy-going",
        "Charon": "Informative",
        "Despina": "Smooth",
        "Enceladus": "Breathy",
        "Erinome": "Clear",
        "Fenrir": "Excitable",
        "Gacrux": "Mature",
        "Iapetus": "Clear",
        "Kore": "Firm",
        "Laomedeia": "Upbeat",
        "Leda": "Youthful",
        "Orus": "Firm",
        "Puck": "Upbeat",
        "Pulcherrima": "Forward",
        "Rasalgethi": "Informative",
        "Sadachbia": "Lively",
        "Sadaltager": "Knowledgeable",
        "Schedar": "Even",
        "Sulafat": "Warm",
        "Umbriel": "Easy-going",
        "Vindemiatrix": "Gentle",
        "Zephyr": "Bright",
        "Zubenelgenubi": "Casual",
    }
    DELIVERY_PRESETS = {
        "technical_briefing": (
            "Deliver as a focused technical product briefing: confident, crisp, "
            "direct, and calm. Keep Chinese technical terms clear, separate clauses "
            "cleanly, and avoid theatrical emotion."
        ),
        "compact_explainer": (
            "Deliver as a compact explainer: efficient pacing, short pauses, clear "
            "articulation, and no dragged endings. Preserve intelligibility over speed."
        ),
        "warm_opening": (
            "Deliver as a warm but professional opening: welcoming, steady, and "
            "credible, with a natural first sentence rather than a hard sales tone."
        ),
        "clear_cta": (
            "Deliver as a clear call to action: slightly faster, decisive, and easy "
            "to follow, emphasizing the action words without sounding urgent."
        ),
    }
    _MODELS = set(MODEL_PROFILES)

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to speak. For multi-speaker output, include speaker labels in the text.",
            },
            "prompt": {
                "type": "string",
                "description": "Natural-language direction for tone, pacing, accent, emotion, and delivery.",
            },
            "audio_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional expressive inline tags for Gemini 3.1 TTS, e.g. [serious], [whispering], [excited]. Bare names are wrapped in brackets.",
            },
            "delivery_preset": {
                "type": "string",
                "enum": sorted(DELIVERY_PRESETS),
                "description": "Reusable Gemini prompt preset for common narration styles.",
            },
            "duration_target_seconds": {
                "type": "number",
                "minimum": 0,
                "description": "Approximate target duration. Gemini has no hard speed control, so this is encoded as prompt guidance.",
            },
            "model": {
                "type": "string",
                "default": DEFAULT_MODEL,
                "enum": sorted(_MODELS),
                "description": "Gemini TTS model. Default is the latest recommended 3.1 Flash TTS preview.",
            },
            "voice": {
                "type": "string",
                "default": DEFAULT_VOICE,
                "enum": sorted(VOICE_OPTIONS),
                "description": "Gemini prebuilt voice, e.g. Kore, Charon, Aoede, Puck.",
            },
            "speaker_voice_configs": {
                "type": "array",
                "description": "Optional two-speaker voice map for dialogue.",
                "items": {
                    "type": "object",
                    "required": ["speaker", "voice"],
                    "properties": {
                        "speaker": {"type": "string"},
                        "voice": {"type": "string", "enum": sorted(VOICE_OPTIONS)},
                    },
                },
            },
            "output_path": {"type": "string"},
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string"},
            "audio_duration_seconds": {"type": ["number", "null"]},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "voice": {"type": "string"},
            "audio_tags": {"type": "array", "items": {"type": "string"}},
            "voice_tone": {"type": "string"},
            "model_profile": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["text", "prompt", "audio_tags", "model", "voice", "speaker_voice_configs"]
    side_effects = ["writes WAV audio file to output_path", "calls Gemini API"]
    user_visible_verification = ["Listen to generated audio for natural speech quality and pacing"]
    quality_score = 0.9
    latency_p50_seconds = 6.0

    def _get_api_key(self) -> str | None:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Gemini TTS pricing is model-specific and may change; this planning
        # estimate is intentionally conservative until usage data is returned.
        return round(len(inputs.get("text", "")) * 0.000030, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(success=False, error="No GOOGLE_API_KEY or GEMINI_API_KEY. " + self.install_instructions)

        start = time.time()
        try:
            result = self._generate(inputs, api_key=api_key)
        except Exception as exc:
            return ToolResult(success=False, error=f"Google TTS failed: {self._safe_error(exc)}")

        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = result.cost_usd or self.estimate_cost(inputs)
        return result

    def _generate(self, inputs: dict[str, Any], *, api_key: str) -> ToolResult:
        import requests

        self._validate_inputs(inputs)
        model = self._resolve_model(inputs)
        voice_name = self._resolve_voice(inputs.get("voice"))
        audio_tags = self._normalize_audio_tags(inputs.get("audio_tags"))
        output_path = Path(inputs.get("output_path", "google_tts.wav"))

        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json=self._payload(inputs, voice_name=voice_name),
            timeout=120,
        )
        response.raise_for_status()

        pcm_audio = self._extract_audio(response.json())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_pcm_as_wav(output_path, pcm_audio)

        audio_duration = self._audio_duration(output_path)
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                "model_profile": self.MODEL_PROFILES.get(model),
                "voice": voice_name,
                "voice_tone": self.VOICE_OPTIONS.get(voice_name),
                "delivery_preset": inputs.get("delivery_preset"),
                "audio_tags": audio_tags,
                "duration_target_seconds": inputs.get("duration_target_seconds"),
                "speaker_voice_configs": inputs.get("speaker_voice_configs"),
                "text_length": len(inputs.get("text", "")),
                "prompt": inputs.get("prompt"),
                "output": str(output_path),
                "format": "wav",
                "audio_duration_seconds": round(audio_duration, 2) if audio_duration else None,
            },
            artifacts=[str(output_path)],
            model=f"google-gemini-tts/{model}/{voice_name}",
        )

    def _payload(self, inputs: dict[str, Any], *, voice_name: str) -> dict[str, Any]:
        speech_config = self._speech_config(inputs, voice_name=voice_name)
        return {
            "contents": [
                {
                    "parts": [
                        {
                            "text": self._prompted_text(inputs),
                        }
                    ]
                }
            ],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": speech_config,
            },
        }

    def _speech_config(self, inputs: dict[str, Any], *, voice_name: str) -> dict[str, Any]:
        speakers = inputs.get("speaker_voice_configs") or []
        if speakers:
            return {
                "multiSpeakerVoiceConfig": {
                    "speakerVoiceConfigs": [
                        {
                            "speaker": item["speaker"],
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {
                                    "voiceName": item["voice"],
                                }
                            },
                        }
                        for item in speakers
                    ]
                }
            }
        return {
            "voiceConfig": {
                "prebuiltVoiceConfig": {
                    "voiceName": voice_name,
                }
            }
        }

    @classmethod
    def _prompted_text(cls, inputs: dict[str, Any]) -> str:
        text = cls._tagged_text(inputs)
        directions = cls._prompt_directions(inputs)
        if not directions:
            return text
        return f"{' '.join(directions)}\n\n{text}"

    @classmethod
    def _tagged_text(cls, inputs: dict[str, Any]) -> str:
        tags = cls._normalize_audio_tags(inputs.get("audio_tags"))
        if not tags:
            return inputs["text"]
        return f"{' '.join(tags)} {inputs['text']}"

    @staticmethod
    def _normalize_audio_tags(audio_tags: Any) -> list[str]:
        if not audio_tags:
            return []
        if isinstance(audio_tags, str):
            raw_tags = [audio_tags]
        elif isinstance(audio_tags, list):
            raw_tags = audio_tags
        else:
            raise ValueError("audio_tags must be a string or list of strings.")

        if len(raw_tags) > 8:
            raise ValueError("audio_tags supports at most 8 tags.")

        normalized: list[str] = []
        for tag in raw_tags:
            if not isinstance(tag, str):
                raise ValueError("audio_tags entries must be strings.")
            clean = tag.strip()
            if not clean:
                continue
            if "\n" in clean or "\r" in clean:
                raise ValueError("audio_tags entries must be single-line strings.")
            if len(clean) > 48:
                raise ValueError("audio_tags entries must be 48 characters or fewer.")
            if clean.startswith("[") or clean.endswith("]"):
                if not (clean.startswith("[") and clean.endswith("]")):
                    raise ValueError("audio_tags entries must use balanced square brackets.")
                clean = clean[1:-1].strip()
            if not clean:
                continue
            normalized.append(f"[{clean}]")
        return normalized

    @classmethod
    def _prompt_directions(cls, inputs: dict[str, Any]) -> list[str]:
        directions: list[str] = []
        preset = inputs.get("delivery_preset")
        if preset:
            directions.append(cls.DELIVERY_PRESETS[preset])

        duration_target = inputs.get("duration_target_seconds")
        if duration_target:
            directions.append(cls._duration_instruction(float(duration_target)))

        prompt = inputs.get("prompt")
        if prompt:
            directions.append(prompt.strip())
        return directions

    @staticmethod
    def _duration_instruction(target_seconds: float) -> str:
        rounded = round(target_seconds, 1)
        return (
            f"Aim for approximately {rounded:g} seconds of audio. Use natural but "
            "compact pacing; if exact timing conflicts with clarity, keep the speech "
            "clear and close to the target duration."
        )

    @staticmethod
    def _extract_audio(payload: dict[str, Any]) -> bytes:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Gemini API response did not include audio content: {payload}") from exc

        for part in parts:
            inline_data = part.get("inlineData") or part.get("inline_data")
            if inline_data and inline_data.get("data"):
                return base64.b64decode(inline_data["data"])
        raise RuntimeError(f"Gemini API response did not include inline audio data: {payload}")

    @staticmethod
    def _write_pcm_as_wav(path: Path, pcm_audio: bytes) -> None:
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(pcm_audio)

    def _validate_inputs(self, inputs: dict[str, Any]) -> None:
        if not inputs.get("text"):
            raise ValueError("text is required")

        model = self._resolve_model(inputs)
        if model not in self._MODELS:
            raise ValueError(f"Unsupported Gemini TTS model: {model}")

        preset = inputs.get("delivery_preset")
        if preset and preset not in self.DELIVERY_PRESETS:
            allowed = ", ".join(sorted(self.DELIVERY_PRESETS))
            raise ValueError(f"Unsupported Google TTS delivery_preset: {preset}. Expected one of: {allowed}")

        duration_target = inputs.get("duration_target_seconds")
        if duration_target is not None and float(duration_target) <= 0:
            raise ValueError("duration_target_seconds must be greater than 0.")

        self._normalize_audio_tags(inputs.get("audio_tags"))

        speakers = inputs.get("speaker_voice_configs") or []
        if len(speakers) > 2:
            raise ValueError("Gemini TTS supports at most two speakers.")
        for item in speakers:
            if not item.get("speaker") or not item.get("voice"):
                raise ValueError("Each speaker_voice_configs item needs speaker and voice.")
            self._resolve_voice(item["voice"])

        self._resolve_voice(inputs.get("voice"))

    def _resolve_model(self, inputs: dict[str, Any]) -> str:
        model = inputs.get("model") or self.DEFAULT_MODEL
        if model == "gemini":
            return self.DEFAULT_MODEL
        return model

    @classmethod
    def _resolve_voice(cls, voice: Any) -> str:
        voice_name = voice or cls.DEFAULT_VOICE
        if not isinstance(voice_name, str):
            raise ValueError("Gemini TTS voice must be a string.")
        if voice_name not in cls.VOICE_OPTIONS:
            allowed = ", ".join(sorted(cls.VOICE_OPTIONS))
            raise ValueError(f"Unsupported Gemini TTS voice: {voice_name}. Expected one of: {allowed}")
        return voice_name

    @staticmethod
    def _audio_duration(path: Path) -> float | None:
        try:
            from tools.analysis.audio_probe import probe_duration

            return probe_duration(path)
        except Exception:
            return None

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        api_key = self._get_api_key()
        if api_key:
            message = message.replace(api_key, "[redacted]")
        return message
