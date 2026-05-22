from __future__ import annotations

import json
from pathlib import Path

from tools.publishers.final_video_catalog import FinalVideoCatalog
from tools.tool_registry import ToolRegistry


def test_final_video_catalog_builds_html_and_links_review_page(tmp_path: Path):
    package_dir = tmp_path / "projects" / "demo" / "final-package"
    review_dir = tmp_path / "reviews" / "demo"
    video = package_dir / "video" / "final.mp4"
    cover = package_dir / "cover" / "cover.jpg"
    manifest_path = package_dir / "final_package_manifest.json"
    review_json = review_dir / "final_package_review.json"
    review_html = review_dir / "final_package_review.html"

    video.parent.mkdir(parents=True)
    cover.parent.mkdir(parents=True)
    review_dir.mkdir(parents=True)
    video.write_bytes(b"fake video")
    cover.write_bytes(b"fake cover")
    manifest_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "created_at": "2026-05-18T08:03:48+00:00",
                "project_id": "demo-project",
                "variant_id": "final-v1-1p2x",
                "channel": "skill_landing_page",
                "package_dir": str(package_dir),
                "video": {
                    "package_path": str(video),
                    "duration_seconds": 12.5,
                },
                "cover": {
                    "package_path": str(cover),
                },
                "files": [],
                "references": [],
            }
        ),
        encoding="utf-8",
    )
    review_json.write_text(
        json.dumps({"manifest_path": str(manifest_path)}),
        encoding="utf-8",
    )
    review_html.write_text("<!doctype html><title>Review</title>", encoding="utf-8")

    result = FinalVideoCatalog().execute(
        {
            "scan_roots": [str(tmp_path / "projects")],
            "review_roots": [str(review_dir)],
            "output_dir": str(tmp_path / "catalog"),
        }
    )

    assert result.success, result.error
    catalog_path = Path(result.data["catalog_path"])
    html_path = Path(result.data["html_path"])
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert catalog["entry_count"] == 1
    entry = catalog["entries"][0]
    assert entry["project_id"] == "demo-project"
    assert entry["playback_speed"] == 1.2
    assert entry["playback_speed_label"] == "1.2x"
    assert entry["package_review_path"] == str(review_html)
    assert entry["created_at"] == "2026-05-18T08:03:48+00:00"
    html = html_path.read_text(encoding="utf-8")
    assert "打开交付包页" in html
    assert "Skill 安装页" in html
    assert "1.2x" in html
    assert "2026-05-18" in html


def test_final_video_catalog_is_discoverable():
    registry = ToolRegistry()
    registry.discover("tools")

    assert "final_video_catalog" in registry.list_all()
