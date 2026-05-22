#!/usr/bin/env python3
"""Build a catalog of approved final videos across OpenMontage projects.

Standard entries come from final_package_manifest.json files. Legacy final
folders are supported only to bootstrap older projects that predate the package
helper.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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


ROOT = Path(__file__).resolve().parents[2]


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_uri(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).expanduser().resolve().as_uri()


def created_date_key(value: str | None) -> str:
    if not value:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else ""


def display_created_at(value: str | None) -> str:
    if not value:
        return "未记录"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def created_sort_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def display_channel(value: str | None) -> str:
    labels = {
        "skill_landing_page": "Skill 安装页",
        "legacy": "旧版归档",
        "legacy-archive": "旧归档文件",
        "default": "默认场景",
    }
    return labels.get(value or "", value or "未记录")


def display_source_type(value: str | None) -> str:
    labels = {
        "final_package_manifest": "标准交付包",
        "legacy_final_dir": "旧 final 目录",
        "legacy_archive": "旧归档文件",
    }
    return labels.get(value or "", value or "未知来源")


def display_tag(value: str) -> str:
    labels = {
        "standard-package": "标准交付包",
        "legacy": "旧版归档",
        "legacy-archive": "旧归档文件",
        "final-archive": "旧归档文件",
        "needs-package-manifest": "待补标准交付包",
    }
    if value in {"skill_landing_page", "legacy", "legacy-archive"}:
        return display_channel(value)
    return labels.get(value, value)


def display_warning(value: str) -> str:
    labels = {
        "video file missing": "视频文件缺失",
        "legacy final folder; create a final_package_manifest.json when practical": "旧 final 目录记录，后续建议补成标准交付包。",
        "legacy FINAL_ARCHIVE.md record; create a final_package_manifest.json when practical": "旧 FINAL_ARCHIVE.md 记录，后续建议补成标准交付包。",
    }
    return labels.get(value, value)


def normalize_playback_speed(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 3) if value > 0 else None
    text = str(value).strip().lower()
    if text.endswith("x"):
        text = text[:-1]
    text = text.replace("p", ".")
    try:
        speed = float(text)
    except ValueError:
        return None
    return round(speed, 3) if speed > 0 else None


def infer_playback_speed(*values: str | None, default: float | None = None) -> float | None:
    for value in values:
        if not value:
            continue
        text = str(value).lower()
        match = re.search(r"(?<![a-z0-9])([0-9]+)p([0-9]+)x(?![a-z0-9])", text)
        if match:
            return normalize_playback_speed(f"{match.group(1)}.{match.group(2)}")
        match = re.search(r"(?<![a-z0-9])([0-9]+(?:\.[0-9]+)?)x(?![a-z0-9])", text)
        if match:
            return normalize_playback_speed(match.group(1))
    return default


def playback_speed_label(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}".rstrip("0").rstrip(".") + "x"


def display_reference_label(value: str | None) -> str:
    labels = {
        "Legacy manifest": "旧 MANIFEST",
        "Legacy archive": "旧归档说明",
    }
    return labels.get(value or "", value or "引用文件")


def file_modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def probe_duration(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return round(float(result.stdout.strip()), 3)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def project_id_from_path(path: Path) -> str:
    parts = path.resolve().parts
    if "projects" in parts:
        idx = parts.index("projects")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return path.parent.name


def text_after(label: str, text: str) -> str | None:
    pattern = rf"{re.escape(label)}[：:]\s*(.+)"
    match = re.search(pattern, text)
    if match:
        return match.group(1).strip()
    return None


def first_markdown_title(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def parse_legacy_manifest(path: Path) -> dict[str, str | None]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {
        "title": first_markdown_title(text),
        "variant_id": text_after("归档版本", text),
        "created_date": text_after("归档日期", text),
        "use_case": text_after("用途", text),
        "duration_note": text_after("视频时长", text),
        "voice_note": text_after("旁白音色", text),
        "visual_note": text_after("画面主题", text),
    }


@dataclass
class CatalogEntry:
    id: str
    title: str
    project_id: str
    variant_id: str
    channel: str
    source_type: str
    status: str
    video_path: str
    cover_path: str | None
    package_manifest_path: str | None
    package_review_path: str | None
    package_dir: str | None
    duration_seconds: float | None
    playback_speed: float | None
    playback_speed_label: str | None
    use_case: str | None
    tags: list[str]
    privacy_notes: list[str]
    warnings: list[str]
    references: list[dict[str, str]]
    created_at: str | None
    sha256: str | None

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


def entry_id(project_id: str, variant_id: str, channel: str, video_path: str) -> str:
    base = "|".join([project_id, variant_id, channel, video_path])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def entry_from_package_manifest(path: Path, review_pages: dict[str, str] | None = None) -> CatalogEntry:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    video = manifest.get("video") or {}
    cover = manifest.get("cover") or {}
    video_path = Path(video.get("package_path") or video.get("source_path") or "")
    cover_path = cover.get("package_path") or cover.get("source_path")
    project_id = manifest.get("project_id") or project_id_from_path(path)
    variant_id = manifest.get("variant_id") or path.parent.name
    channel = manifest.get("channel") or "default"
    playback_speed = (
        normalize_playback_speed(manifest.get("playback_speed"))
        or normalize_playback_speed(manifest.get("speed"))
        or normalize_playback_speed(video.get("playback_speed"))
        or normalize_playback_speed(video.get("speed"))
        or infer_playback_speed(
            variant_id,
            str(video_path),
            str(path.parent),
            default=1.0,
        )
    )
    references = []
    for item in manifest.get("references") or []:
        references.append(
            {
                "role": str(item.get("role") or ""),
                "label": str(item.get("label") or item.get("role") or ""),
                "path": str(item.get("path") or ""),
            }
        )
    title = project_id.replace("-", " ")
    warnings = []
    if not video_path.exists():
        warnings.append("video file missing")
    return CatalogEntry(
        id=entry_id(project_id, variant_id, channel, str(video_path)),
        title=title,
        project_id=project_id,
        variant_id=variant_id,
        channel=channel,
        source_type="final_package_manifest",
        status="approved",
        video_path=str(video_path),
        cover_path=str(cover_path) if cover_path else None,
        package_manifest_path=str(path),
        package_review_path=(review_pages or {}).get(str(path.resolve())),
        package_dir=str(path.parent),
        duration_seconds=video.get("duration_seconds") or probe_duration(video_path),
        playback_speed=playback_speed,
        playback_speed_label=playback_speed_label(playback_speed),
        use_case=None,
        tags=[channel, "standard-package"],
        privacy_notes=[],
        warnings=warnings,
        references=references,
        created_at=manifest.get("created_at") or file_modified_at(video_path),
        sha256=sha256(video_path),
    )


def entry_from_legacy_final_dir(path: Path) -> CatalogEntry | None:
    manifest_path = path / "MANIFEST.md"
    info = parse_legacy_manifest(manifest_path)
    mp4s = sorted(path.glob("*.mp4"))
    if not mp4s:
        return None
    video_path = mp4s[0]
    covers = sorted(list(path.glob("*.jpg")) + list(path.glob("*.png")))
    cover_path = covers[0] if covers else None
    project_id = project_id_from_path(path)
    variant_id = info.get("variant_id") or path.name
    playback_speed = infer_playback_speed(str(variant_id), str(video_path), str(path))
    title = info.get("title") or project_id.replace("-", " ")
    warnings = ["legacy final folder; create a final_package_manifest.json when practical"]
    return CatalogEntry(
        id=entry_id(project_id, str(variant_id), "legacy", str(video_path)),
        title=str(title),
        project_id=project_id,
        variant_id=str(variant_id),
        channel="legacy",
        source_type="legacy_final_dir",
        status="approved",
        video_path=str(video_path),
        cover_path=str(cover_path) if cover_path else None,
        package_manifest_path=None,
        package_review_path=None,
        package_dir=str(path),
        duration_seconds=probe_duration(video_path),
        playback_speed=playback_speed,
        playback_speed_label=playback_speed_label(playback_speed),
        use_case=info.get("use_case"),
        tags=["legacy", "needs-package-manifest"],
        privacy_notes=[],
        warnings=warnings,
        references=(
            [{"role": "legacy_manifest", "label": "Legacy manifest", "path": str(manifest_path)}]
            if manifest_path.exists()
            else []
        ),
        created_at=info.get("created_date") or file_modified_at(video_path),
        sha256=sha256(video_path),
    )


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "-", value.strip())
    return re.sub(r"-{2,}", "-", value).strip("-") or "item"


def parse_markdown_table_rows(text: str, heading: str) -> list[list[str]]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower() == f"## {heading}".lower():
            start = index + 1
            break
    if start is None:
        return []
    rows: list[list[str]] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
            continue
        rows.append(cells)
    return rows[1:] if rows and any("version" in cell.lower() for cell in rows[0]) else rows


def markdown_code_path(value: str) -> str:
    match = re.search(r"`([^`]+)`", value)
    return match.group(1) if match else value.strip()


def entry_from_legacy_archive(path: Path) -> list[CatalogEntry]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    project_id = project_id_from_path(path)
    archive_title = first_markdown_title(text) or project_id.replace("-", " ")
    rows = parse_markdown_table_rows(text, "Final Deliverables")
    entries: list[CatalogEntry] = []
    for row in rows:
        if len(row) < 3:
            continue
        label, render_path, purpose = row[0], markdown_code_path(row[1]), row[2]
        video_path = (path.parent / render_path).resolve()
        if not video_path.exists() or video_path.suffix.lower() not in {".mp4", ".mov", ".webm"}:
            continue
        variant_id = slug(label)
        playback_speed = infer_playback_speed(label, render_path, str(video_path))
        entries.append(
            CatalogEntry(
                id=entry_id(project_id, variant_id, "legacy-archive", str(video_path)),
                title=f"{archive_title} · {label}",
                project_id=project_id,
                variant_id=variant_id,
                channel="legacy-archive",
                source_type="legacy_archive",
                status="approved",
                video_path=str(video_path),
                cover_path=None,
                package_manifest_path=None,
                package_review_path=None,
                package_dir=str(path.parent),
                duration_seconds=probe_duration(video_path),
                playback_speed=playback_speed,
                playback_speed_label=playback_speed_label(playback_speed),
                use_case=purpose,
                tags=["legacy", "final-archive", "needs-package-manifest"],
                privacy_notes=[],
                warnings=[
                    "legacy FINAL_ARCHIVE.md record; create a final_package_manifest.json when practical"
                ],
                references=[
                    {"role": "legacy_archive", "label": "Legacy archive", "path": str(path)}
                ],
                created_at=file_modified_at(video_path),
                sha256=sha256(video_path),
            )
        )
    return entries


def scan_package_manifests(scan_roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in scan_roots:
        if root.is_file() and root.name == "final_package_manifest.json":
            found.append(root.resolve())
        elif root.exists():
            found.extend(path.resolve() for path in root.rglob("final_package_manifest.json"))
    return sorted(set(found))


def scan_legacy_final_dirs(scan_roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for manifest in root.rglob("MANIFEST.md"):
            if "/final/" in str(manifest):
                found.append(manifest.parent.resolve())
    return sorted(set(found))


def scan_legacy_archives(scan_roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in scan_roots:
        if not root.exists():
            continue
        found.extend(path.resolve() for path in root.rglob("FINAL_ARCHIVE.md"))
    return sorted(set(found))


def scan_package_review_pages(scan_roots: list[Path], review_roots: list[Path] | None = None) -> dict[str, str]:
    review_pages: dict[str, str] = {}
    candidates: list[Path] = []
    roots = list(scan_roots)
    roots.extend(review_roots or [])
    scratch = ROOT / ".scratch"
    if scratch.exists():
        roots.append(scratch)
    for root in roots:
        if not root.exists():
            continue
        candidates.extend(root.rglob("final_package_review.json"))
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        manifest_path = data.get("manifest_path")
        if not manifest_path:
            continue
        html_path = path.with_name("final_package_review.html")
        if html_path.exists():
            review_pages[str(Path(manifest_path).expanduser().resolve())] = str(html_path.resolve())
    return review_pages


def build_catalog(
    package_manifest_paths: list[Path],
    legacy_dirs: list[Path],
    legacy_archives: list[Path],
    catalog_path: Path,
    review_pages: dict[str, str] | None = None,
) -> dict[str, Any]:
    entries: list[CatalogEntry] = []
    seen: set[str] = set()
    for path in package_manifest_paths:
        entry = entry_from_package_manifest(path, review_pages)
        if entry.id not in seen:
            entries.append(entry)
            seen.add(entry.id)
    for path in legacy_dirs:
        entry = entry_from_legacy_final_dir(path)
        if entry and entry.id not in seen:
            entries.append(entry)
            seen.add(entry.id)
    for path in legacy_archives:
        for entry in entry_from_legacy_archive(path):
            if entry.id not in seen:
                entries.append(entry)
                seen.add(entry.id)
    entries.sort(key=lambda item: (item.project_id, item.variant_id, item.channel))
    entries.sort(key=lambda item: created_sort_seconds(item.created_at), reverse=True)
    data = {
        "version": "1.0",
        "generated_at": now(),
        "entry_count": len(entries),
        "catalog_path": str(catalog_path),
        "entries": [entry.to_json() for entry in entries],
    }
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def render_html(catalog: dict[str, Any], output_path: Path) -> None:
    entries = catalog.get("entries") or []
    cards = []
    for item in entries:
        video_src = file_uri(item.get("video_path"))
        cover_src = file_uri(item.get("cover_path"))
        created_at = item.get("created_at")
        created_display = display_created_at(created_at)
        created_key = created_date_key(created_at)
        tags = " ".join(
            f"<span class=\"tag\">{html.escape(display_tag(str(tag)))}</span>"
            for tag in item.get("tags", [])
        )
        warnings = " ".join(
            f"<span class=\"warn\">{html.escape(display_warning(str(warning)))}</span>"
            for warning in item.get("warnings", [])
        )
        references = "".join(
            f"<li><a href=\"{html.escape(file_uri(ref.get('path')), quote=True)}\">{html.escape(display_reference_label(ref.get('label') or ref.get('role')))}</a></li>"
            for ref in item.get("references", [])
        )
        review_path = item.get("package_review_path")
        if not review_path and item.get("references"):
            review_path = (item.get("references") or [{}])[0].get("path")
        review_label = "打开交付包页" if item.get("package_review_path") else "打开归档说明"
        review_button = (
            f"<a class=\"primary-link\" href=\"{html.escape(file_uri(review_path), quote=True)}\">{html.escape(review_label)}</a>"
            if review_path
            else "<span class=\"muted\">暂无交付包页</span>"
        )
        manifest_link = ""
        if item.get("package_manifest_path"):
            manifest_link = (
                f"<a href=\"{html.escape(file_uri(item.get('package_manifest_path')), quote=True)}\">"
                "final_package_manifest.json</a>"
            )
        package_link = ""
        if item.get("package_dir"):
            package_link = f"<code>{html.escape(item.get('package_dir') or '')}</code>"
        cover_value = (
            f"<code>{html.escape(item.get('cover_path') or '')}</code>"
            if item.get("cover_path")
            else '<span class="muted">未提供</span>'
        )
        duration_value = (
            f"{html.escape(str(item.get('duration_seconds')))}s"
            if item.get("duration_seconds")
            else "未知"
        )
        speed_value = html.escape(item.get("playback_speed_label") or "未知")
        cards.append(
            f"""
<article class="card" data-project="{html.escape(item.get('project_id') or '')}"
  data-source="{html.escape(item.get('source_type') or '')}"
  data-created="{html.escape(created_key)}"
  data-tags="{html.escape(' '.join(item.get('tags') or []))}">
  <div class="preview">
    {"<video controls preload=\"metadata\" poster=\"" + html.escape(cover_src, quote=True) + "\" src=\"" + html.escape(video_src, quote=True) + "\"></video>" if video_src else ""}
  </div>
    <div class="content">
      <div class="eyebrow">{html.escape(display_source_type(item.get('source_type')))}</div>
    <h2>{html.escape(item.get('title') or '')}</h2>
    <div class="actions">{review_button}</div>
    <div class="meta">
      <div class="meta-item"><span>项目</span><strong>{html.escape(item.get('project_id') or '')}</strong></div>
      <div class="meta-item"><span>版本</span><strong>{html.escape(item.get('variant_id') or '')}</strong></div>
      <div class="meta-item"><span>投放场景</span><strong>{html.escape(display_channel(item.get('channel')))}</strong></div>
      <div class="meta-item"><span>时长</span><strong>{duration_value}</strong></div>
      <div class="meta-item"><span>速度</span><strong>{speed_value}</strong></div>
      <div class="meta-item"><span>生成时间</span><strong>{html.escape(created_display)}</strong></div>
    </div>
    <p>{html.escape(item.get('use_case') or '')}</p>
    <div>{tags}</div>
    <div class="warnings">{warnings}</div>
    <details>
      <summary>文件与引用</summary>
      <p>视频: <code>{html.escape(item.get('video_path') or '')}</code></p>
      <p>封面: {cover_value}</p>
      <p>清单: {manifest_link or '<span class=\"muted\">未提供</span>'}</p>
      <p>目录: {package_link}</p>
      <ul>{references}</ul>
    </details>
  </div>
</article>
"""
        )
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>最终视频目录</title>
<style>
:root {{
  color-scheme: dark;
  --bg: #07111e;
  --panel: #101b2b;
  --line: #273e5c;
  --text: #eef6ff;
  --muted: #aab8cd;
  --cyan: #52d7ff;
  --green: #55d99f;
  --warn: #ffd166;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: radial-gradient(circle at 28% -8%, rgba(82,215,255,.18), transparent 36%), var(--bg);
  color: var(--text);
}}
main {{ width: min(1180px, calc(100vw - 48px)); margin: 0 auto; padding: 36px 0 80px; }}
.hero, .toolbar, .card {{
  border: 1px solid var(--line);
  border-radius: 20px;
  background: linear-gradient(180deg, rgba(17,30,48,.96), rgba(9,17,29,.94));
  box-shadow: 0 18px 60px rgba(0,0,0,.24);
}}
.hero {{ padding: 28px; margin-bottom: 18px; }}
h1 {{ margin: 0 0 10px; font-size: clamp(34px, 5vw, 58px); letter-spacing: 0; }}
p {{ color: var(--muted); line-height: 1.6; }}
.toolbar {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: end; padding: 16px; margin-bottom: 18px; }}
.field {{ display: grid; gap: 6px; }}
.field span {{ color: var(--muted); font-size: 13px; font-weight: 700; }}
input, select {{
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(0,0,0,.22);
  color: var(--text);
  padding: 12px 14px;
  font: inherit;
}}
input[type="search"] {{ min-width: min(420px, 100%); flex: 1; }}
input[type="date"] {{ min-width: 160px; }}
.count {{ color: var(--muted); }}
.card {{ display: grid; grid-template-columns: 42% 1fr; gap: 20px; padding: 18px; margin-bottom: 18px; }}
video {{ width: 100%; border-radius: 14px; border: 1px solid rgba(255,255,255,.12); background: #020712; }}
.eyebrow {{ color: var(--cyan); font-weight: 800; text-transform: uppercase; font-size: 12px; letter-spacing: .08em; }}
h2 {{ margin: 6px 0 12px; font-size: 24px; letter-spacing: 0; }}
.meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin: 10px 0 12px; }}
.meta-item {{
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 12px;
  padding: 10px;
  background: rgba(255,255,255,.03);
}}
.meta span {{ display: block; color: var(--muted); font-size: 12px; }}
.meta strong {{ display: block; margin-top: 4px; overflow-wrap: anywhere; }}
.tag, .warn {{
  display: inline-block;
  border: 1px solid rgba(82,215,255,.28);
  border-radius: 999px;
  padding: 5px 9px;
  margin: 0 6px 6px 0;
  color: var(--cyan);
  background: rgba(82,215,255,.08);
}}
.warn {{ color: var(--warn); border-color: rgba(255,209,102,.28); background: rgba(255,209,102,.08); }}
.muted, code {{ color: var(--muted); overflow-wrap: anywhere; }}
.actions {{ margin: 0 0 12px; }}
.primary-link {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(82,215,255,.4);
  border-radius: 999px;
  padding: 9px 13px;
  color: #07111e;
  background: linear-gradient(135deg, var(--cyan), var(--green));
  font-weight: 800;
  text-decoration: none;
}}
.primary-link:hover {{ filter: brightness(1.08); }}
details {{ margin-top: 12px; }}
summary {{ cursor: pointer; color: var(--green); font-weight: 700; }}
a {{ color: #9aa8ff; }}
@media (max-width: 900px) {{
  main {{ width: min(100vw - 28px, 1180px); padding-top: 22px; }}
  .card {{ grid-template-columns: 1fr; }}
  .meta {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
</style>
</head>
<body>
<main>
  <section class="hero">
    <h1>最终视频目录</h1>
    <p>本地正式成片资产库。标准记录来自 Final Package Helper 的打包清单；旧归档会保留显示，并标记为待补标准交付包。</p>
    <p class="count"><strong>{len(entries)}</strong> 条记录 · 生成于 {html.escape(catalog.get('generated_at') or '')}</p>
  </section>
  <section class="toolbar">
    <input id="search" type="search" placeholder="搜索标题、项目、版本、投放场景、标签">
    <label class="field"><span>来源</span>
      <select id="source">
        <option value="">全部来源</option>
        <option value="final_package_manifest" selected>标准交付包</option>
        <option value="legacy_final_dir">旧 final 目录</option>
        <option value="legacy_archive">旧归档文件</option>
      </select>
    </label>
    <label class="field"><span>开始日期</span><input id="date-start" type="date"></label>
    <label class="field"><span>结束日期</span><input id="date-end" type="date"></label>
    <span id="visible-count" class="count"></span>
  </section>
  <section id="cards">
    {''.join(cards)}
  </section>
</main>
<script>
(() => {{
  const search = document.getElementById('search');
  const source = document.getElementById('source');
  const dateStart = document.getElementById('date-start');
  const dateEnd = document.getElementById('date-end');
  const cards = Array.from(document.querySelectorAll('.card'));
  const visibleCount = document.getElementById('visible-count');
  const allMedia = Array.from(document.querySelectorAll('video'));
  allMedia.forEach((video) => {{
    video.addEventListener('play', () => {{
      allMedia.forEach((other) => {{
        if (other !== video) other.pause();
      }});
    }});
  }});
  function apply() {{
    const q = search.value.trim().toLowerCase();
    const sourceValue = source.value;
    const startValue = dateStart.value;
    const endValue = dateEnd.value;
    let visible = 0;
    cards.forEach((card) => {{
      const text = card.innerText.toLowerCase();
      const created = card.dataset.created || '';
      const sourceOk = !sourceValue || card.dataset.source === sourceValue;
      const searchOk = !q || text.includes(q);
      const dateOk = (!startValue || (created && created >= startValue)) && (!endValue || (created && created <= endValue));
      const ok = searchOk && sourceOk && dateOk;
      card.style.display = ok ? '' : 'none';
      if (ok) visible += 1;
    }});
    visibleCount.textContent = `${{visible}} 条可见`;
  }}
  search.addEventListener('input', apply);
  source.addEventListener('change', apply);
  dateStart.addEventListener('change', apply);
  dateEnd.addEventListener('change', apply);
  apply();
}})();
</script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")


class FinalVideoCatalog(BaseTool):
    name = "final_video_catalog"
    version = "0.1.0"
    tier = ToolTier.PUBLISH
    capability = "publishing"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []
    capabilities = [
        "index_final_package_manifests",
        "import_legacy_final_archives",
        "create_final_video_catalog_json",
        "create_final_video_catalog_html",
        "link_catalog_entries_to_package_review_pages",
    ]
    best_for = [
        "building a durable library of approved final video deliverables",
        "finding previous final videos by project, channel, date, and tags",
        "connecting many final packages back to their package review pages",
    ]
    not_good_for = [
        "choosing which render variant should be final",
        "packaging a single final deliverable",
        "publishing videos to external platforms",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["build"],
                "default": "build",
            },
            "scan_roots": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Directories to scan for final_package_manifest.json files.",
            },
            "manifests": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Explicit final_package_manifest.json paths to include.",
            },
            "review_roots": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Directories to scan for final_package_review.json files.",
            },
            "include_legacy_final": {
                "type": "boolean",
                "default": False,
                "description": "Also import legacy final folders and FINAL_ARCHIVE.md records.",
            },
            "output_dir": {
                "type": "string",
                "description": "Directory for catalog.json and catalog.html when explicit output paths are omitted.",
            },
            "catalog_path": {"type": "string"},
            "html_path": {"type": "string"},
        },
    }
    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = [
        "scan_roots",
        "manifests",
        "review_roots",
        "include_legacy_final",
        "catalog_path",
        "html_path",
    ]
    side_effects = ["writes final video catalog JSON and HTML"]
    user_visible_verification = [
        "Open catalog.html and confirm the default standard-package filter, date filters, preview playback, and package review links.",
        "Use legacy-source filters to confirm older videos are visible but clearly marked as needing standard packages.",
    ]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        output_dir, catalog_path, html_path = self._output_paths(inputs)
        scan_roots = self._paths(inputs.get("scan_roots")) or [ROOT / "projects"]
        manifests = self._paths(inputs.get("manifests"))
        review_roots = self._paths(inputs.get("review_roots"))
        return {
            "tool": self.name,
            "operation": inputs.get("operation", "build"),
            "status": self.get_status().value,
            "would_execute": True,
            "scan_roots": [str(path) for path in scan_roots],
            "manifests": [str(path) for path in manifests],
            "review_roots": [str(path) for path in review_roots],
            "include_legacy_final": bool(inputs.get("include_legacy_final", False)),
            "would_write": [str(catalog_path), str(html_path)],
            "output_dir": str(output_dir),
        }

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        if inputs.get("operation", "build") != "build":
            return ToolResult(
                success=False,
                error=f"Unsupported operation: {inputs.get('operation')}",
            )
        try:
            output_dir, catalog_path, html_path = self._output_paths(inputs)
            scan_roots = self._paths(inputs.get("scan_roots")) or [ROOT / "projects"]
            explicit_manifests = self._paths(inputs.get("manifests"))
            review_roots = self._paths(inputs.get("review_roots"))
            include_legacy = bool(inputs.get("include_legacy_final", False))

            package_manifests = scan_package_manifests(scan_roots) + explicit_manifests
            legacy_dirs = scan_legacy_final_dirs(scan_roots) if include_legacy else []
            legacy_archives = scan_legacy_archives(scan_roots) if include_legacy else []
            review_pages = scan_package_review_pages(
                scan_roots + [path.parent for path in explicit_manifests],
                review_roots,
            )
            catalog = build_catalog(
                package_manifests,
                legacy_dirs,
                legacy_archives,
                catalog_path,
                review_pages,
            )
            render_html(catalog, html_path)
            return ToolResult(
                success=True,
                data={
                    "catalog_path": str(catalog_path),
                    "html_path": str(html_path),
                    "entry_count": catalog["entry_count"],
                    "output_dir": str(output_dir),
                },
                artifacts=[str(catalog_path), str(html_path)],
                duration_seconds=round(time.time() - start, 2),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_seconds=round(time.time() - start, 2),
            )

    @staticmethod
    def _paths(values: Any) -> list[Path]:
        if not values:
            return []
        if isinstance(values, (str, Path)):
            values = [values]
        return [Path(value).expanduser().resolve() for value in values]

    @staticmethod
    def _output_paths(inputs: dict[str, Any]) -> tuple[Path, Path, Path]:
        output_dir = Path(
            inputs.get("output_dir") or ROOT / "artifacts" / "final-video-catalog"
        ).expanduser().resolve()
        catalog_path = Path(
            inputs.get("catalog_path") or output_dir / "catalog.json"
        ).expanduser().resolve()
        html_path = Path(
            inputs.get("html_path") or output_dir / "catalog.html"
        ).expanduser().resolve()
        return output_dir, catalog_path, html_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan-root",
        action="append",
        default=[],
        help="Directory to scan for final_package_manifest.json files.",
    )
    parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        help="Explicit final_package_manifest.json path to include.",
    )
    parser.add_argument(
        "--review-root",
        action="append",
        default=[],
        help="Directory to scan for final_package_review.json files that link manifests to review pages.",
    )
    parser.add_argument(
        "--include-legacy-final",
        action="store_true",
        help="Also include legacy projects/*/final/*/MANIFEST.md folders and project FINAL_ARCHIVE.md records.",
    )
    parser.add_argument(
        "--catalog",
        default=str(ROOT / "artifacts/final-video-catalog/catalog.json"),
        help="Output catalog JSON path.",
    )
    parser.add_argument(
        "--html",
        default=str(ROOT / "artifacts/final-video-catalog/catalog.html"),
        help="Output catalog HTML path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scan_roots = [Path(path).expanduser().resolve() for path in args.scan_root]
    explicit_manifests = [Path(path).expanduser().resolve() for path in args.manifest]
    review_roots = [Path(path).expanduser().resolve() for path in args.review_root]
    if not scan_roots and not explicit_manifests:
        scan_roots = [ROOT / "projects"]
    package_manifests = scan_package_manifests(scan_roots) + explicit_manifests
    legacy_dirs = scan_legacy_final_dirs(scan_roots) if args.include_legacy_final else []
    legacy_archives = scan_legacy_archives(scan_roots) if args.include_legacy_final else []
    review_pages = scan_package_review_pages(
        scan_roots + [path.parent for path in explicit_manifests],
        review_roots,
    )
    catalog_path = Path(args.catalog).expanduser().resolve()
    html_path = Path(args.html).expanduser().resolve()
    catalog = build_catalog(package_manifests, legacy_dirs, legacy_archives, catalog_path, review_pages)
    render_html(catalog, html_path)
    print(json.dumps({"catalog": str(catalog_path), "html": str(html_path), "entries": catalog["entry_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
