# Publish Director - Documentary Montage Pipeline

## When To Use

Package the finished documentary montage with source provenance, delivery notes,
and a standard OpenMontage video feedback review package.

## Process

### 1. Preserve Source Truth

Keep provenance close to the final output:

- clip provider,
- original URL,
- license or usage note,
- picked slot or narrative beat,
- any rejected or substituted source notes that affect interpretation.

### 2. Label The Hero Export

Make the hero export obvious. If there are alternates, label them by purpose:

- hero cut,
- music-only or narration variant,
- social cutdown,
- review encode.

### 3. Create The Standard Review Feedback Package

Read `skills/core/video-feedback-review-package.md` and use
`video_feedback_review_package` for the approved review encode unless the user
explicitly opts out. The package should include:

- keyed HTML5 video playback,
- current-time feedback capture,
- overall feedback capture,
- local JSONL feedback storage,
- feedback summary/export support.

Record the review package path and any tunnel handoff note in `publish_log`.

### 4. Quality Gate

- source/license context stays attached,
- hero export and alternates are clearly named,
- review package is present or opt-out is explicit,
- feedback can be summarized from local JSONL after review.

## Common Pitfalls

- Shipping a montage without source provenance.
- Treating the review package as public hosting.
- Dropping feedback JSONL files from the project archive.
