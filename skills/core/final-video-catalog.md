# Final Video Catalog

## When to Use

Use `final_video_catalog` after one or more videos have been approved and
packaged with Final Package Helper.

It is a library/index tool, not a creative review tool. Use it when you need to
find, preview, and trace finished videos across projects, channels, and
publishing contexts.

It is strongest for:

- production labs with many finished videos;
- teams that package final deliverables with `final_package_manifest.json`;
- projects that need a durable HTML catalog with video previews;
- finding final videos by project, variant, channel, tags, and generation date;
- opening the related Final Package Helper review page from a catalog entry.

Skip it when you are still choosing between render variants. Use
`variant_manager` first. Skip it when you only need to assemble one approved
deliverable. Use `publish_packager` first.

Decision rule: use this tool only for approved or archived final outputs. Draft
renders and rejected candidates should not enter the catalog.

## Tool

| Tool | Capability |
|------|------------|
| `final_video_catalog` | Build `catalog.json` and `catalog.html` from final packages |
| `publish_packager` | Package one approved final video |
| `variant_manager` | Decide which render variant is approved |

## Workflow

1. Approve a candidate with `variant_manager`.
2. Package the approved deliverable with `publish_packager`.
3. Run `final_video_catalog` over the project/output roots that contain
   `final_package_manifest.json` files.
4. Open `catalog.html`.
5. Confirm the default standard-package filter, date filters, video previews,
   and package review links.
6. Optionally enable `include_legacy_final` to bootstrap older projects that
   predate Final Package Helper.

## Minimal Input

```json
{
  "operation": "build",
  "scan_roots": ["projects"],
  "output_dir": "artifacts/final-video-catalog"
}
```

## With Explicit Review Roots

If Final Package Helper review pages live outside the scanned project tree,
include their review directories so catalog entries can link back to the
package review page:

```json
{
  "operation": "build",
  "scan_roots": ["projects"],
  "review_roots": ["projects/my-project/reviews/final-package"],
  "output_dir": "artifacts/final-video-catalog"
}
```

The tool discovers `final_package_review.json`, reads its `manifest_path`, and
links the matching catalog entry to `final_package_review.html`.

## Outputs

- `catalog.json`: machine-readable final video library.
- `catalog.html`: local browser page with search, source filters, date-range
  filters, video preview players, package review links, speed labels, and file
  references.

Speed labels are read from `playback_speed` when the final package manifest
provides it. If it is absent, the catalog infers common derived-version names
such as `1p2x` or `1.2x`; standard package entries fall back to `1.0x`.

The HTML view defaults to standard package entries because those represent
approved deliverables. Legacy records remain available through the source
filter and are marked as needing standard package records.

## Boundaries

- `variant_manager` decides the final candidate for a channel.
- `publish_packager` assembles and verifies one approved package.
- `final_video_catalog` indexes many approved packages into a durable library.

This tool is local-only and deterministic. It does not upload or publish files.
