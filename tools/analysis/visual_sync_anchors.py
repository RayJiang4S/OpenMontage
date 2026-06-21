"""Build word-level visual sync anchors from a transcript.

This tool turns narration phrases such as "Management processes" into exact
word-start timestamps from a word-level transcript. Composition code can then
import one generated table instead of scattering hand-tuned reveal seconds.
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class VisualSyncAnchors(BaseTool):
    name = "visual_sync_anchors"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []
    install_instructions = "No external runtime is required."
    agent_skills: list[str] = []

    capabilities = [
        "word_level_visual_anchor_generation",
        "remotion_visual_sync_table",
        "visual_cue_timestamp_audit",
        "narration_phrase_matching",
    ]
    supports = {
        "faster_whisper_segments": True,
        "whisperx_segments": True,
        "json_output": True,
        "typescript_output": True,
        "cue_timestamp_delta_check": True,
    }
    best_for = [
        "narration-led Remotion videos where highlights must begin at exact spoken phrases",
        "generating a reusable visualSync table before render",
        "auditing existing visual cue timestamps against word-level transcript timing",
    ]
    not_good_for = [
        "transcripts without word-level timestamps",
        "semantic visual review of rendered frames",
        "music-led videos without narration anchors",
    ]

    input_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["build"], "default": "build"},
            "transcript_path": {"type": "string"},
            "transcript": {"type": "object"},
            "visual_cues_path": {"type": "string"},
            "visual_cues": {"type": "array"},
            "output_dir": {"type": "string"},
            "output_json_path": {"type": "string"},
            "output_ts_path": {"type": "string"},
            "typescript_export_name": {"type": "string", "default": "visualSync"},
            "timestamp_tolerance_seconds": {"type": "number", "default": 0.35},
            "policy": {
                "type": "string",
                "enum": ["onset_at_word_start", "stable_at_word_start"],
                "default": "onset_at_word_start",
            },
            "stable_lead_seconds": {"type": "number", "default": 0.35},
            "fail_on_missing": {"type": "boolean", "default": True},
        },
        "anyOf": [{"required": ["transcript_path"]}, {"required": ["transcript"]}],
        "required": ["visual_cues"],
    }
    output_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string"},
            "status": {"type": "string"},
            "anchors": {"type": "object"},
            "results_path": {"type": "string"},
            "typescript_path": {"type": "string"},
            "cue_count": {"type": "integer"},
            "anchored_count": {"type": "integer"},
            "missing_count": {"type": "integer"},
            "findings": {"type": "array"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=10)
    idempotency_key_fields = [
        "operation",
        "transcript_path",
        "visual_cues_path",
        "visual_cues",
        "output_json_path",
        "output_ts_path",
        "policy",
    ]
    side_effects = [
        "writes visual_sync_anchors.json",
        "optionally writes visualSync TypeScript module",
    ]
    user_visible_verification = [
        "Review cue_results in visual_sync_anchors.json before wiring the generated table into composition code.",
    ]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs.get("operation", "build")
        start = time.time()
        try:
            if operation != "build":
                return ToolResult(success=False, error=f"Unknown operation: {operation}")
            result = self._build(inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"Visual sync anchor generation failed: {exc}")
        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _build(self, inputs: dict[str, Any]) -> ToolResult:
        transcript = self._load_payload(inputs, "transcript", "transcript_path")
        cues = self._load_cues(inputs)
        if not cues:
            raise ValueError("visual_cues must contain at least one cue")

        words = self._flatten_words(transcript)
        if not words:
            raise ValueError("transcript must contain word-level timestamps")

        output_dir = Path(inputs.get("output_dir") or ".").expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = Path(inputs.get("output_json_path") or output_dir / "visual_sync_anchors.json").expanduser()
        ts_path_value = inputs.get("output_ts_path")
        ts_path = Path(ts_path_value).expanduser() if ts_path_value else None
        tolerance = float(inputs.get("timestamp_tolerance_seconds", 0.35))
        policy = str(inputs.get("policy") or "onset_at_word_start")
        stable_lead = float(inputs.get("stable_lead_seconds", 0.35))
        export_name = self._safe_identifier(str(inputs.get("typescript_export_name") or "visualSync")) or "visualSync"

        anchors: dict[str, dict[str, float]] = {}
        cue_results: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []

        for index, cue in enumerate(cues):
            cue_id = str(cue.get("id") or cue.get("cue_id") or cue.get("key") or f"cue-{index + 1}")
            anchor_text = str(cue.get("anchor_text") or cue.get("narration_anchor") or cue.get("phrase") or cue.get("text") or "").strip()
            group_key = self._group_key(cue)
            key = self._cue_key(cue, cue_id)
            timestamp = self._optional_float(cue, ("timestamp_seconds", "time_seconds", "at_seconds", "start_seconds", "start"))
            expected_state = str(cue.get("expected_state") or cue.get("expected") or "")

            if not anchor_text:
                result = self._cue_result(cue_id, group_key, key, anchor_text, timestamp, expected_state, status="missing")
                result["message"] = "Cue has no anchor_text/narration_anchor."
                cue_results.append(result)
                findings.append(self._finding("error", "anchor_text_missing", cue_id, "Cue has no anchor text."))
                continue

            match = self._find_phrase(words, anchor_text)
            if not match:
                result = self._cue_result(cue_id, group_key, key, anchor_text, timestamp, expected_state, status="missing")
                result["message"] = "Anchor phrase was not found in word-level transcript."
                cue_results.append(result)
                findings.append(
                    self._finding("error", "anchor_phrase_not_found", cue_id, "Anchor phrase was not found in word-level transcript.", expected=anchor_text)
                )
                continue

            anchor_start = float(match[0]["start"])
            anchor_end = float(match[-1].get("end", match[-1]["start"]))
            if policy == "stable_at_word_start":
                trigger = max(0.0, anchor_start - stable_lead)
            else:
                trigger = anchor_start
            trigger = round(trigger, 3)
            anchors.setdefault(group_key, {})[key] = trigger

            delta = None
            if timestamp is not None:
                delta = round(float(timestamp) - trigger, 3)
                if abs(delta) > tolerance:
                    findings.append(
                        self._finding(
                            "warning",
                            "existing_timestamp_drift",
                            cue_id,
                            f"Existing cue timestamp is {abs(delta):.3f}s {'late' if delta > 0 else 'early'} relative to generated anchor.",
                            expected=f"{trigger:.3f}s +/- {tolerance:.3f}s",
                            actual=f"{float(timestamp):.3f}s",
                            delta_seconds=delta,
                        )
                    )

            cue_results.append(
                {
                    "id": cue_id,
                    "group": group_key,
                    "key": key,
                    "anchor_text": anchor_text,
                    "matched_text": " ".join(str(item["word"]).strip() for item in match),
                    "word_start_seconds": round(anchor_start, 3),
                    "word_end_seconds": round(anchor_end, 3),
                    "trigger_seconds": trigger,
                    "timestamp_seconds": timestamp,
                    "delta_seconds": delta,
                    "expected_state": expected_state,
                    "policy": policy,
                    "status": "anchored",
                }
            )

        missing_count = sum(1 for item in cue_results if item["status"] != "anchored")
        status = "passed" if missing_count == 0 else "needs-revision"
        results = {
            "version": self.version,
            "tool": self.name,
            "operation": "build",
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "policy": policy,
            "timestamp_tolerance_seconds": tolerance,
            "anchor_source": str(inputs.get("transcript_path") or "inline transcript"),
            "cue_source": str(inputs.get("visual_cues_path") or "inline visual_cues"),
            "anchors": anchors,
            "cue_results": cue_results,
            "findings": findings,
            "summary": {
                "cue_count": len(cues),
                "anchored_count": len(cues) - missing_count,
                "missing_count": missing_count,
                "finding_count": len(findings),
            },
        }

        self._write_json(json_path, results)
        artifacts = [str(json_path)]
        if ts_path:
            ts_path.parent.mkdir(parents=True, exist_ok=True)
            ts_path.write_text(self._typescript_module(anchors, export_name, policy), encoding="utf-8")
            artifacts.append(str(ts_path))

        fail_on_missing = bool(inputs.get("fail_on_missing", True))
        success = not (fail_on_missing and missing_count)
        return ToolResult(
            success=success,
            data={
                "operation": "build",
                "status": status,
                "anchors": anchors,
                "results_path": str(json_path),
                "typescript_path": str(ts_path) if ts_path else "",
                "cue_count": len(cues),
                "anchored_count": len(cues) - missing_count,
                "missing_count": missing_count,
                "findings": findings,
            },
            artifacts=artifacts,
            error=None if success else "One or more visual cues could not be anchored.",
        )

    def _load_payload(self, inputs: dict[str, Any], inline_key: str, path_key: str) -> Any:
        if inputs.get(inline_key) is not None:
            return deepcopy(inputs[inline_key])
        path = inputs.get(path_key)
        if not path:
            raise ValueError(f"{inline_key} or {path_key} is required")
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))

    def _load_cues(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        cues: list[dict[str, Any]] = []
        if isinstance(inputs.get("visual_cues"), list):
            cues.extend(item for item in inputs["visual_cues"] if isinstance(item, dict))
        if inputs.get("visual_cues_path"):
            payload = json.loads(Path(inputs["visual_cues_path"]).expanduser().read_text(encoding="utf-8"))
            if isinstance(payload, list):
                cues.extend(item for item in payload if isinstance(item, dict))
            elif isinstance(payload, dict):
                for key in ("visual_cues", "cues", "items"):
                    if isinstance(payload.get(key), list):
                        cues.extend(item for item in payload[key] if isinstance(item, dict))
                        break
        return cues

    def _flatten_words(self, transcript: Any) -> list[dict[str, Any]]:
        segments = transcript.get("segments") if isinstance(transcript, dict) else transcript
        if not isinstance(segments, list):
            return []
        words: list[dict[str, Any]] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            for word in segment.get("words") or []:
                if not isinstance(word, dict):
                    continue
                text = str(word.get("word") or word.get("text") or "").strip()
                start = self._first_number(word, ("start", "start_seconds", "startMs"))
                end = self._first_number(word, ("end", "end_seconds", "endMs"))
                if start is None or not text:
                    continue
                if "Ms" in word:
                    start = start / 1000.0
                    end = end / 1000.0 if end is not None else None
                words.append(
                    {
                        "word": text,
                        "token": self._token(text),
                        "start": round(float(start), 3),
                        "end": round(float(end if end is not None else start), 3),
                    }
                )
        return [word for word in words if word["token"]]

    @classmethod
    def _find_phrase(cls, words: list[dict[str, Any]], phrase: str) -> list[dict[str, Any]] | None:
        phrase_tokens = [token for token in (cls._token(part) for part in re.findall(r"[\w'-]+|[\u3400-\u9fff]", phrase)) if token]
        if not phrase_tokens:
            return None
        word_tokens = [word["token"] for word in words]
        for index in range(0, len(word_tokens) - len(phrase_tokens) + 1):
            if word_tokens[index:index + len(phrase_tokens)] == phrase_tokens:
                return words[index:index + len(phrase_tokens)]
        return None

    @staticmethod
    def _token(value: str) -> str:
        return re.sub(r"(^[^\w\u3400-\u9fff]+|[^\w\u3400-\u9fff]+$)", "", str(value).lower())

    @classmethod
    def _group_key(cls, cue: dict[str, Any]) -> str:
        raw = cue.get("group_key") or cue.get("group") or cue.get("scene_key") or cue.get("section_key") or cue.get("scene_id") or cue.get("section_id") or "default"
        return cls._camel_key(str(raw)) or "default"

    @classmethod
    def _cue_key(cls, cue: dict[str, Any], cue_id: str) -> str:
        raw = cue.get("key") or cue.get("anchor_key") or cue_id
        return cls._camel_key(str(raw)) or "anchor"

    @staticmethod
    def _camel_key(value: str) -> str:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            return value
        parts = re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]+", value)
        if not parts:
            return ""
        first = parts[0].lower()
        rest = [part[:1].upper() + part[1:] for part in parts[1:]]
        candidate = first + "".join(rest)
        if candidate and candidate[0].isdigit():
            candidate = f"t{candidate}"
        return candidate

    @staticmethod
    def _safe_identifier(value: str) -> str:
        candidate = re.sub(r"\W+", "", value)
        if candidate and candidate[0].isdigit():
            candidate = f"v{candidate}"
        return candidate

    @staticmethod
    def _optional_float(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            if item.get(key) is not None:
                return float(item[key])
        return None

    @staticmethod
    def _first_number(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            if item.get(key) is not None:
                return float(item[key])
        return None

    @staticmethod
    def _cue_result(
        cue_id: str,
        group: str,
        key: str,
        anchor_text: str,
        timestamp: float | None,
        expected_state: str,
        *,
        status: str,
    ) -> dict[str, Any]:
        return {
            "id": cue_id,
            "group": group,
            "key": key,
            "anchor_text": anchor_text,
            "timestamp_seconds": timestamp,
            "expected_state": expected_state,
            "status": status,
        }

    @staticmethod
    def _finding(
        severity: str,
        kind: str,
        item_id: str,
        message: str,
        *,
        expected: Any = "",
        actual: Any = "",
        delta_seconds: float | None = None,
    ) -> dict[str, Any]:
        finding = {
            "severity": severity,
            "kind": kind,
            "item_id": item_id,
            "message": message,
            "expected": expected,
            "actual": actual,
        }
        if delta_seconds is not None:
            finding["delta_seconds"] = delta_seconds
        return finding

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _typescript_module(anchors: dict[str, dict[str, float]], export_name: str, policy: str) -> str:
        payload = json.dumps(anchors, ensure_ascii=False, indent=2)
        return (
            "// Generated by OpenMontage visual_sync_anchors.\n"
            f"// Policy: {policy}. Keep reveal timings tied to transcript word starts.\n"
            f"export const {export_name} = {payload} as const;\n\n"
            "export const activeIndexFromTimes = (seconds: number, times: number[]) =>\n"
            "  times.reduce((active, at, index) => (seconds >= at ? index : active), -1);\n"
        )
