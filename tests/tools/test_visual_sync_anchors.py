import json
from pathlib import Path

from tools.analysis.visual_sync_anchors import VisualSyncAnchors
from tools.tool_registry import ToolRegistry


def word(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


def transcript() -> dict:
    return {
        "segments": [
            {
                "text": "Finance, business systems, manual submissions, and management processes.",
                "start": 10.0,
                "end": 18.0,
                "words": [
                    word("Finance", 12.82, 13.22),
                    word("business", 13.92, 14.22),
                    word("systems", 14.24, 14.7),
                    word("manual", 15.32, 15.66),
                    word("submissions", 15.68, 16.1),
                    word("and", 16.2, 16.32),
                    word("management", 16.48, 16.88),
                    word("processes", 16.9, 17.3),
                ],
            }
        ]
    }


def test_builds_word_level_visual_sync_table(tmp_path: Path):
    result = VisualSyncAnchors().execute(
        {
            "operation": "build",
            "transcript": transcript(),
            "visual_cues": [
                {
                    "id": "management-processes",
                    "group": "opening",
                    "key": "managementProcesses",
                    "anchor_text": "management processes",
                    "timestamp_seconds": 16.0,
                    "expected_state": "Management processes row starts highlighting.",
                }
            ],
            "output_dir": str(tmp_path),
            "output_ts_path": str(tmp_path / "visualSync.ts"),
        }
    )

    assert result.success
    assert result.data["anchors"] == {"opening": {"managementProcesses": 16.48}}
    assert result.data["anchored_count"] == 1
    assert result.data["findings"][0]["kind"] == "existing_timestamp_drift"
    payload = json.loads(Path(result.data["results_path"]).read_text(encoding="utf-8"))
    assert payload["cue_results"][0]["word_start_seconds"] == 16.48
    ts = Path(result.data["typescript_path"]).read_text(encoding="utf-8")
    assert "export const visualSync" in ts
    assert '"managementProcesses": 16.48' in ts
    assert "activeIndexFromTimes" in ts


def test_stable_policy_starts_before_word_anchor(tmp_path: Path):
    result = VisualSyncAnchors().execute(
        {
            "transcript": transcript(),
            "visual_cues": [
                {
                    "id": "manual-submissions",
                    "group": "opening",
                    "anchor_text": "manual submissions",
                }
            ],
            "output_dir": str(tmp_path),
            "policy": "stable_at_word_start",
            "stable_lead_seconds": 0.25,
        }
    )

    assert result.success
    assert result.data["anchors"]["opening"]["manualSubmissions"] == 15.07


def test_missing_anchor_can_fail_or_report(tmp_path: Path):
    result = VisualSyncAnchors().execute(
        {
            "transcript": transcript(),
            "visual_cues": [{"id": "missing", "group": "opening", "anchor_text": "not in transcript"}],
            "output_dir": str(tmp_path),
        }
    )

    assert not result.success
    assert result.data["missing_count"] == 1
    assert result.data["findings"][0]["kind"] == "anchor_phrase_not_found"


def test_visual_sync_anchors_is_discoverable():
    registry = ToolRegistry()
    registry.discover("tools")
    assert registry.get("visual_sync_anchors") is not None
