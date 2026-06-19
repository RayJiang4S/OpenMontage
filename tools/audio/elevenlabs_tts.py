"""ElevenLabs text-to-speech provider tool."""

from __future__ import annotations

import os
import time
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


class ElevenLabsTTS(BaseTool):
    name = "elevenlabs_tts"
    version = "0.2.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "elevenlabs"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Set the ELEVENLABS_API_KEY environment variable:\n"
        "  export ELEVENLABS_API_KEY=your_key_here\n"
        "Get a key at https://elevenlabs.io"
    )
    fallback = "openai_tts"
    fallback_tools = ["openai_tts", "piper_tts"]
    agent_skills = ["elevenlabs", "text-to-speech"]

    capabilities = [
        "text_to_speech",
        "text_to_dialogue",
        "multi_speaker_dialogue",
        "voice_selection",
        "expressive_audio_tags",
        "ssml_support",
        "pronunciation_control",
    ]
    supports = {
        "voice_cloning": True,
        "multilingual": True,
        "offline": False,
        "native_audio": True,
        "audio_tags": True,
        "text_to_dialogue": True,
        "multi_speaker_dialogue": True,
    }
    best_for = [
        "high-quality narration",
        "Eleven v3 expressive narration with inline audio tags",
        "Text to Dialogue multi-voice conversational scenes",
        "voice-sensitive spokesperson videos",
        "multilingual spoken delivery",
    ]
    not_good_for = [
        "fully offline production",
        "privacy-constrained local-only workflows",
    ]

    input_schema = {
        "type": "object",
        "required": [],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["synthesize", "text_to_dialogue"],
                "default": "synthesize",
                "description": "synthesize uses /text-to-speech/{voice_id}; text_to_dialogue uses ElevenLabs Text to Dialogue.",
            },
            "text": {"type": "string", "description": "Text to convert to speech"},
            "dialogue_inputs": {
                "type": "array",
                "description": "Text to Dialogue turns. Each item needs text and voice_id. Up to 10 unique voices and about 2000 total characters.",
                "items": {
                    "type": "object",
                    "required": ["text", "voice_id"],
                    "properties": {
                        "text": {"type": "string"},
                        "voice_id": {"type": "string"},
                    },
                },
            },
            "voice_id": {
                "type": "string",
                "description": "ElevenLabs voice ID (default: Rachel)",
            },
            "model_id": {
                "type": "string",
                "default": "eleven_multilingual_v2",
                "description": "TTS model to use. Use eleven_v3 for the latest expressive model and Text to Dialogue.",
            },
            "audio_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional Eleven v3 expressive inline tags, e.g. [whispers], [excited], [sarcastic]. Bare names are wrapped in brackets.",
            },
            "stability": {
                "type": "number",
                "default": 0.5,
                "minimum": 0,
                "maximum": 1,
            },
            "similarity_boost": {
                "type": "number",
                "default": 0.75,
                "minimum": 0,
                "maximum": 1,
            },
            "style": {
                "type": "number",
                "default": 0.0,
                "minimum": 0,
                "maximum": 1,
            },
            "speed": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 2.0,
                "description": "Optional speaking speed multiplier for supported ElevenLabs models.",
            },
            "use_speaker_boost": {
                "type": "boolean",
                "description": "Optional speaker boost setting for supported voices.",
            },
            "settings": {
                "type": "object",
                "description": "Optional Text to Dialogue settings object passed through to ElevenLabs.",
            },
            "seed": {
                "type": "integer",
                "description": "Optional generation seed for supported ElevenLabs endpoints.",
            },
            "output_path": {"type": "string"},
            "output_format": {
                "type": "string",
                "default": "mp3_44100_128",
                "enum": ["mp3_44100_128", "mp3_44100_192", "pcm_16000", "pcm_24000", "pcm_44100"],
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = [
        "operation",
        "text",
        "dialogue_inputs",
        "voice_id",
        "model_id",
        "audio_tags",
        "seed",
    ]
    side_effects = ["writes audio file to output_path", "calls ElevenLabs API"]
    user_visible_verification = ["Listen to generated audio for natural speech quality"]

    DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
    DEFAULT_MODEL_ID = "eleven_multilingual_v2"
    DEFAULT_DIALOGUE_MODEL_ID = "eleven_v3"

    def get_status(self) -> ToolStatus:
        if os.environ.get("ELEVENLABS_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        text_length = len(inputs.get("text", "") or "")
        dialogue_inputs = inputs.get("dialogue_inputs") or inputs.get("inputs") or []
        if isinstance(dialogue_inputs, list):
            text_length += sum(
                len(item.get("text", "") or "")
                for item in dialogue_inputs
                if isinstance(item, dict)
            )
        return round(text_length * 0.0003, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="No ElevenLabs API key. " + self.install_instructions)

        start = time.time()
        try:
            operation = inputs.get("operation") or (
                "text_to_dialogue" if inputs.get("dialogue_inputs") else "synthesize"
            )
            if operation == "text_to_dialogue":
                result = self._generate_dialogue(inputs, api_key)
            elif operation in {"synthesize", "generate"}:
                result = self._generate(inputs, api_key)
            else:
                return ToolResult(success=False, error=f"Unsupported ElevenLabs TTS operation: {operation}")
        except Exception as exc:
            return ToolResult(success=False, error=f"TTS generation failed: {self._safe_error(exc)}")

        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = self.estimate_cost(inputs)
        return result

    def _generate(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        import requests

        if not inputs.get("text"):
            raise ValueError("text is required for synthesize operation")

        text = self._text_with_audio_tags(inputs)
        voice_id = inputs.get("voice_id", self.DEFAULT_VOICE_ID)
        model_id = inputs.get("model_id", self.DEFAULT_MODEL_ID)
        output_format = inputs.get("output_format", "mp3_44100_128")

        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": model_id,
                "voice_settings": self._voice_settings(inputs),
            },
            params={"output_format": output_format},
            timeout=120,
        )
        response.raise_for_status()

        ext = self._extension_for_format(output_format)
        output_path = Path(inputs.get("output_path", f"tts_output.{ext}"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "operation": "synthesize",
                "model": model_id,
                "voice_id": voice_id,
                "text_length": len(text),
                "audio_tags": self._normalize_audio_tags(inputs.get("audio_tags")),
                "output": str(output_path),
                "format": output_format,
            },
            artifacts=[str(output_path)],
            model=model_id,
        )

    def _generate_dialogue(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        import requests

        dialogue_inputs = self._dialogue_inputs(inputs)
        model_id = inputs.get("model_id", self.DEFAULT_DIALOGUE_MODEL_ID)
        output_format = inputs.get("output_format", "mp3_44100_128")

        body: dict[str, Any] = {
            "inputs": dialogue_inputs,
            "model_id": model_id,
        }
        if inputs.get("settings") is not None:
            if not isinstance(inputs["settings"], dict):
                raise ValueError("settings must be an object for text_to_dialogue.")
            body["settings"] = inputs["settings"]
        if inputs.get("seed") is not None:
            body["seed"] = inputs["seed"]

        response = requests.post(
            "https://api.elevenlabs.io/v1/text-to-dialogue",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json=body,
            params={"output_format": output_format},
            timeout=180,
        )
        response.raise_for_status()

        ext = self._extension_for_format(output_format)
        output_path = Path(inputs.get("output_path", f"elevenlabs_dialogue.{ext}"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "operation": "text_to_dialogue",
                "model": model_id,
                "dialogue_turns": len(dialogue_inputs),
                "unique_voice_count": len({item["voice_id"] for item in dialogue_inputs}),
                "text_length": sum(len(item["text"]) for item in dialogue_inputs),
                "audio_tags": self._normalize_audio_tags(inputs.get("audio_tags")),
                "output": str(output_path),
                "format": output_format,
            },
            artifacts=[str(output_path)],
            model=model_id,
        )

    @staticmethod
    def _voice_settings(inputs: dict[str, Any]) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "stability": inputs.get("stability", 0.5),
            "similarity_boost": inputs.get("similarity_boost", 0.75),
            "style": inputs.get("style", 0.0),
        }
        if inputs.get("speed") is not None:
            settings["speed"] = inputs["speed"]
        if inputs.get("use_speaker_boost") is not None:
            settings["use_speaker_boost"] = inputs["use_speaker_boost"]
        return settings

    @classmethod
    def _text_with_audio_tags(cls, inputs: dict[str, Any]) -> str:
        return cls._apply_audio_tags(inputs["text"], inputs.get("audio_tags"))

    @classmethod
    def _dialogue_inputs(cls, inputs: dict[str, Any]) -> list[dict[str, str]]:
        raw_dialogue = inputs.get("dialogue_inputs") or inputs.get("inputs")
        if not isinstance(raw_dialogue, list) or not raw_dialogue:
            raise ValueError("dialogue_inputs must be a non-empty list for text_to_dialogue.")

        prepared: list[dict[str, str]] = []
        for item in raw_dialogue:
            if not isinstance(item, dict):
                raise ValueError("dialogue_inputs entries must be objects.")
            text = item.get("text")
            voice_id = item.get("voice_id")
            if not text or not voice_id:
                raise ValueError("Each dialogue_inputs entry needs text and voice_id.")
            prepared.append(
                {
                    "text": cls._apply_audio_tags(str(text), inputs.get("audio_tags")),
                    "voice_id": str(voice_id),
                }
            )

        unique_voice_count = len({item["voice_id"] for item in prepared})
        if unique_voice_count > 10:
            raise ValueError("ElevenLabs Text to Dialogue supports at most 10 unique voice IDs.")
        if sum(len(item["text"]) for item in prepared) > 2000:
            raise ValueError("ElevenLabs Text to Dialogue should stay within 2000 total characters.")
        return prepared

    @classmethod
    def _apply_audio_tags(cls, text: str, audio_tags: Any) -> str:
        tags = cls._normalize_audio_tags(audio_tags)
        if not tags:
            return text
        return f"{' '.join(tags)} {text}"

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

    @staticmethod
    def _extension_for_format(output_format: str) -> str:
        return "mp3" if "mp3" in output_format else "wav"

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[redacted]")
        return message
