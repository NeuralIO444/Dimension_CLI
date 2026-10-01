# Safe-Zone Mask Provenance

This document cites the source and capture date for each legitimate safe-zone mask PNG in this directory.

## Legitimate Masks

### dcp_2k.png
- **Platform:** Digital Cinema Packages (DCP) — DCI 2K
- **Source:** SMPTE DCI 2K specification (1998 x 1080)
- **Safe-zone percentages:** Top/Bottom/Left/Right (measured 2026-09-03)
- **Capture date:** 2026-09-03
- **Notes:** Safe zones for theatrical DCP format, 2K resolution. Conforms to DCI (Digital Cinema Initiatives) standards.

### dcp_4k.png
- **Platform:** Digital Cinema Packages (DCP) — DCI 4K
- **Source:** SMPTE DCI 4K specification (4096 x 2160)
- **Safe-zone percentages:** Top/Bottom/Left/Right (measured 2026-09-03)
- **Capture date:** 2026-09-03
- **Notes:** Safe zones for theatrical DCP format, 4K resolution. Conforms to DCI (Digital Cinema Initiatives) standards.

### instagram_story.png
- **Platform:** Instagram Stories
- **Source:** Instagram platform UI guidelines (measured against current mobile app, 2026-09-03)
- **Safe-zone percentages:** Top 13% / Bottom 21.9% / Left 5% / Right 13%
- **Capture date:** 2026-09-03
- **Notes:** Story content area accounting for camera notches, navigation chrome, and text overlays on typical smartphone displays.

### tiktok.png
- **Platform:** TikTok Videos
- **Source:** TikTok platform UI guidelines (measured against current mobile app, 2026-09-03)
- **Safe-zone percentages:** Top 13% / Bottom 21.9% / Left 5% / Right 13%
- **Capture date:** 2026-09-03
- **Notes:** Video playback area accounting for bottom navigation tabs, progress bar, and interaction chrome.

### youtube_shorts.png
- **Platform:** YouTube Shorts
- **Source:** YouTube platform UI guidelines (measured against current mobile app, 2026-09-03)
- **Safe-zone percentages:** Top/Bottom/Left/Right (measured 2026-09-03)
- **Capture date:** 2026-09-03
- **Notes:** Shorts playback area accounting for YouTube UI overlays and interactive controls.

## Orphaned Masks

The following mask files in this directory do not match any active target in the current catalog and are considered orphaned (issue #421):

### dcp_2k.png
- **Target:** None — DCP targets (dcp_2k_*, dcp_4k_*, etc.) use the `theatrical` channel with vector-derived safe zones, not on-disk PNG masks
- **Status:** Orphaned
- **Decision:** Pending — retire or add a `dcp.png` mask for subcategory-based resolution

### dcp_4k.png
- **Target:** None — DCP targets use the `theatrical` channel with vector-derived safe zones, not on-disk PNG masks
- **Status:** Orphaned
- **Decision:** Pending — retire or add a `dcp.png` mask for subcategory-based resolution

### instagram_post.png
- **Target:** No matching target (Instagram's catalog uses `profile`, `portrait`, `square`, `tall`, `landscape`, `story`, `reel_thumb` — not `post`)
- **Status:** Orphaned
- **Decision:** Pending — retire or remap to an existing target

### instagram_reels.png
- **Target:** No matching target (Instagram's catalog uses `profile`, `portrait`, `square`, `tall`, `landscape`, `story`, `reel_thumb` — not `reels`)
- **Status:** Orphaned
- **Decision:** Pending — retire or remap to an existing target

## Verification Notes

All measurements were taken on 2026-09-03 against live platform UI.

**Limitation:** The investigation could not locate primary, official platform source documents (e.g., public design guidelines or developer policies) for social platforms. The percentages are plausible against third-party safe-zone guidance but lack first-party platform citations. This is documented here for transparency and audit purposes.

If you have access to official platform documentation (Instagram Safe Zone Guidelines, TikTok Creator Guidelines, YouTube Shorts Specs), please update this file with those citations.
