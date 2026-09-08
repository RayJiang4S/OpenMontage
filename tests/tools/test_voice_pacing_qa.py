from __future__ import annotations

import json
import math
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from tools.analysis.voice_pacing_qa import VoicePacingQA
from tools.tool_registry import ToolRegistry


def write_tone(path: Path, duration_seconds: float = 4.0, sample_rate: int = 16000) -> None:
    t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds), endpoint=False)
    audio = 0.2 * np.sin(2 * math.pi * 180 * t)
    pcm = np.int16(np.clip(audio, -1, 1) * 32767)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_voice_pacing_qa_reports_scene_metrics(tmp_path: Path):
    audio = tmp_path / "narration.wav"
    write_tone(audio)
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "sections": [
                    {
                        "id": "s1",
                        "label": "Opening",
                        "start_seconds": 0,
                        "end_seconds": 4,
                        "text": "One two three four five six seven eight",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = VoicePacingQA().execute(
        {
            "input_path": str(audio),
            "script_path": str(script),
            "output_dir": str(tmp_path),
            "acceptable_wpm_min": 100,
            "acceptable_wpm_max": 140,
        }
    )

    assert result.success, result.error
    assert result.data["status"] == "passed"
    report = json.loads(Path(result.data["report_path"]).read_text(encoding="utf-8"))
    assert report["scene_metrics"][0]["words_per_minute_total"] == 120.0
    assert report["silence_scan"]["longest_mid_video_silence_seconds"] == 0.0


def test_voice_pacing_qa_is_discoverable():
    registry = ToolRegistry()
    registry.discover("tools")
    assert registry.get("voice_pacing_qa") is not None
