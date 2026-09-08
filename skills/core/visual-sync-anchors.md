# Visual Sync Anchors

Use `visual_sync_anchors` before rendering narration-led Remotion videos when
screen elements must reveal, highlight, or move exactly with spoken words.

This is the preferred fix for recurring "the highlight is slightly early/late"
feedback. Do not keep hand-tuning scattered literal seconds in composition code
when a word-level transcript exists.

## When To Use

Use this tool for:

- card or row highlights tied to spoken labels;
- process-node reveals tied to narration phrases;
- product-screen focus boxes tied to specific spoken states;
- executive explainers, screen demos, and animated UI walkthroughs with final
  TTS/narration audio.

Skip it when:

- there is no narration;
- the transcript lacks word-level timestamps;
- the visual timing is music-led or intentionally abstract.

## Required Inputs

1. A word-level transcript from `transcriber`, faster-whisper, or WhisperX.
2. A reviewed list of visual cues:

```json
[
  {
    "id": "management-processes",
    "group": "opening",
    "key": "managementProcesses",
    "anchor_text": "management processes",
    "expected_state": "Management processes row starts highlighting."
  }
]
```

Use `anchor_text` for the exact spoken phrase. Keep it short and literal.
Prefer the first content word that should trigger the visual change.

## Tool Call

```python
from tools.analysis.visual_sync_anchors import VisualSyncAnchors

result = VisualSyncAnchors().execute({
    "transcript_path": "projects/my-video/artifacts/transcript.json",
    "visual_cues": visual_cues,
    "output_dir": "projects/my-video/artifacts/visual-sync",
    "output_ts_path": "projects/my-video/remotion/visualSync.ts",
    "policy": "onset_at_word_start"
})
```

Outputs:

- `visual_sync_anchors.json`: source record, cue matches, drift findings;
- optional `visualSync.ts`: Remotion-ready `visualSync` object plus
  `activeIndexFromTimes()`.

## Timing Policy

Default policy:

- `onset_at_word_start`: animation starts at the spoken word start. This avoids
  visual highlights appearing before the narration reaches the phrase.

Alternative:

- `stable_at_word_start`: trigger time is moved earlier by
  `stable_lead_seconds`, so the element is fully stable when the word begins.
  Use only when the approved style wants the visual state already settled at the
  exact word.

Record the chosen policy in `render_report.metadata.visual_sync_policy`.

## Remotion Usage

Import the generated table:

```ts
import { visualSync, activeIndexFromTimes } from "./visualSync";
```

Then use anchor values instead of literals:

```ts
const signalTimes = [
  visualSync.opening.finance,
  visualSync.opening.businessSystems,
  visualSync.opening.manualSubmissions,
  visualSync.opening.managementProcesses,
];

const active = activeIndexFromTimes(seconds, signalTimes);
```

Do not introduce new reveal literals such as `at={16}` or
`seconds > 16` for narration-tied events. If a phrase needs retiming, update
the cue or transcript anchor and regenerate the table.

## QA

After rendering:

1. Run `visual_timing_qa` on the same cue list or high-risk subset.
2. Inspect before/at/after frames.
3. If timing is wrong, fix the anchor/cue once and regenerate, rather than
   editing many component-local numbers.
