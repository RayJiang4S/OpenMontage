"""Analyze narration pacing consistency for rendered videos or audio tracks.

The tool is intentionally deterministic and local. It combines script scene
timings, optional word-level transcripts, silence scanning, loudness checks,
and a lightweight pitch estimate so narration-led videos can catch sudden
speed, pause, or tone changes before final review.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

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


_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")


class VoicePacingQA(BaseTool):
    name = "voice_pacing_qa"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe", "python:numpy"]
    install_instructions = (
        "Install ffmpeg and numpy:\n"
        "  macOS: brew install ffmpeg && python3 -m pip install numpy\n"
        "  Linux: sudo apt install ffmpeg && python3 -m pip install numpy"
    )

    capabilities = [
        "narration_scene_wpm_check",
        "rolling_word_pacing_check",
        "scene_transition_voice_jump_check",
        "pause_and_dead_air_detection",
        "loudness_and_pitch_consistency_check",
    ]
    supports = {
        "rendered_video_input": True,
        "audio_input": True,
        "script_sections": True,
        "word_level_transcript": True,
        "timeline_time_shifts": True,
        "json_report": True,
    }
    best_for = [
        "narration-led executive explainers where scene-to-scene voice pace should stay calm",
        "checking final renders before feedback review or publish packaging",
        "catching TTS segment changes that alter speed, pauses, loudness, or pitch",
    ]
    not_good_for = [
        "judging semantic voice acting quality without human listening",
        "music-led edits where spoken-word pacing is not the timing authority",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string"},
            "script_path": {"type": "string"},
            "scenes_path": {"type": "string"},
            "scenes": {"type": "array"},
            "transcript_path": {"type": "string"},
            "transcript": {"type": "object"},
            "time_shifts": {
                "type": "array",
                "description": "Timeline transforms for transcript words, e.g. [{from_seconds: 82, shift_seconds: -4}].",
            },
            "output_dir": {"type": "string"},
            "output_json_path": {"type": "string"},
            "target_wpm": {"type": "number", "default": 128},
            "acceptable_wpm_min": {"type": "number", "default": 110},
            "acceptable_wpm_max": {"type": "number", "default": 150},
            "scene_wpm_delta_warning_pct": {"type": "number", "default": 18},
            "scene_wpm_delta_fail_pct": {"type": "number", "default": 28},
            "rolling_window_seconds": {"type": "number", "default": 12},
            "rolling_step_seconds": {"type": "number", "default": 3},
            "rolling_wpm_delta_warning_pct": {"type": "number", "default": 35},
            "silence_threshold_db": {"type": "number", "default": -38},
            "silence_min_duration_seconds": {"type": "number", "default": 0.45},
            "mid_silence_warning_seconds": {"type": "number", "default": 1.25},
            "mid_silence_fail_seconds": {"type": "number", "default": 2.5},
            "tail_silence_allowance_seconds": {"type": "number", "default": 3.0},
            "loudness_delta_warning_db": {"type": "number", "default": 3.0},
            "pitch_delta_warning_hz": {"type": "number", "default": 35.0},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "input_path": {"type": "string"},
            "duration_seconds": {"type": "number"},
            "scene_count": {"type": "integer"},
            "findings": {"type": "array"},
            "report_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=512, vram_mb=0, disk_mb=20)
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = [
        "input_path",
        "script_path",
        "scenes_path",
        "transcript_path",
        "time_shifts",
        "target_wpm",
    ]
    side_effects = ["writes voice_pacing_qa_report.json"]
    user_visible_verification = [
        "Review findings and scene_metrics before approving a narration-led final render.",
        "If audio speed is changed, regenerate transcript, captions, and visual sync anchors before rerendering.",
    ]

    def get_status(self) -> ToolStatus:
        try:
            self.check_dependencies()
        except Exception:
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.time()
        try:
            result = self._execute(inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"Voice pacing QA failed: {exc}")
        result.duration_seconds = round(time.time() - started, 2)
        return result

    def _execute(self, inputs: dict[str, Any]) -> ToolResult:
        input_path = Path(inputs["input_path"]).expanduser().resolve()
        if not input_path.exists():
            return ToolResult(success=False, error=f"File not found: {input_path}")

        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            return ToolResult(success=False, error="ffmpeg and ffprobe are required")

        probe = self._probe(input_path)
        if not probe["has_audio"]:
            return ToolResult(success=False, error=f"No audio stream found: {input_path}")

        scenes = self._load_scenes(inputs)
        transcript = self._load_optional_payload(inputs, "transcript", "transcript_path")
        time_shifts = list(inputs.get("time_shifts") or [])
        words = self._apply_time_shifts(self._flatten_words(transcript), time_shifts) if transcript else []

        if not scenes:
            scenes = [
                {
                    "id": "full",
                    "label": "Full audio",
                    "start_seconds": 0.0,
                    "end_seconds": probe["duration_seconds"],
                    "text": " ".join(word["word"] for word in words),
                }
            ]

        scenes = self._sanitize_scenes(scenes, probe["duration_seconds"])
        if not scenes:
            return ToolResult(success=False, error="No valid scenes found for pacing analysis")

        thresholds = self._thresholds(inputs)
        silence = self._silence_scan(input_path, probe["duration_seconds"], thresholds)
        scene_metrics = [
            self._scene_metric(input_path, scene, words, thresholds)
            for scene in scenes
        ]
        rolling_windows = self._rolling_word_windows(words, probe["duration_seconds"], thresholds)
        findings = self._build_findings(scene_metrics, rolling_windows, silence, thresholds)
        status = self._status_from_findings(findings)

        report = {
            "version": "1.0",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "input_path": str(input_path),
            "duration_seconds": probe["duration_seconds"],
            "audio_stream": probe["audio_stream"],
            "thresholds": thresholds,
            "time_shifts": time_shifts,
            "scene_metrics": scene_metrics,
            "rolling_windows": rolling_windows,
            "silence_scan": silence,
            "findings": findings,
            "status": status,
            "interpretation": self._interpretation(status, findings),
        }

        output_dir = Path(inputs.get("output_dir") or input_path.parent).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = Path(inputs.get("output_json_path") or output_dir / "voice_pacing_qa_report.json").expanduser()
        if not output_path.is_absolute():
            output_path = output_dir / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        return ToolResult(
            success=status != "failed",
            data={
                "status": status,
                "input_path": str(input_path),
                "duration_seconds": probe["duration_seconds"],
                "scene_count": len(scene_metrics),
                "finding_count": len(findings),
                "findings": findings,
                "report_path": str(output_path),
            },
            artifacts=[str(output_path)],
        )

    def _thresholds(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "target_wpm": float(inputs.get("target_wpm", 128)),
            "acceptable_wpm_min": float(inputs.get("acceptable_wpm_min", 110)),
            "acceptable_wpm_max": float(inputs.get("acceptable_wpm_max", 150)),
            "scene_wpm_delta_warning_pct": float(inputs.get("scene_wpm_delta_warning_pct", 18)),
            "scene_wpm_delta_fail_pct": float(inputs.get("scene_wpm_delta_fail_pct", 28)),
            "rolling_window_seconds": float(inputs.get("rolling_window_seconds", 12)),
            "rolling_step_seconds": float(inputs.get("rolling_step_seconds", 3)),
            "rolling_wpm_delta_warning_pct": float(inputs.get("rolling_wpm_delta_warning_pct", 35)),
            "silence_threshold_db": float(inputs.get("silence_threshold_db", -38)),
            "silence_min_duration_seconds": float(inputs.get("silence_min_duration_seconds", 0.45)),
            "mid_silence_warning_seconds": float(inputs.get("mid_silence_warning_seconds", 1.25)),
            "mid_silence_fail_seconds": float(inputs.get("mid_silence_fail_seconds", 2.5)),
            "tail_silence_allowance_seconds": float(inputs.get("tail_silence_allowance_seconds", 3.0)),
            "loudness_delta_warning_db": float(inputs.get("loudness_delta_warning_db", 3.0)),
            "pitch_delta_warning_hz": float(inputs.get("pitch_delta_warning_hz", 35.0)),
        }

    def _probe(self, path: Path) -> dict[str, Any]:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float((data.get("format") or {}).get("duration") or 0.0)
        if audio_stream and audio_stream.get("duration"):
            duration = max(duration, float(audio_stream["duration"]))
        return {
            "duration_seconds": round(duration, 3),
            "has_audio": audio_stream is not None,
            "audio_stream": {
                "codec": audio_stream.get("codec_name") if audio_stream else None,
                "sample_rate": int(audio_stream.get("sample_rate") or 0) if audio_stream else None,
                "channels": int(audio_stream.get("channels") or 0) if audio_stream else None,
                "duration_seconds": round(float(audio_stream.get("duration") or duration), 3) if audio_stream else 0,
            },
        }

    def _load_optional_payload(self, inputs: dict[str, Any], payload_key: str, path_key: str) -> dict[str, Any] | None:
        if inputs.get(payload_key) is not None:
            return dict(inputs[payload_key])
        path_value = inputs.get(path_key)
        if not path_value:
            return None
        return json.loads(Path(path_value).expanduser().read_text(encoding="utf-8"))

    def _load_scenes(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        if inputs.get("scenes"):
            return [dict(scene) for scene in inputs["scenes"]]
        for key in ("script_path", "scenes_path"):
            if not inputs.get(key):
                continue
            payload = json.loads(Path(inputs[key]).expanduser().read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [dict(scene) for scene in payload]
            if isinstance(payload, dict):
                if isinstance(payload.get("sections"), list):
                    return [dict(scene) for scene in payload["sections"]]
                if isinstance(payload.get("scenes"), list):
                    return [dict(scene) for scene in payload["scenes"]]
        return []

    def _sanitize_scenes(self, scenes: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
        clean: list[dict[str, Any]] = []
        for index, scene in enumerate(scenes):
            start = self._first_float(scene, ("start_seconds", "start", "from_seconds")) or 0.0
            end = self._first_float(scene, ("end_seconds", "end", "to_seconds")) or 0.0
            if end <= start:
                continue
            start = max(0.0, min(float(start), duration))
            end = max(start, min(float(end), duration))
            if end - start < 0.25:
                continue
            clean.append(
                {
                    "id": str(scene.get("id") or scene.get("section_id") or f"scene-{index + 1:02d}"),
                    "label": str(scene.get("label") or scene.get("title") or scene.get("id") or f"Scene {index + 1}"),
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                    "text": str(scene.get("text") or scene.get("script") or scene.get("narration") or ""),
                }
            )
        return clean

    def _first_float(self, payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            if payload.get(key) is not None:
                try:
                    return float(payload[key])
                except (TypeError, ValueError):
                    return None
        return None

    def _flatten_words(self, transcript: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not transcript:
            return []
        words: list[dict[str, Any]] = []
        for segment in transcript.get("segments") or []:
            for raw in segment.get("words") or []:
                token = str(raw.get("word") or raw.get("text") or "").strip()
                if not token:
                    continue
                start = self._word_time(raw, "start")
                end = self._word_time(raw, "end")
                if start is None:
                    continue
                if end is None or end < start:
                    end = start
                words.append({"word": token, "start": round(start, 3), "end": round(end, 3)})
        return sorted(words, key=lambda item: item["start"])

    def _word_time(self, raw: dict[str, Any], base: str) -> float | None:
        candidates = [base, f"{base}_seconds", f"{base}Seconds"]
        ms_key = f"{base}Ms"
        for key in candidates:
            if raw.get(key) is not None:
                try:
                    value = float(raw[key])
                    return value / 1000.0 if value > 10000 else value
                except (TypeError, ValueError):
                    return None
        if raw.get(ms_key) is not None:
            try:
                return float(raw[ms_key]) / 1000.0
            except (TypeError, ValueError):
                return None
        return None

    def _apply_time_shifts(self, words: list[dict[str, Any]], shifts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not shifts:
            return words
        shifted: list[dict[str, Any]] = []
        for word in words:
            offset = 0.0
            for shift in shifts:
                start_at = float(shift.get("from_seconds", shift.get("at_seconds", 0.0)))
                if word["start"] >= start_at:
                    offset += float(shift.get("shift_seconds", 0.0))
            shifted.append(
                {
                    **word,
                    "start": round(max(0.0, word["start"] + offset), 3),
                    "end": round(max(0.0, word["end"] + offset), 3),
                }
            )
        return sorted(shifted, key=lambda item: item["start"])

    def _scene_metric(
        self,
        input_path: Path,
        scene: dict[str, Any],
        words: list[dict[str, Any]],
        thresholds: dict[str, Any],
    ) -> dict[str, Any]:
        start = float(scene["start_seconds"])
        end = float(scene["end_seconds"])
        duration = max(0.001, end - start)
        script_word_count = len(_WORD_RE.findall(scene.get("text", "")))
        scene_words = [w for w in words if start <= w["start"] < end]
        transcript_word_count = len(scene_words)
        word_count = script_word_count or transcript_word_count

        pcm, sample_rate = self._decode_audio(input_path, start, duration)
        audio = self._audio_stats(pcm, sample_rate, thresholds)
        pitch = self._pitch_stats(pcm, sample_rate, audio["activity_threshold_dbfs"])

        wpm_total = (word_count / duration) * 60.0 if word_count else 0.0
        speaking_duration = max(0.001, audio["active_duration_seconds"])
        wpm_active = (word_count / speaking_duration) * 60.0 if word_count else 0.0
        return {
            "id": scene["id"],
            "label": scene["label"],
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "duration_seconds": round(duration, 3),
            "script_word_count": script_word_count,
            "transcript_word_count": transcript_word_count,
            "words_per_minute_total": round(wpm_total, 1),
            "words_per_minute_active": round(wpm_active, 1),
            "speech_activity_ratio": audio["activity_ratio"],
            "active_duration_seconds": audio["active_duration_seconds"],
            "rms_dbfs": audio["rms_dbfs"],
            "peak_dbfs": audio["peak_dbfs"],
            "pitch_median_hz": pitch["median_hz"],
            "pitch_iqr_hz": pitch["iqr_hz"],
            "pitch_sample_count": pitch["sample_count"],
        }

    def _decode_audio(self, path: Path, start: float, duration: float, sample_rate: int = 16000) -> tuple[np.ndarray, int]:
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "pipe:1",
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=max(30, int(duration) + 30), check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="ignore") or "ffmpeg audio decode failed")
        pcm = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, sample_rate

    def _audio_stats(self, pcm: np.ndarray, sample_rate: int, thresholds: dict[str, Any]) -> dict[str, Any]:
        if pcm.size == 0:
            return {
                "rms_dbfs": -120.0,
                "peak_dbfs": -120.0,
                "activity_ratio": 0.0,
                "active_duration_seconds": 0.0,
                "activity_threshold_dbfs": thresholds["silence_threshold_db"],
            }
        rms = math.sqrt(float(np.mean(np.square(pcm))) + 1e-12)
        peak = float(np.max(np.abs(pcm)) + 1e-12)
        rms_dbfs = self._dbfs(rms)
        peak_dbfs = self._dbfs(peak)
        frame = max(1, int(sample_rate * 0.05))
        trimmed = pcm[: (pcm.size // frame) * frame]
        if trimmed.size == 0:
            frame_db = np.array([rms_dbfs])
        else:
            chunks = trimmed.reshape((-1, frame))
            frame_rms = np.sqrt(np.mean(np.square(chunks), axis=1) + 1e-12)
            frame_db = np.array([self._dbfs(float(value)) for value in frame_rms])
        activity_threshold = max(float(thresholds["silence_threshold_db"]), rms_dbfs - 24.0)
        active_frames = frame_db > activity_threshold
        active_duration = float(np.sum(active_frames)) * 0.05
        total_duration = pcm.size / sample_rate
        return {
            "rms_dbfs": round(rms_dbfs, 1),
            "peak_dbfs": round(peak_dbfs, 1),
            "activity_ratio": round(active_duration / total_duration if total_duration else 0.0, 3),
            "active_duration_seconds": round(active_duration, 3),
            "activity_threshold_dbfs": round(activity_threshold, 1),
        }

    def _pitch_stats(self, pcm: np.ndarray, sample_rate: int, activity_threshold_dbfs: float) -> dict[str, Any]:
        if pcm.size < int(sample_rate * 0.08):
            return {"median_hz": None, "iqr_hz": None, "sample_count": 0}

        frame_size = int(sample_rate * 0.04)
        hop = int(sample_rate * 0.10)
        min_lag = max(1, int(sample_rate / 320.0))
        max_lag = min(frame_size - 1, int(sample_rate / 80.0))
        window = np.hanning(frame_size).astype(np.float32)
        pitches: list[float] = []

        for offset in range(0, pcm.size - frame_size, hop):
            frame = pcm[offset : offset + frame_size]
            rms = math.sqrt(float(np.mean(np.square(frame))) + 1e-12)
            if self._dbfs(rms) < activity_threshold_dbfs:
                continue
            centered = (frame - float(np.mean(frame))) * window
            autocorr = np.correlate(centered, centered, mode="full")[frame_size - 1 :]
            if autocorr[0] <= 1e-9 or max_lag >= autocorr.size:
                continue
            search = autocorr[min_lag : max_lag + 1]
            if search.size == 0:
                continue
            lag = int(np.argmax(search)) + min_lag
            confidence = float(autocorr[lag] / autocorr[0])
            if confidence < 0.28:
                continue
            pitches.append(sample_rate / lag)

        if not pitches:
            return {"median_hz": None, "iqr_hz": None, "sample_count": 0}
        arr = np.array(pitches)
        q1, q3 = np.percentile(arr, [25, 75])
        return {
            "median_hz": round(float(np.median(arr)), 1),
            "iqr_hz": round(float(q3 - q1), 1),
            "sample_count": int(arr.size),
        }

    def _dbfs(self, value: float) -> float:
        return max(-120.0, 20.0 * math.log10(max(value, 1e-12)))

    def _silence_scan(self, path: Path, duration: float, thresholds: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            "ffmpeg",
            "-v",
            "info",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={thresholds['silence_threshold_db']}dB:d={thresholds['silence_min_duration_seconds']}",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=max(60, int(duration) + 30), check=False)
        segments: list[dict[str, float]] = []
        current_start: float | None = None
        for line in result.stderr.splitlines():
            start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
            if start_match:
                current_start = float(start_match.group(1))
                continue
            end_match = re.search(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line)
            if end_match and current_start is not None:
                end = float(end_match.group(1))
                segments.append(
                    {
                        "start_seconds": round(current_start, 3),
                        "end_seconds": round(end, 3),
                        "duration_seconds": round(float(end_match.group(2)), 3),
                    }
                )
                current_start = None
        if current_start is not None:
            segments.append(
                {
                    "start_seconds": round(current_start, 3),
                    "end_seconds": round(duration, 3),
                    "duration_seconds": round(max(0.0, duration - current_start), 3),
                }
            )

        tail_allowance = thresholds["tail_silence_allowance_seconds"]
        mid = [seg for seg in segments if seg["start_seconds"] < max(0.0, duration - tail_allowance)]
        tail = [seg for seg in segments if seg["start_seconds"] >= max(0.0, duration - tail_allowance)]
        return {
            "threshold_db": thresholds["silence_threshold_db"],
            "min_duration_seconds": thresholds["silence_min_duration_seconds"],
            "segments": segments,
            "mid_video_segments": mid,
            "tail_segments": tail,
            "longest_mid_video_silence_seconds": round(max([s["duration_seconds"] for s in mid] or [0.0]), 3),
            "longest_tail_silence_seconds": round(max([s["duration_seconds"] for s in tail] or [0.0]), 3),
        }

    def _rolling_word_windows(
        self,
        words: list[dict[str, Any]],
        duration: float,
        thresholds: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not words:
            return []
        window = thresholds["rolling_window_seconds"]
        step = thresholds["rolling_step_seconds"]
        windows: list[dict[str, Any]] = []
        t = 0.0
        while t < duration:
            end = min(duration, t + window)
            count = sum(1 for word in words if t <= word["start"] < end)
            wpm = (count / max(0.001, end - t)) * 60.0
            if count >= 5:
                windows.append(
                    {
                        "start_seconds": round(t, 3),
                        "end_seconds": round(end, 3),
                        "word_count": count,
                        "words_per_minute": round(wpm, 1),
                    }
                )
            t += step
        return windows

    def _build_findings(
        self,
        scenes: list[dict[str, Any]],
        rolling_windows: list[dict[str, Any]],
        silence: dict[str, Any],
        thresholds: dict[str, Any],
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        low = thresholds["acceptable_wpm_min"]
        high = thresholds["acceptable_wpm_max"]
        for scene in scenes:
            wpm = scene["words_per_minute_total"]
            if wpm and wpm < low:
                findings.append(
                    self._finding(
                        "warning",
                        "scene_pace_slow",
                        scene["id"],
                        f"{scene['label']} averages {wpm:.1f} WPM, below the calm briefing floor of {low:.0f} WPM.",
                        recommendation="Listen to this scene for over-long pauses; consider shortening holds or regenerating at a slightly steadier pace.",
                    )
                )
            elif wpm > high:
                findings.append(
                    self._finding(
                        "warning",
                        "scene_pace_fast",
                        scene["id"],
                        f"{scene['label']} averages {wpm:.1f} WPM, above the calm briefing ceiling of {high:.0f} WPM.",
                        recommendation="Consider adding small pauses or regenerating the segment with a calmer delivery.",
                    )
                )
            if scene["script_word_count"] and scene["transcript_word_count"]:
                mismatch = abs(scene["script_word_count"] - scene["transcript_word_count"]) / scene["script_word_count"]
                if mismatch > 0.18:
                    findings.append(
                        self._finding(
                            "warning",
                            "scene_transcript_count_mismatch",
                            scene["id"],
                            f"{scene['label']} script/transcript word counts differ by {mismatch:.0%}.",
                            recommendation="Confirm transcript time shifts and scene boundaries before trusting rolling WPM findings.",
                        )
                    )

        for previous, current in zip(scenes, scenes[1:]):
            prev_wpm = previous["words_per_minute_total"]
            cur_wpm = current["words_per_minute_total"]
            if prev_wpm and cur_wpm:
                delta_pct = abs(cur_wpm - prev_wpm) / prev_wpm * 100.0
                if delta_pct >= thresholds["scene_wpm_delta_fail_pct"]:
                    severity = "error"
                    kind = "scene_pace_jump"
                elif delta_pct >= thresholds["scene_wpm_delta_warning_pct"]:
                    severity = "warning"
                    kind = "scene_pace_shift"
                else:
                    severity = ""
                    kind = ""
                if severity:
                    direction = "faster" if cur_wpm > prev_wpm else "slower"
                    findings.append(
                        self._finding(
                            severity,
                            kind,
                            f"{previous['id']}->{current['id']}",
                            f"Scene transition changes from {prev_wpm:.1f} to {cur_wpm:.1f} WPM ({delta_pct:.1f}% {direction}).",
                            recommendation="Review this boundary by ear; if it feels abrupt, retime/regenerate the outlier and then rebuild captions and visual anchors.",
                        )
                    )

            loud_delta = abs(current["rms_dbfs"] - previous["rms_dbfs"])
            if loud_delta >= thresholds["loudness_delta_warning_db"]:
                findings.append(
                    self._finding(
                        "warning",
                        "scene_loudness_jump",
                        f"{previous['id']}->{current['id']}",
                        f"Scene RMS changes by {loud_delta:.1f} dB at the transition.",
                        recommendation="Normalize narration segments or check whether the transition sounds like a different take.",
                    )
                )

            if previous["pitch_median_hz"] and current["pitch_median_hz"]:
                pitch_delta = abs(current["pitch_median_hz"] - previous["pitch_median_hz"])
                if pitch_delta >= thresholds["pitch_delta_warning_hz"]:
                    findings.append(
                        self._finding(
                            "warning",
                            "scene_pitch_jump",
                            f"{previous['id']}->{current['id']}",
                            f"Estimated median pitch changes by {pitch_delta:.1f} Hz at the transition.",
                            recommendation="Listen to the boundary for tone shift; regenerate with the locked provider/voice if the change is audible.",
                        )
                    )

        for previous, current in zip(rolling_windows, rolling_windows[1:]):
            prev_wpm = previous["words_per_minute"]
            cur_wpm = current["words_per_minute"]
            if prev_wpm <= 0:
                continue
            delta_pct = abs(cur_wpm - prev_wpm) / prev_wpm * 100.0
            if delta_pct >= thresholds["rolling_wpm_delta_warning_pct"]:
                findings.append(
                    self._finding(
                        "warning",
                        "rolling_pace_shift",
                        f"{current['start_seconds']:.1f}s",
                        f"Rolling {thresholds['rolling_window_seconds']:.0f}s WPM changes from {prev_wpm:.1f} to {cur_wpm:.1f} ({delta_pct:.1f}%).",
                        recommendation="Check for a local pause or rushed sentence around this timestamp.",
                    )
                )

        longest_mid = silence["longest_mid_video_silence_seconds"]
        if longest_mid >= thresholds["mid_silence_fail_seconds"]:
            findings.append(
                self._finding(
                    "error",
                    "long_mid_video_silence",
                    "audio",
                    f"Longest mid-video silence is {longest_mid:.3f}s.",
                    recommendation="Remove dead air or extend visuals intentionally with an explicit hold.",
                )
            )
        elif longest_mid >= thresholds["mid_silence_warning_seconds"]:
            findings.append(
                self._finding(
                    "warning",
                    "long_narration_pause",
                    "audio",
                    f"Longest mid-video pause is {longest_mid:.3f}s.",
                    recommendation="Listen to the pause; keep it only if it feels like a natural scene breath.",
                )
            )

        return findings

    def _finding(
        self,
        severity: str,
        kind: str,
        subject: str,
        message: str,
        recommendation: str = "",
    ) -> dict[str, Any]:
        finding = {
            "severity": severity,
            "kind": kind,
            "subject": subject,
            "message": message,
        }
        if recommendation:
            finding["recommendation"] = recommendation
        return finding

    def _status_from_findings(self, findings: list[dict[str, Any]]) -> str:
        if any(item["severity"] == "error" for item in findings):
            return "failed"
        if findings:
            return "passed_with_warnings"
        return "passed"

    def _interpretation(self, status: str, findings: list[dict[str, Any]]) -> str:
        if status == "passed":
            return "Narration pacing, pause structure, loudness, and estimated pitch are within the configured consistency thresholds."
        if status == "passed_with_warnings":
            return "No hard pacing failure was detected, but the warnings should be checked by ear before final approval."
        return "One or more hard pacing failures were detected and should be fixed before final approval."
