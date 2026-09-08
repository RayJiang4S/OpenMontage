# Publish Director - Character Animation Pipeline

## Goal

Package the final character-animation deliverable with honest metadata and a
strong character-forward thumbnail concept.

## Requirements

- Mention the actual visual treatment: local rigged character animation,
  procedural effects, Remotion/HyperFrames render, or mixed.
- Pick a poster frame where the main character's emotion is readable.
- If the output is a sample, label it as a sample.
- If the final is inspired by a reference, describe the inspiration without
  claiming duplication.

## Output

Produce `publish_log` with:

- final video path,
- thumbnail/poster-frame notes,
- title ideas,
- description,
- platform-specific export notes,
- limitations or follow-up recommendations.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.

## Local production packaging

When packaging a finished Chinese explainer or similar deliverable, use
`publish_packager` if available instead of copying files by hand. It should
write `final_package_manifest.json` and keep cover/first-frame handling
from `script.cover_policy`.

For iterative human review before final publishing, read
`skills/core/video-feedback-review-package.md` and use
`video_feedback_review_package` for the keyed local review page. Run Visual
Timing QA for narration-led motion scenes before treating a package as final.
