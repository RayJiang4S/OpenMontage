import json
import py_compile
from pathlib import Path

from tools.publishers.video_feedback_preview import VideoFeedbackPreview
from tools.tool_registry import ToolRegistry


def test_video_feedback_preview_generates_generic_review_package(tmp_path: Path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"fake-mp4-for-package-test")
    output_dir = tmp_path / "preview"

    result = VideoFeedbackPreview().execute(
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
    assert Path(result.data["served_video_path"]).read_bytes() == video.read_bytes()
    config = json.loads(Path(result.data["config_path"]).read_text(encoding="utf-8"))
    assert config["video_label"] == "demo-v1"
    assert config["access_key_env"] == "DEMO_REVIEW_KEY"
    assert config["scenes"][1]["label"] == "Detail"
    index = Path(result.data["index_path"]).read_text(encoding="utf-8")
    assert "Submit feedback" in index
    assert "Feedback saved" in index
    assert "DEMO_REVIEW_KEY" not in index
    py_compile.compile(result.data["server_path"], doraise=True)


def test_video_feedback_preview_is_discoverable():
    registry = ToolRegistry()
    registry.discover("tools")
    assert registry.get("video_feedback_preview") is not None
