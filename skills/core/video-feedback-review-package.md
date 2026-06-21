# Video Feedback Review Package

Use `video_feedback_review_package` as the standard OpenMontage handoff when a
reviewer should watch a rendered MP4 and submit feedback against the actual
playback timeline.

This is a standard review capability, not public hosting. It uses a local keyed
server, optional temporary tunnel, and local JSONL feedback storage.

## Default Policy

For user-reviewed videos, publish stages should prepare a review package unless
the user explicitly opts out or the output is only an internal probe. This is
preferred over detached frame sheets for normal human review because feedback is
captured at the playback time where the issue is observed.

Do not model this as an extra pipeline stage. It is the default human-review
sidecar produced inside the publish stage after the MP4 exists and automated
technical QA has passed. It should replace routine manual frame-sheet review;
use `visual_timing_qa` only for specific high-risk cues that still need
before/at/after frame inspection.

Use it for:

- "at 00:16 this highlight is late";
- mobile or external-network review through a tunnel;
- collecting overall suggestions and timepoint feedback in one page;
- preserving feedback records for the next revision pass.

Skip it for:

- durable public publishing;
- identity-based access control requirements;
- large-scale public comment moderation.

## Tool Call

```python
from tools.publishers.video_feedback_preview import VideoFeedbackReviewPackage

result = VideoFeedbackReviewPackage().execute({
    "video_path": "projects/my-video/renders/final-faststart.mp4",
    "output_dir": "projects/my-video/renders/review-package",
    "video_label": "my-video-v3-faststart",
    "page_title": "My Video Review",
    "copy_mode": "hardlink",
    "access_key_env": "OPENMONTAGE_VIDEO_REVIEW_KEY",
    "scenes": [
        {"id": "s1", "label": "Opening", "start": 0, "end": 42},
        {"id": "s2", "label": "System View", "start": 42, "end": 83}
    ]
})
```

The tool writes:

- `index.html`: HTML5 video player plus feedback form;
- `serve_with_key.py`: local key-gated server and feedback API;
- `summarize_feedback.py`: standalone JSONL-to-Markdown/JSON summary script;
- `preview_config.json`: video label, scenes, env var names, port;
- `README.md`: run, read, summarize, and tunnel instructions;
- `video.mp4`: hard link, symlink, or copy depending on `copy_mode`.

## Run Locally

```bash
cd projects/my-video/renders/review-package
OPENMONTAGE_VIDEO_REVIEW_KEY=<shared-key> PORT=8794 python3 serve_with_key.py
```

Open:

```text
http://127.0.0.1:8794/index.html?key=<shared-key>
```

For temporary external access, run a tunnel separately:

```bash
cloudflared tunnel --url http://127.0.0.1:8794
```

## Feedback Storage And Reading

By default, feedback is appended to:

```text
feedback/feedback-YYYY-MM-DD.jsonl
```

While the server is running, read collected feedback through:

```text
/feedback.json?key=<shared-key>
/feedback.md?key=<shared-key>
```

After review, summarize local files:

```bash
python3 summarize_feedback.py
```

Or use the tool operation directly:

```python
from tools.publishers.video_feedback_preview import VideoFeedbackReviewPackage

result = VideoFeedbackReviewPackage().execute({
    "operation": "summarize",
    "feedback_dir": "projects/my-video/renders/review-package/feedback",
    "output_dir": "projects/my-video/review-notes"
})
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
9. Confirm `/feedback.md?key=<shared-key>` shows the probe.

Never commit the shared key, tunnel URL secrets, or probe feedback containing a
real access key.
