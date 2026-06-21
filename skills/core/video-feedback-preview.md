# Video Feedback Preview

Use `video_feedback_preview` when a reviewer needs to watch an MP4 on phone or
outside the local network and submit precise feedback tied to playback time.

This is a temporary review workflow, not durable public hosting.

## When To Use

Use it for:

- review iterations where the user needs to say "at 00:16 this highlight is
  wrong";
- external phone review through a tunnel;
- collecting overall suggestions and timepoint feedback in the same page.

Skip it for:

- final public publishing;
- identity-based access control requirements;
- large-scale comment moderation.

## Tool Call

```python
from tools.publishers.video_feedback_preview import VideoFeedbackPreview

result = VideoFeedbackPreview().execute({
    "video_path": "projects/my-video/renders/final-faststart.mp4",
    "output_dir": "projects/my-video/renders/mobile-preview",
    "video_label": "my-video-v3-faststart",
    "page_title": "My Video Review",
    "copy_mode": "hardlink",
    "access_key_env": "OPENMONTAGE_VIDEO_PREVIEW_KEY",
    "scenes": [
        {"id": "s1", "label": "Opening", "start": 0, "end": 42},
        {"id": "s2", "label": "System View", "start": 42, "end": 83}
    ]
})
```

The tool writes:

- `index.html`: HTML5 video player plus feedback form;
- `serve_with_key.py`: local key-gated server and feedback API;
- `preview_config.json`: video label, scenes, env var names, port;
- `README.md`: run and tunnel instructions;
- `video.mp4`: hard link, symlink, or copy depending on `copy_mode`.

## Run Locally

```bash
cd projects/my-video/renders/mobile-preview
OPENMONTAGE_VIDEO_PREVIEW_KEY=<shared-key> PORT=8794 python3 serve_with_key.py
```

Open:

```text
http://127.0.0.1:8794/index.html?key=<shared-key>
```

For temporary external access, run a tunnel separately, for example:

```bash
cloudflared tunnel --url http://127.0.0.1:8794
```

## Feedback Storage

By default, feedback is appended to:

```text
feedback/feedback-YYYY-MM-DD.jsonl
```

Each record includes:

- video label;
- current playback time and `MM:SS` label;
- auto-matched scene from `preview_config.json`;
- overall-vs-timepoint scope;
- optional name/contact;
- message;
- sanitized page URL with the access key removed;
- user agent and remote address.

## Verification

Before sharing:

1. Probe the MP4 and prefer a faststart file.
2. Start the server with a non-committed shared key.
3. Confirm keyed `index.html` returns 200.
4. Confirm keyed `video.mp4` returns 200 and expected content length.
5. Confirm no-key `video.mp4` HEAD returns 403.
6. Confirm no-key `POST /feedback` returns 403.
7. Confirm keyed empty feedback returns 400.
8. Submit one valid probe using `FEEDBACK_FILE=/tmp/...` so real feedback data
   is not polluted.

Never commit the shared key, tunnel URL secrets, or probe feedback containing a
real access key.
