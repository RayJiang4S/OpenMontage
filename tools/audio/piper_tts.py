"""Piper local text-to-speech provider tool."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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


class PiperTTS(BaseTool):
    name = "piper_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "piper"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:piper-tts"]
    install_instructions = (
        "Install Piper TTS:\n"
        "  pip install piper-tts\n"
        "Then download a .onnx voice model and set PIPER_MODEL_PATH to it, "
        "or pass model=/path/to/voice.onnx."
    )
    agent_skills = ["text-to-speech"]

    capabilities = [
        "text_to_speech",
        "offline_generation",
    ]
    supports = {
        "voice_cloning": False,
        "multilingual": False,
        "offline": True,
        "native_audio": True,
    }
    best_for = [
        "offline narration fallback",
        "privacy-sensitive local-only workflows",
    ]
    not_good_for = [
        "best-in-class expressive voice quality",
        "voice clone matching",
    ]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "model": {
                "type": "string",
                "default": "auto",
                "description": "Path or basename of a Piper .onnx model. Use auto to pick PIPER_MODEL_PATH or a local project voice.",
            },
            "speaker_id": {
                "type": "integer",
                "default": 0,
            },
            "length_scale": {
                "type": "number",
                "default": 1.0,
            },
            "sentence_silence": {
                "type": "number",
                "default": 0.3,
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=512, vram_mb=0, disk_mb=200, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=[])
    idempotency_key_fields = ["text", "model", "speaker_id", "length_scale"]
    side_effects = ["writes audio file to output_path"]
    user_visible_verification = ["Listen to generated audio for intelligibility"]

    def get_status(self) -> ToolStatus:
        if not self._runtime_command():
            return ToolStatus.UNAVAILABLE
        try:
            self._resolve_model({"model": "auto"})
            return ToolStatus.AVAILABLE
        except FileNotFoundError:
            return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error="Piper TTS not available. " + self.install_instructions)

        start = time.time()
        try:
            result = self._generate(inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"Local TTS generation failed: {exc}")

        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _generate(self, inputs: dict[str, Any]) -> ToolResult:
        runtime = self._runtime_command()
        if runtime is None:
            raise FileNotFoundError("piper runtime not found. Install piper-tts in the active environment.")

        model_path = self._resolve_model(inputs)
        output_path = Path(inputs.get("output_path", "tts_output.wav"))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        proc = subprocess.run(
            runtime
            + [
                "--model", str(model_path),
                "--speaker", str(inputs.get("speaker_id", 0)),
                "--length-scale", str(inputs.get("length_scale", 1.0)),
                "--sentence-silence", str(inputs.get("sentence_silence", 0.3)),
                "--output_file", str(output_path),
                "--data-dir", str(self._data_dir()),
            ],
            input=inputs["text"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(self._data_dir()),
        )

        if proc.returncode != 0:
            return ToolResult(success=False, error=f"Piper failed (exit {proc.returncode}): {proc.stderr}")
        if not output_path.exists():
            return ToolResult(success=False, error=f"Piper output file missing: {output_path}")

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": str(model_path),
                "speaker_id": inputs.get("speaker_id", 0),
                "text_length": len(inputs["text"]),
                "output": str(output_path),
                "format": "wav",
            },
            artifacts=[str(output_path)],
            model=str(model_path),
        )

    @staticmethod
    def _runtime_command() -> list[str] | None:
        binary = shutil.which("piper")
        if binary:
            return [binary]
        try:
            import piper  # noqa: F401
        except ImportError:
            return None
        return [sys.executable, "-m", "piper"]

    @classmethod
    def _resolve_model(cls, inputs: dict[str, Any]) -> Path:
        model = inputs.get("model") or "auto"
        candidates = cls._candidate_model_paths(str(model))
        for candidate in candidates:
            if candidate.exists() and candidate.suffix == ".onnx":
                return candidate
        searched = ", ".join(str(path) for path in candidates[:8])
        raise FileNotFoundError(
            "Piper voice model not found. Set PIPER_MODEL_PATH or pass model=/path/to/voice.onnx. "
            f"Searched: {searched}"
        )

    @staticmethod
    def _candidate_model_paths(model: str) -> list[Path]:
        repo_root = Path(__file__).resolve().parents[2]
        paths: list[Path] = []

        env_model = os.environ.get("PIPER_MODEL_PATH")
        if env_model:
            paths.append(Path(env_model).expanduser())

        if model and model != "auto":
            model_path = Path(model).expanduser()
            paths.append(model_path)
            if model_path.suffix != ".onnx":
                paths.extend(
                    [
                        Path.home() / ".piper" / "models" / f"{model}.onnx",
                        Path.home() / ".local" / "share" / "piper" / "models" / f"{model}.onnx",
                        repo_root / "assets" / "voices" / f"{model}.onnx",
                    ]
                )

        paths.extend(sorted((repo_root / "projects").glob("*/assets/voices/*.onnx")))
        paths.extend(sorted((Path.home() / ".piper" / "models").glob("*.onnx")))
        paths.extend(sorted((Path.home() / ".local" / "share" / "piper" / "models").glob("*.onnx")))

        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key not in seen:
                unique.append(path)
                seen.add(key)
        return unique

    @staticmethod
    def _data_dir() -> Path:
        data_dir = Path(os.environ.get("PIPER_DATA_DIR", "~/.cache/openmontage/piper")).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
