# Voice Pacing QA

Use `voice_pacing_qa` for narration-led videos when voice speed, pauses, and
scene-to-scene tone must feel steady.

This is a quality gate, not a TTS generator. It reads a rendered MP4 or audio
track plus optional script sections and word-level transcript data, then reports
scene WPM, rolling WPM shifts, long pauses, loudness jumps, and lightweight
pitch changes.

## When To Use

Use it:

- after final or near-final render for executive explainers, screen demos, and
  product walkthroughs;
- after any TTS segment is regenerated, trimmed, sped up, or slowed down;
- before sharing a video feedback review package when narration timing drives
  highlights or subtitles.

Skip it:

- when there is no narration;
- when the output is a rough timing probe;
- when music, not speech, is the timing authority.

## Tool Call

```python
from tools.analysis.voice_pacing_qa import VoicePacingQA

result = VoicePacingQA().execute({
    "input_path": "projects/my-video/renders/final-faststart.mp4",
    "script_path": "projects/my-video/artifacts/script.json",
    "transcript_path": "projects/my-video/artifacts/transcript.json",
    "output_dir": "projects/my-video/artifacts",
    "target_wpm": 128,
    "acceptable_wpm_min": 110,
    "acceptable_wpm_max": 150
})
```

If the rendered timeline removed or inserted time after transcript generation,
pass the same transform used by the composition:

```json
{
  "time_shifts": [
    {"from_seconds": 82, "shift_seconds": -4}
  ]
}
```

## Review Policy

- `passed`: continue with normal technical QA and feedback handoff.
- `passed_with_warnings`: listen to the warned scene or boundary by ear before
  approval. If it sounds abrupt, fix the outlier segment.
- `failed`: fix before final handoff.

For corporate/executive briefing videos, a calm baseline is typically around
`120-140 WPM`. Individual scenes do not need identical speed, but transitions
should not jump sharply unless the script intentionally changes energy.

## If You Change Audio

Changing narration speed or regenerating a segment changes timing authority.
After any such edit:

1. Rebuild or re-transcribe the final narration.
2. Regenerate subtitles from the locked word timings.
3. Regenerate `visual_sync_anchors` for narration-tied highlights.
4. Rerender.
5. Run `voice_pacing_qa`, subtitle checks, visual timing checks, and final
   technical QA again.
