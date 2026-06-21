"""Create a keyed video review package with timecoded feedback capture."""

from __future__ import annotations

import json
import os
import shutil
import time
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


class VideoFeedbackPreview(BaseTool):
    name = "video_feedback_preview"
    version = "0.2.0"
    tier = ToolTier.PUBLISH
    capability = "publishing"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []
    install_instructions = "No external runtime is required. Use a tunnel such as cloudflared separately if external access is needed."
    agent_skills: list[str] = []

    capabilities = [
        "standard_video_review_package",
        "keyed_video_feedback_preview",
        "timecoded_reviewer_feedback",
        "local_jsonl_feedback_storage",
        "feedback_summary_export",
        "mobile_review_page",
    ]
    supports = {
        "html5_video": True,
        "shared_key_gate": True,
        "current_time_feedback": True,
        "overall_feedback": True,
        "scene_auto_match": True,
        "local_jsonl_storage": True,
        "feedback_read_api": True,
        "feedback_summary_markdown": True,
        "cloudflare_tunnel_ready": True,
    }
    best_for = [
            "sharing a review MP4 on phone or external network while collecting precise timecoded feedback",
            "iterating narration-led videos with human feedback tied to playback time",
            "standard OpenMontage review handoffs before final publishing",
        ]
    not_good_for = [
        "durable public hosting",
        "identity-based access control",
        "large-scale public comment collection",
    ]

    input_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["prepare", "summarize"], "default": "prepare"},
            "video_path": {"type": "string"},
            "output_dir": {"type": "string"},
            "feedback_path": {"type": "string"},
            "feedback_dir": {"type": "string"},
            "video_label": {"type": "string"},
            "page_title": {"type": "string", "default": "Video Feedback"},
            "video_filename": {"type": "string", "default": "video.mp4"},
            "copy_mode": {"type": "string", "enum": ["hardlink", "copy", "symlink", "none"], "default": "hardlink"},
            "access_key_env": {"type": "string", "default": "OPENMONTAGE_VIDEO_PREVIEW_KEY"},
            "port_env": {"type": "string", "default": "PORT"},
            "default_port": {"type": "integer", "default": 8794},
            "feedback_dir_env": {"type": "string", "default": "FEEDBACK_DIR"},
            "feedback_file_env": {"type": "string", "default": "FEEDBACK_FILE"},
            "summary_filename": {"type": "string", "default": "feedback_summary.md"},
            "records_filename": {"type": "string", "default": "feedback_records.json"},
            "scenes": {"type": "array"},
        },
        "allOf": [
            {
                "if": {"properties": {"operation": {"const": "prepare"}}},
                "then": {"required": ["video_path", "output_dir"]},
            },
            {
                "if": {"properties": {"operation": {"const": "summarize"}}},
                "then": {"anyOf": [{"required": ["feedback_path"]}, {"required": ["feedback_dir"]}]},
            },
        ],
    }
    output_schema = {
        "type": "object",
        "properties": {
            "operation": {"type": "string"},
            "output_dir": {"type": "string"},
            "index_path": {"type": "string"},
            "server_path": {"type": "string"},
            "config_path": {"type": "string"},
            "readme_path": {"type": "string"},
            "summary_script_path": {"type": "string"},
            "served_video_path": {"type": "string"},
            "feedback_dir": {"type": "string"},
            "video_label": {"type": "string"},
            "access_key_env": {"type": "string"},
            "launch_command": {"type": "string"},
            "feedback_json_url": {"type": "string"},
            "feedback_markdown_url": {"type": "string"},
            "summary_command": {"type": "string"},
            "feedback_count": {"type": "integer"},
            "summary_path": {"type": "string"},
            "records_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=20)
    idempotency_key_fields = [
        "operation",
        "video_path",
        "output_dir",
        "video_label",
        "scenes",
        "feedback_path",
        "feedback_dir",
    ]
    side_effects = [
        "writes a local preview page",
        "writes a local keyed feedback server",
        "links or copies the review video into the preview directory",
    ]
    user_visible_verification = [
        "Run python3 serve_with_key.py with the configured access key, then open /index.html?key=...",
        "Submit one temporary feedback record with FEEDBACK_FILE pointing at /tmp before sharing.",
    ]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs.get("operation", "prepare")
        start = time.time()
        try:
            if operation == "prepare":
                result = self._prepare(inputs)
            elif operation == "summarize":
                result = self._summarize(inputs)
            else:
                return ToolResult(success=False, error=f"Unknown operation: {operation}")
        except Exception as exc:
            return ToolResult(success=False, error=f"Video feedback review package failed: {exc}")
        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _prepare(self, inputs: dict[str, Any]) -> ToolResult:
        video_path = Path(str(inputs["video_path"])).expanduser().resolve()
        if not video_path.is_file():
            raise ValueError(f"video_path does not exist: {video_path}")
        output_dir = Path(str(inputs["output_dir"])).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        video_filename = str(inputs.get("video_filename") or "video.mp4")
        video_label = str(inputs.get("video_label") or video_path.stem)
        access_key_env = str(inputs.get("access_key_env") or "OPENMONTAGE_VIDEO_PREVIEW_KEY")
        port_env = str(inputs.get("port_env") or "PORT")
        feedback_dir_env = str(inputs.get("feedback_dir_env") or "FEEDBACK_DIR")
        feedback_file_env = str(inputs.get("feedback_file_env") or "FEEDBACK_FILE")
        default_port = int(inputs.get("default_port") or 8794)
        page_title = str(inputs.get("page_title") or "Video Feedback")
        scenes = self._normalize_scenes(inputs.get("scenes") or [])

        served_video_path = output_dir / video_filename
        self._materialize_video(video_path, served_video_path, str(inputs.get("copy_mode") or "hardlink"))

        config = {
            "version": self.version,
            "tool_name": self.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "video_label": video_label,
            "page_title": page_title,
            "video_filename": video_filename,
            "access_key_env": access_key_env,
            "port_env": port_env,
            "default_port": default_port,
            "feedback_dir_env": feedback_dir_env,
            "feedback_file_env": feedback_file_env,
            "scenes": scenes,
        }
        config_path = output_dir / "preview_config.json"
        index_path = output_dir / "index.html"
        server_path = output_dir / "serve_with_key.py"
        summary_script_path = output_dir / "summarize_feedback.py"
        readme_path = output_dir / "README.md"

        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        index_path.write_text(self._index_html(page_title, video_filename), encoding="utf-8")
        server_path.write_text(self._server_py(), encoding="utf-8")
        summary_script_path.write_text(self._summary_py(), encoding="utf-8")
        readme_path.write_text(
            self._readme(video_label, video_filename, access_key_env, port_env, default_port, self.name),
            encoding="utf-8",
        )

        feedback_dir = output_dir / "feedback"
        data = {
            "operation": "prepare",
            "output_dir": str(output_dir),
            "index_path": str(index_path),
            "server_path": str(server_path),
            "config_path": str(config_path),
            "readme_path": str(readme_path),
            "summary_script_path": str(summary_script_path),
            "served_video_path": str(served_video_path),
            "feedback_dir": str(feedback_dir),
            "video_label": video_label,
            "access_key_env": access_key_env,
            "launch_command": f"{access_key_env}=<shared-key> {port_env}={default_port} python3 serve_with_key.py",
            "feedback_json_url": "/feedback.json?key=<shared-key>",
            "feedback_markdown_url": "/feedback.md?key=<shared-key>",
            "summary_command": "python3 summarize_feedback.py",
        }
        return ToolResult(
            success=True,
            data=data,
            artifacts=[
                str(index_path),
                str(server_path),
                str(summary_script_path),
                str(config_path),
                str(readme_path),
                str(served_video_path),
            ],
        )

    def _summarize(self, inputs: dict[str, Any]) -> ToolResult:
        feedback_paths = self._feedback_paths(inputs)
        records = self._read_feedback_records(feedback_paths)
        markdown = self._feedback_summary_markdown(records)

        artifacts: list[str] = []
        data: dict[str, Any] = {
            "operation": "summarize",
            "feedback_count": len(records),
            "feedback_paths": [str(path) for path in feedback_paths],
            "summary_markdown": markdown,
        }
        output_dir_value = inputs.get("output_dir")
        if output_dir_value:
            output_dir = Path(str(output_dir_value)).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            summary_path = output_dir / str(inputs.get("summary_filename") or "feedback_summary.md")
            records_path = output_dir / str(inputs.get("records_filename") or "feedback_records.json")
            summary_path.write_text(markdown, encoding="utf-8")
            records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            artifacts.extend([str(summary_path), str(records_path)])
            data["output_dir"] = str(output_dir)
            data["summary_path"] = str(summary_path)
            data["records_path"] = str(records_path)

        return ToolResult(success=True, data=data, artifacts=artifacts)

    @staticmethod
    def _feedback_paths(inputs: dict[str, Any]) -> list[Path]:
        paths: list[Path] = []
        if inputs.get("feedback_path"):
            paths.append(Path(str(inputs["feedback_path"])).expanduser().resolve())
        if inputs.get("feedback_dir"):
            feedback_dir = Path(str(inputs["feedback_dir"])).expanduser().resolve()
            paths.extend(sorted(feedback_dir.glob("*.jsonl")))
        existing = [path for path in paths if path.is_file()]
        if not existing:
            raise ValueError("No feedback JSONL files found")
        return existing

    @staticmethod
    def _read_feedback_records(paths: list[Path]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in paths:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL in {path}:{line_number}") from exc
                if isinstance(item, dict):
                    records.append(item)
        records.sort(key=lambda item: (str(item.get("video", "")), item.get("current_time_seconds") is None, item.get("current_time_seconds") or 0))
        return records

    @staticmethod
    def _feedback_summary_markdown(records: list[dict[str, Any]]) -> str:
        lines = ["# OpenMontage Video Feedback", ""]
        if not records:
            lines.extend(["No feedback records found.", ""])
            return "\n".join(lines)
        by_scope: dict[str, int] = {}
        for record in records:
            scope = str(record.get("scope") or "unknown")
            by_scope[scope] = by_scope.get(scope, 0) + 1
        lines.append(f"- Total records: {len(records)}")
        for scope, count in sorted(by_scope.items()):
            lines.append(f"- {scope}: {count}")
        lines.append("")
        for record in records:
            scope = str(record.get("scope") or "unknown")
            video = str(record.get("video") or "video")
            time_label = str(record.get("playback_time_label") or "overall")
            scene = record.get("scene") or {}
            scene_label = scene.get("label") if isinstance(scene, dict) else ""
            message = str(record.get("message") or "").strip()
            heading = f"## {video} - {time_label}"
            if scope == "overall":
                heading = f"## {video} - overall"
            lines.extend([
                heading,
                f"- ID: {record.get('id', '')}",
                f"- Status: {record.get('status', 'open')}",
                f"- Scope: {scope}",
            ])
            if scene_label:
                lines.append(f"- Scene: {scene_label}")
            if record.get("name"):
                lines.append(f"- Reviewer: {record.get('name')}")
            lines.extend(["", message, ""])
        return "\n".join(lines)

    @staticmethod
    def _normalize_scenes(scenes: list[Any]) -> list[dict[str, Any]]:
        normalized = []
        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                continue
            scene_id = str(scene.get("id") or scene.get("scene_id") or f"s{index + 1:02d}")
            label = str(scene.get("label") or scene.get("title") or scene_id)
            start = float(scene.get("start") if scene.get("start") is not None else scene.get("start_seconds", 0.0))
            end_value = scene.get("end") if scene.get("end") is not None else scene.get("end_seconds")
            end = float(end_value) if end_value is not None else start
            normalized.append({"id": scene_id, "label": label, "start": start, "end": end})
        return normalized

    @staticmethod
    def _materialize_video(source: Path, dest: Path, copy_mode: str) -> None:
        if copy_mode == "none":
            return
        dest.unlink(missing_ok=True)
        if copy_mode == "copy":
            shutil.copy2(source, dest)
        elif copy_mode == "symlink":
            dest.symlink_to(source)
        else:
            try:
                os.link(source, dest)
            except OSError:
                shutil.copy2(source, dest)

    @staticmethod
    def _index_html(page_title: str, video_filename: str) -> str:
        title_json = json.dumps(page_title)
        video_json = json.dumps(video_filename)
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{page_title}</title>
  <style>
    :root {{ color-scheme: dark; --bg:#050b16; --panel:#0b1220; --strong:#111827; --line:rgba(148,163,184,.28); --text:#f8fafc; --muted:#94a3b8; --accent:#2dd4bf; --accent2:#14b8a6; --warn:#fbbf24; }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin:0; min-height:100%; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    body {{ padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }}
    main {{ width:min(1120px,100%); margin:0 auto; padding:18px; }}
    .player {{ border:1px solid var(--line); background:#000; }}
    video {{ width:100%; max-height:76svh; aspect-ratio:16/9; display:block; object-fit:contain; background:#000; }}
    .feedback {{ margin-top:14px; border:1px solid var(--line); background:var(--panel); padding:16px; }}
    .head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:14px; }}
    h1 {{ margin:0; font-size:18px; line-height:1.3; letter-spacing:0; }}
    .context {{ margin-top:5px; color:var(--muted); font-size:13px; line-height:1.45; }}
    .time {{ min-width:84px; border:1px solid rgba(45,212,191,.45); background:rgba(20,184,166,.12); color:#ccfbf1; padding:8px 10px; text-align:center; font-variant-numeric:tabular-nums; font-size:15px; font-weight:800; }}
    form {{ display:grid; gap:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
    .field {{ display:grid; gap:6px; }}
    label {{ color:#dbeafe; font-size:13px; font-weight:700; }}
    input, textarea, button {{ font:inherit; }}
    input, textarea {{ width:100%; border:1px solid rgba(148,163,184,.35); border-radius:6px; background:var(--strong); color:var(--text); outline:none; }}
    input {{ min-height:42px; padding:0 11px; }}
    textarea {{ min-height:116px; resize:vertical; padding:11px; line-height:1.45; }}
    input:focus, textarea:focus {{ border-color:var(--accent); box-shadow:0 0 0 3px rgba(45,212,191,.13); }}
    .scope {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
    .scope-option {{ position:relative; display:grid; gap:4px; min-height:76px; border:1px solid rgba(148,163,184,.35); border-radius:6px; background:var(--strong); padding:11px; cursor:pointer; }}
    .scope-option input {{ position:absolute; opacity:0; pointer-events:none; }}
    .scope-option.selected {{ border-color:var(--accent); background:rgba(20,184,166,.12); box-shadow:0 0 0 3px rgba(45,212,191,.08); }}
    .scope-title {{ color:var(--text); font-size:15px; font-weight:800; line-height:1.25; }}
    .scope-detail {{ color:var(--muted); font-size:12px; line-height:1.35; }}
    .actions {{ display:flex; align-items:center; justify-content:space-between; gap:12px; }}
    .submit {{ min-height:42px; border:1px solid var(--accent2); border-radius:6px; background:var(--accent2); color:#042f2e; cursor:pointer; font-weight:800; padding:0 14px; white-space:nowrap; }}
    .submit:disabled {{ cursor:not-allowed; opacity:.6; }}
    .status {{ flex:1; min-height:20px; border:1px solid transparent; border-radius:6px; color:var(--muted); font-size:13px; line-height:1.4; padding:8px 10px; }}
    .status.error {{ background:rgba(245,158,11,.11); border-color:rgba(245,158,11,.38); color:var(--warn); }}
    .status.ok {{ background:rgba(20,184,166,.14); border-color:rgba(45,212,191,.62); color:#99f6e4; font-size:15px; font-weight:800; }}
    @media (max-width:720px) {{ main {{ padding:0 0 16px; }} .player,.feedback {{ border-left:0; border-right:0; }} .grid,.scope {{ grid-template-columns:1fr; }} .head,.actions {{ align-items:stretch; flex-direction:column; }} .submit {{ width:100%; }} video {{ max-height:46svh; }} }}
  </style>
</head>
<body>
  <main>
    <section class="player"><video id="video" src="{video_filename}" controls playsinline preload="metadata"></video></section>
    <section class="feedback">
      <div class="head">
        <div><h1 id="pageTitle"></h1><div class="context" id="context">Current playback time is captured automatically when you submit.</div></div>
        <div class="time" id="timeBadge">00:00</div>
      </div>
      <form id="feedbackForm">
        <div class="field">
          <label>Feedback scope</label>
          <div class="scope" id="scopeToggle">
            <label class="scope-option selected"><input type="radio" name="scope" value="timepoint" checked><span class="scope-title">Current time</span><span class="scope-detail">Attach this note to the current playback time and matching scene.</span></label>
            <label class="scope-option"><input type="radio" name="scope" value="overall"><span class="scope-title">Overall note</span><span class="scope-detail">Use this for general suggestions not tied to one moment.</span></label>
          </div>
        </div>
        <div class="grid">
          <div class="field"><label for="name">Name (optional)</label><input id="name" autocomplete="name" placeholder="For follow-up"></div>
          <div class="field"><label for="contact">Contact (optional)</label><input id="contact" autocomplete="email" placeholder="Email, phone, or handle"></div>
        </div>
        <div class="field"><label for="message">Feedback</label><textarea id="message" required placeholder="Describe what feels wrong or what should change."></textarea></div>
        <div class="actions"><div class="status" id="status" aria-live="polite"></div><button class="submit" id="submit" type="submit">Submit feedback</button></div>
      </form>
    </section>
  </main>
  <script>
    const PAGE_TITLE = {title_json};
    const VIDEO_FILE = {video_json};
    const video = document.getElementById("video");
    const form = document.getElementById("feedbackForm");
    const timeBadge = document.getElementById("timeBadge");
    const context = document.getElementById("context");
    const statusEl = document.getElementById("status");
    const submit = document.getElementById("submit");
    document.getElementById("pageTitle").textContent = PAGE_TITLE;

    function formatTime(seconds) {{
      const whole = Math.max(0, Math.round(Number(seconds) || 0));
      const min = Math.floor(whole / 60);
      const sec = whole % 60;
      return `${{String(min).padStart(2, "0")}}:${{String(sec).padStart(2, "0")}}`;
    }}
    function currentScope() {{
      return new FormData(form).get("scope") || "timepoint";
    }}
    function updateTime() {{
      const label = formatTime(video.currentTime);
      timeBadge.textContent = label;
      context.textContent = currentScope() === "overall"
        ? "This note will be saved as an overall suggestion."
        : `Current playback time ${{label}} will be saved with this feedback.`;
    }}
    function accessKeyFromPage() {{
      const pageKey = new URLSearchParams(window.location.search).get("key");
      if (pageKey) return pageKey;
      try {{
        const videoUrl = new URL(video.currentSrc || video.src, window.location.href);
        return videoUrl.searchParams.get("key") || "";
      }} catch (_err) {{
        return "";
      }}
    }}
    function feedbackEndpoint() {{
      const key = accessKeyFromPage();
      return key ? `/feedback?key=${{encodeURIComponent(key)}}` : "/feedback";
    }}
    function setStatus(kind, text) {{
      statusEl.className = `status ${{kind || ""}}`;
      statusEl.textContent = text || "";
    }}
    document.querySelectorAll(".scope-option").forEach((option) => {{
      option.addEventListener("click", () => {{
        document.querySelectorAll(".scope-option").forEach((item) => item.classList.remove("selected"));
        option.classList.add("selected");
        updateTime();
      }});
    }});
    video.addEventListener("timeupdate", updateTime);
    video.addEventListener("loadedmetadata", updateTime);
    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      const message = document.getElementById("message").value.trim();
      if (!message) {{
        setStatus("error", "Please write feedback before submitting.");
        return;
      }}
      const scope = currentScope();
      submit.disabled = true;
      setStatus("", "Submitting...");
      const payload = {{
        message,
        currentTimeSeconds: scope === "overall" ? null : Number(video.currentTime || 0),
        durationSeconds: Number(video.duration || 0),
        sceneId: scope === "overall" ? "overall" : "auto",
        name: document.getElementById("name").value,
        contact: document.getElementById("contact").value,
        href: window.location.href,
        playbackState: video.ended ? "ended" : (video.paused ? "paused" : "playing")
      }};
      try {{
        const response = await fetch(feedbackEndpoint(), {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(payload)
        }});
        const data = await response.json().catch(() => ({{ ok: false, error: "Invalid response" }}));
        if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${{response.status}}`);
        const savedAt = data.time_label ? ` at ${{data.time_label}}` : "";
        setStatus("ok", `Feedback saved${{savedAt}}. Continue watching or submit another note.`);
        document.getElementById("message").value = "";
      }} catch (error) {{
        setStatus("error", `Submit failed: ${{error.message}}`);
      }} finally {{
        submit.disabled = false;
      }}
    }});
    updateTime();
  </script>
</body>
</html>
"""

    @staticmethod
    def _summary_py() -> str:
        return r'''import argparse
import json
from pathlib import Path


def read_records(feedback_dir: Path) -> list[dict]:
    records = []
    for path in sorted(feedback_dir.glob("*.jsonl")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL in {path}:{line_number}") from exc
            if isinstance(item, dict):
                records.append(item)
    records.sort(key=lambda item: (str(item.get("video", "")), item.get("current_time_seconds") is None, item.get("current_time_seconds") or 0))
    return records


def summary_markdown(records: list[dict]) -> str:
    lines = ["# OpenMontage Video Feedback", ""]
    if not records:
        lines.extend(["No feedback records found.", ""])
        return "\n".join(lines)
    by_scope = {}
    for record in records:
        scope = str(record.get("scope") or "unknown")
        by_scope[scope] = by_scope.get(scope, 0) + 1
    lines.append(f"- Total records: {len(records)}")
    for scope, count in sorted(by_scope.items()):
        lines.append(f"- {scope}: {count}")
    lines.append("")
    for record in records:
        scope = str(record.get("scope") or "unknown")
        video = str(record.get("video") or "video")
        time_label = str(record.get("playback_time_label") or "overall")
        scene = record.get("scene") or {}
        scene_label = scene.get("label") if isinstance(scene, dict) else ""
        message = str(record.get("message") or "").strip()
        heading = f"## {video} - {time_label}"
        if scope == "overall":
            heading = f"## {video} - overall"
        lines.extend([
            heading,
            f"- ID: {record.get('id', '')}",
            f"- Status: {record.get('status', 'open')}",
            f"- Scope: {scope}",
        ])
        if scene_label:
            lines.append(f"- Scene: {scene_label}")
        if record.get("name"):
            lines.append(f"- Reviewer: {record.get('name')}")
        lines.extend(["", message, ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize OpenMontage video feedback JSONL records.")
    parser.add_argument("--feedback-dir", default="feedback")
    parser.add_argument("--summary-path", default="feedback_summary.md")
    parser.add_argument("--records-path", default="feedback_records.json")
    args = parser.parse_args()

    records = read_records(Path(args.feedback_dir))
    Path(args.summary_path).write_text(summary_markdown(records), encoding="utf-8")
    Path(args.records_path).write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(records)} feedback records to {args.summary_path} and {args.records_path}")


if __name__ == "__main__":
    main()
'''

    @staticmethod
    def _server_py() -> str:
        return r'''from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import datetime as dt
import glob
import json
import os
import posixpath
import secrets
import threading
import uuid
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse


CONFIG_PATH = os.environ.get("OPENMONTAGE_VIDEO_PREVIEW_CONFIG", "preview_config.json")
with open(CONFIG_PATH, encoding="utf-8") as config_file:
    CONFIG = json.load(config_file)

ACCESS_KEY_ENV = CONFIG.get("access_key_env", "OPENMONTAGE_VIDEO_PREVIEW_KEY")
ACCESS_KEY = os.environ.get(ACCESS_KEY_ENV, "")
COOKIE_NAME = "openmontage_video_access"
FEEDBACK_LOCK = threading.Lock()
VIDEO_LABEL = CONFIG.get("video_label", "review-video")
SCENES = CONFIG.get("scenes") or []
VIDEO_PATH = "/" + CONFIG.get("video_filename", "video.mp4").lstrip("/")
ALLOWED_FILES = {VIDEO_PATH}


class KeyedVideoFeedbackHandler(SimpleHTTPRequestHandler):
    server_version = "OpenMontageVideoFeedbackReviewPackage/0.2"

    def _request_key(self):
        parsed = urlparse(self.path)
        query_key = parse_qs(parsed.query).get("key", [""])[0]
        if query_key:
            return query_key
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return value
        return ""

    def _authorized(self):
        return bool(ACCESS_KEY) and secrets.compare_digest(self._request_key(), ACCESS_KEY)

    def _normalized_path(self):
        parsed = urlparse(self.path)
        return posixpath.normpath(unquote(parsed.path or "/"))

    def _send_gate(self):
        body = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video Access</title><style>
html,body{margin:0;min-height:100%;background:#050b16;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
body{min-height:100svh;display:grid;place-items:center;padding:24px}
form{width:min(420px,100%);display:grid;gap:14px}
label{font-size:15px;color:#cbd5e1}
input,button{min-height:44px;border-radius:8px;border:1px solid rgba(148,163,184,.45);background:rgba(15,23,42,.86);color:#f8fafc;font-size:16px;padding:0 12px}
button{cursor:pointer;font-weight:700;background:#0f766e;border-color:#14b8a6}
</style></head><body><form method="get" action="/index.html">
<label for="key">Access key</label><input id="key" name="key" type="password" autocomplete="off" autofocus>
<button type="submit">Open video</button></form></body></html>"""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_index(self):
        video_name = CONFIG.get("video_filename", "video.mp4").encode("utf-8")
        keyed = (CONFIG.get("video_filename", "video.mp4") + "?key=" + ACCESS_KEY).encode("utf-8")
        with open("index.html", "rb") as file:
            body = file.read().replace(b'src="' + video_name + b'"', b'src="' + keyed + b'"')
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", f"{COOKIE_NAME}={ACCESS_KEY}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            self._send_gate()
            return
        path = self._normalized_path()
        if path in ("", "/", "/index.html"):
            self._send_index()
            return
        if path == "/feedback.json":
            records = self._read_feedback_records()
            self._send_json({"ok": True, "count": len(records), "records": records})
            return
        if path == "/feedback.md":
            records = self._read_feedback_records()
            self._send_text(self._feedback_summary_markdown(records), "text/markdown; charset=utf-8")
            return
        if path in ALLOWED_FILES:
            super().do_GET()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self):
        if not self._authorized():
            self.send_response(HTTPStatus.FORBIDDEN)
            self.end_headers()
            return
        path = self._normalized_path()
        if path in ("", "/", "/index.html"):
            self._send_index()
            return
        if path in ALLOWED_FILES:
            super().do_HEAD()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self._authorized():
            self._send_json({"ok": False, "error": "Authentication required"}, HTTPStatus.FORBIDDEN)
            return
        if self._normalized_path() != "/feedback":
            self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body(max_bytes=20000)
            record = self._build_feedback_record(payload)
            path = self._append_feedback_record(record)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception:
            self._send_json({"ok": False, "error": "Failed to save feedback"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({
            "ok": True,
            "id": record["id"],
            "status": record["status"],
            "scene": record["scene"],
            "time_label": record["playback_time_label"],
            "saved_to": os.path.basename(path),
        })

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def _read_json_body(self, max_bytes):
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if content_length <= 0:
            raise ValueError("Empty feedback")
        if content_length > max_bytes:
            raise ValueError("Feedback is too large")
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError("Invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Invalid feedback payload")
        return payload

    def _build_feedback_record(self, payload):
        message = self._clean_text(payload.get("message", ""), 4000)
        if not message:
            raise ValueError("Feedback message is required")
        current_time = self._clean_seconds(payload.get("currentTimeSeconds"))
        duration_seconds = self._clean_seconds(payload.get("durationSeconds"))
        manual_scene_id = self._clean_text(payload.get("sceneId", "auto"), 80) or "auto"
        is_overall = manual_scene_id == "overall"
        auto_scene = None if is_overall else self._scene_for_time(current_time)
        selected_scene = self._selected_scene(manual_scene_id, auto_scene)
        now = dt.datetime.now(dt.timezone.utc).astimezone()
        return {
            "id": uuid.uuid4().hex[:12],
            "created_at": now.isoformat(timespec="seconds"),
            "status": "open",
            "decision": "pending_review",
            "video": VIDEO_LABEL,
            "scope": "overall" if is_overall else "timepoint",
            "category": "pending_triage",
            "scene": selected_scene,
            "auto_scene": auto_scene,
            "manual_scene_id": manual_scene_id,
            "current_time_seconds": None if is_overall else current_time,
            "playback_time_label": "" if is_overall else self._format_time(current_time),
            "duration_seconds": duration_seconds,
            "name": self._clean_text(payload.get("name", ""), 100),
            "contact": self._clean_text(payload.get("contact", ""), 140),
            "message": message,
            "page": self._sanitize_href(payload.get("href", "")),
            "playback_state": self._clean_choice(payload.get("playbackState"), {"playing", "paused", "ended"}, "paused"),
            "user_agent": self.headers.get("User-Agent", "")[:300],
            "remote_addr": self._remote_addr(),
        }

    def _append_feedback_record(self, record):
        feedback_path = self._feedback_storage_path()
        os.makedirs(os.path.dirname(feedback_path) or ".", exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with FEEDBACK_LOCK:
            with open(feedback_path, "a", encoding="utf-8") as feedback_file:
                feedback_file.write(line + "\n")
        return feedback_path

    def _feedback_storage_path(self):
        feedback_path = os.environ.get(CONFIG.get("feedback_file_env", "FEEDBACK_FILE"))
        if feedback_path:
            return feedback_path
        feedback_dir = os.environ.get(CONFIG.get("feedback_dir_env", "FEEDBACK_DIR"), os.path.join(os.getcwd(), "feedback"))
        date_part = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d")
        return os.path.join(feedback_dir, f"feedback-{date_part}.jsonl")

    def _feedback_paths(self):
        feedback_path = os.environ.get(CONFIG.get("feedback_file_env", "FEEDBACK_FILE"))
        if feedback_path:
            return [feedback_path] if os.path.isfile(feedback_path) else []
        feedback_dir = os.environ.get(CONFIG.get("feedback_dir_env", "FEEDBACK_DIR"), os.path.join(os.getcwd(), "feedback"))
        return sorted(path for path in glob.glob(os.path.join(feedback_dir, "*.jsonl")) if os.path.isfile(path))

    def _read_feedback_records(self):
        records = []
        for path in self._feedback_paths():
            with open(path, encoding="utf-8") as feedback_file:
                for line in feedback_file:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        records.append(item)
        records.sort(key=lambda item: (str(item.get("video", "")), item.get("current_time_seconds") is None, item.get("current_time_seconds") or 0))
        return records

    def _feedback_summary_markdown(self, records):
        lines = ["# OpenMontage Video Feedback", ""]
        if not records:
            lines.extend(["No feedback records found.", ""])
            return "\n".join(lines)
        by_scope = {}
        for record in records:
            scope = str(record.get("scope") or "unknown")
            by_scope[scope] = by_scope.get(scope, 0) + 1
        lines.append(f"- Total records: {len(records)}")
        for scope, count in sorted(by_scope.items()):
            lines.append(f"- {scope}: {count}")
        lines.append("")
        for record in records:
            scope = str(record.get("scope") or "unknown")
            video = str(record.get("video") or "video")
            time_label = str(record.get("playback_time_label") or "overall")
            scene = record.get("scene") or {}
            scene_label = scene.get("label") if isinstance(scene, dict) else ""
            message = str(record.get("message") or "").strip()
            heading = f"## {video} - {time_label}"
            if scope == "overall":
                heading = f"## {video} - overall"
            lines.extend([
                heading,
                f"- ID: {record.get('id', '')}",
                f"- Status: {record.get('status', 'open')}",
                f"- Scope: {scope}",
            ])
            if scene_label:
                lines.append(f"- Scene: {scene_label}")
            if record.get("name"):
                lines.append(f"- Reviewer: {record.get('name')}")
            lines.extend(["", message, ""])
        return "\n".join(lines)

    def _send_json(self, body, status=HTTPStatus.OK):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, body, content_type, status=HTTPStatus.OK):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _scene_for_time(self, seconds):
        if seconds is None:
            return None
        for scene in SCENES:
            if float(scene.get("start", 0)) <= seconds < float(scene.get("end", 0)):
                return {
                    "id": scene.get("id"),
                    "label": scene.get("label"),
                    "start_seconds": scene.get("start"),
                    "end_seconds": scene.get("end"),
                }
        if SCENES and seconds >= float(SCENES[-1].get("end", 0)):
            scene = SCENES[-1]
            return {
                "id": scene.get("id"),
                "label": scene.get("label"),
                "start_seconds": scene.get("start"),
                "end_seconds": scene.get("end"),
            }
        return None

    def _selected_scene(self, scene_id, auto_scene):
        if scene_id == "overall":
            return None
        if scene_id == "auto":
            return auto_scene
        for scene in SCENES:
            if scene.get("id") == scene_id:
                return {
                    "id": scene.get("id"),
                    "label": scene.get("label"),
                    "start_seconds": scene.get("start"),
                    "end_seconds": scene.get("end"),
                }
        return auto_scene

    def _sanitize_href(self, value):
        text = self._clean_text(value, 500)
        if not text:
            return ""
        parsed = urlparse(text)
        query = urlencode([
            (key, item_value)
            for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() != "key"
        ])
        sanitized = parsed._replace(query=query).geturl()
        return sanitized.replace(ACCESS_KEY, "[redacted]") if ACCESS_KEY else sanitized

    def _remote_addr(self):
        forwarded_for = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()[:80]
        return self.client_address[0] if self.client_address else ""

    @staticmethod
    def _format_time(seconds):
        if seconds is None:
            return ""
        whole = max(0, int(round(seconds)))
        minutes, sec = divmod(whole, 60)
        return f"{minutes:02d}:{sec:02d}"

    @staticmethod
    def _clean_text(value, max_length):
        if value is None:
            return ""
        value = str(value).replace("\x00", "").strip()
        return value[:max_length]

    def _clean_choice(self, value, allowed, default):
        value = self._clean_text(value, 60)
        return value if value in allowed else default

    @staticmethod
    def _clean_seconds(value):
        if value in ("", None):
            return None
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return None
        return round(max(0.0, seconds), 3)


if __name__ == "__main__":
    if not ACCESS_KEY:
        raise SystemExit(f"{ACCESS_KEY_ENV} must be set")
    port = int(os.environ.get(CONFIG.get("port_env", "PORT"), str(CONFIG.get("default_port", 8794))))
    server = ThreadingHTTPServer(("127.0.0.1", port), KeyedVideoFeedbackHandler)
    server.serve_forever()
'''

    @staticmethod
    def _readme(
        video_label: str,
        video_filename: str,
        access_key_env: str,
        port_env: str,
        default_port: int,
        tool_name: str,
    ) -> str:
        return f"""# OpenMontage Video Feedback Review Package

This directory was generated by `{tool_name}` for `{video_label}`.

## Run Locally

```bash
cd this-directory
{access_key_env}=<shared-review-key> {port_env}={default_port} python3 serve_with_key.py
```

Open:

```text
http://127.0.0.1:{default_port}/index.html?key=<shared-review-key>
```

The page serves `{video_filename}` through a keyed HTML5 video player and stores
feedback as local JSONL under `feedback/feedback-YYYY-MM-DD.jsonl` by default.

Read collected feedback:

```text
http://127.0.0.1:{default_port}/feedback.json?key=<shared-review-key>
http://127.0.0.1:{default_port}/feedback.md?key=<shared-review-key>
```

Or summarize the local JSONL files after review:

```bash
python3 summarize_feedback.py
```

For a safe submit test without polluting review data:

```bash
FEEDBACK_FILE=/tmp/openmontage-feedback-probe.jsonl \\
  {access_key_env}=<shared-review-key> {port_env}={default_port} python3 serve_with_key.py
```

Use a tunnel such as `cloudflared tunnel --url http://127.0.0.1:{default_port}`
when temporary external phone access is needed. The shared key is read from the
environment and is never written by this tool.
"""


class VideoFeedbackReviewPackage(VideoFeedbackPreview):
    """Canonical standard OpenMontage video review feedback package tool."""

    name = "video_feedback_review_package"
