import json
import py_compile
from pathlib import Path

from lib.pipeline_loader import list_pipelines, load_pipeline
from tools.publishers.video_feedback_preview import VideoFeedbackPreview, VideoFeedbackReviewPackage
from tools.tool_registry import ToolRegistry


def test_video_feedback_review_package_generates_standard_package(tmp_path: Path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"fake-mp4-for-package-test")
    output_dir = tmp_path / "preview"

    result = VideoFeedbackReviewPackage().execute(
        {
            "operation": "prepare",
            "video_path": str(video),
            "output_dir": str(output_dir),
            "video_label": "demo-v1",
            "page_title": "Demo Review",
            "copy_mode": "copy",
            "access_key_env": "DEMO_REVIEW_KEY",
            "scenes": [
                {"id": "s1", "label": "Opening", "start": 0, "end": 10},
                {"id": "s2", "label": "Detail", "start": 10, "end": 20},
            ],
        }
    )

    assert result.success
    assert Path(result.data["index_path"]).exists()
    assert Path(result.data["server_path"]).exists()
    assert Path(result.data["readme_path"]).exists()
    assert Path(result.data["summary_script_path"]).exists()
    assert Path(result.data["served_video_path"]).read_bytes() == video.read_bytes()
    config = json.loads(Path(result.data["config_path"]).read_text(encoding="utf-8"))
    assert config["tool_name"] == "video_feedback_review_package"
    assert config["video_label"] == "demo-v1"
    assert config["access_key_env"] == "DEMO_REVIEW_KEY"
    assert config["scenes"][1]["label"] == "Detail"
    index = Path(result.data["index_path"]).read_text(encoding="utf-8")
    assert "Submit feedback" in index
    assert "Feedback saved" in index
    assert "DEMO_REVIEW_KEY" not in index
    py_compile.compile(result.data["server_path"], doraise=True)
    py_compile.compile(result.data["summary_script_path"], doraise=True)
    server = Path(result.data["server_path"]).read_text(encoding="utf-8")
    assert "/feedback.json" in server
    assert "/feedback.md" in server


def test_video_feedback_review_package_summarizes_feedback(tmp_path: Path):
    feedback_dir = tmp_path / "feedback"
    feedback_dir.mkdir()
    feedback_file = feedback_dir / "feedback-2026-06-21.jsonl"
    feedback_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "a1",
                        "status": "open",
                        "video": "demo-v1",
                        "scope": "timepoint",
                        "playback_time_label": "00:19",
                        "current_time_seconds": 19,
                        "scene": {"label": "Opening"},
                        "message": "Left highlight lands too early.",
                    }
                ),
                json.dumps(
                    {
                        "id": "a2",
                        "status": "open",
                        "video": "demo-v1",
                        "scope": "overall",
                        "current_time_seconds": None,
                        "message": "Overall pacing is better.",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "summary"
    result = VideoFeedbackReviewPackage().execute(
        {
            "operation": "summarize",
            "feedback_dir": str(feedback_dir),
            "output_dir": str(output_dir),
        }
    )

    assert result.success
    assert result.data["feedback_count"] == 2
    assert "Left highlight lands too early." in result.data["summary_markdown"]
    assert Path(result.data["summary_path"]).exists()
    assert Path(result.data["records_path"]).exists()


def test_video_feedback_preview_is_discoverable():
    registry = ToolRegistry()
    registry.discover("tools")
    legacy_tool = registry.get("video_feedback_preview")
    standard_tool = registry.get("video_feedback_review_package")
    assert legacy_tool is not None
    assert standard_tool is not None
    assert legacy_tool.capability == "publishing"
    assert standard_tool.capability == "publishing"
    assert "standard_video_review_package" in standard_tool.capabilities


def test_video_feedback_review_package_is_standard_publish_tool():
    for name in list_pipelines():
        manifest = load_pipeline(name)
        publish_stage = next((stage for stage in manifest["stages"] if stage["name"] == "publish"), None)
        if publish_stage is None:
            continue
        assert "video_feedback_review_package" in publish_stage.get("tools_available", []), name
