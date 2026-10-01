# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Built-in target catalog — the complete library of resolution + aspect-ratio
entries that ship with Dimension.

This file is the authoritative source. Customs and favorites live elsewhere
(in the user's TargetStore on disk), but every entry here is hardcoded,
versioned with the app, and never written to disk.

Layout:
  • DIGITAL_CINEMA  → DCP standards (6 entries) + K-tier ladder (2K..8K)
  • UHD_BROADCAST   → 4K UHD, 8K UHD, 1080p, 720p — full aspect ladders
  • SOCIAL          → 7 platforms × ~7 sizes each

ID convention: `builtin:<short_slug>`. Slugs use the resolution tier first
so duplicate aspect ratios across tiers don't collide. Example:
  builtin:dc_4k_185         (Digital Cinema 4K, 1.85)
  builtin:uhd_4k_169        (4K UHD, 16:9)
  builtin:ig_post_square    (Instagram square post)
"""

import json
import os
from typing import List, Optional
from models.target import Target



# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _t(
    id_slug: str,
    label: str,
    category: str,
    subcategory: str,
    width: int,
    height: int,
    aspect_label: str,
    aspect_ratio: Optional[float] = None,
    channel: Optional[str] = None,
) -> Target:
    """Tight wrapper around Target.make so each catalog row stays a one-liner."""
    return Target.make(
        id=f"builtin:{id_slug}",
        label=label,
        category=category,
        subcategory=subcategory,
        width=width,
        height=height,
        aspect_label=aspect_label,
        aspect_ratio=aspect_ratio,
        source="builtin",
        channel=channel,
    )


def _k_tier_ladder(
    tier_label: str,         # "8K", "6K", "4K"...
    tier_slug: str,          # "8k", "6k", "4k"...
    rows: list,              # list of (aspect_label, aspect_ratio, width, height)
    channel: Optional[str] = None,
) -> List[Target]:
    """Expand a Digital Cinema K-tier into 13 catalog rows.
    The aspect ladder is the same shape for every K-tier — only the locked
    width changes — so the call sites stay flat."""
    out = []
    for aspect_label, aspect_ratio, w, h in rows:
        # Slugify aspect for the id: "0.80 (4:5)" → "0_80", "1.78 (16:9)" → "1_78"
        slug_aspect = aspect_label.split(" ")[0].replace(".", "_")
        out.append(_t(
            id_slug=f"dc_{tier_slug}_{slug_aspect}",
            label=f"Digital Cinema {tier_label} · {aspect_label}",
            category="digital_cinema",
            subcategory=f"k_tier_{tier_slug}",
            width=w,
            height=h,
            aspect_label=aspect_label,
            aspect_ratio=aspect_ratio,
            channel=channel,
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────
# DIGITAL CINEMA — DCP standards (pinned at top of drawer)
# ─────────────────────────────────────────────────────────────────────────

DCP_STANDARDS: List[Target] = [
    _t("dcp_4k_flat",  "DCP 4K Flat (1.85)",         "digital_cinema", "dcp", 3996, 2160, "Flat (1.85)", 1.85, channel="theatrical"),
    _t("dcp_4k_scope", "DCP 4K Scope (2.39)",        "digital_cinema", "dcp", 4096, 1716, "Scope (2.39)", 2.39, channel="theatrical"),
    _t("dcp_4k_full",  "DCP 4K Full Container",      "digital_cinema", "dcp", 4096, 2160, "Full (1.90)", 1.90, channel="theatrical"),
    _t("dcp_2k_flat",  "DCP 2K Flat (1.85)",         "digital_cinema", "dcp", 1998, 1080, "Flat (1.85)", 1.85, channel="theatrical"),
    _t("dcp_2k_scope", "DCP 2K Scope (2.39)",        "digital_cinema", "dcp", 2048,  858, "Scope (2.39)", 2.39, channel="theatrical"),
    _t("dcp_2k_full",  "DCP 2K Full Container",      "digital_cinema", "dcp", 2048, 1080, "Full (1.90)", 1.90, channel="theatrical"),
]


# ─────────────────────────────────────────────────────────────────────────
# DIGITAL CINEMA — K-tier ladders
# Source: standard 13-row aspect ladder per tier (0.80, 1.25, 1.33, 1.66,
# 1.78, 1.85, 1.90, 2.00, 2.35, 2.37, 2.39, 2.40, 2.44).
# ─────────────────────────────────────────────────────────────────────────

_LADDER_8K = [
    ("0.80 (4:5)",  0.80, 3686, 4608),  # 8K source data shows portrait at this row
    ("1.25 (5:4)",  1.25, 5760, 4608),
    ("1.33 (4:3)",  1.33, 6144, 4608),
    ("1.66 (5:3)",  1.66, 7680, 4608),
    ("1.78 (16:9)", 1.78, 8192, 4608),
    ("1.85",        1.85, 8192, 4428),
    ("1.90",        1.90, 8192, 4320),
    ("2.00",        2.00, 8192, 4096),
    ("2.35",        2.35, 8192, 3486),
    ("2.37",        2.37, 8192, 3456),
    ("2.39",        2.39, 8192, 3428),
    ("2.40",        2.40, 8192, 3414),
    ("2.44",        2.44, 8192, 3356),
]

_LADDER_6K = [
    ("0.80 (4:5)",  0.80, 2764, 3456),
    ("1.25 (5:4)",  1.25, 4320, 3456),
    ("1.33 (4:3)",  1.33, 4608, 3456),
    ("1.66 (5:3)",  1.66, 5760, 3456),
    ("1.78 (16:9)", 1.78, 6144, 3456),
    ("1.85",        1.85, 6144, 3320),
    ("1.90",        1.90, 6144, 3234),
    ("2.00",        2.00, 6144, 3072),
    ("2.35",        2.35, 6144, 2614),
    ("2.37",        2.37, 6144, 2592),
    ("2.39",        2.39, 6144, 2570),
    ("2.40",        2.40, 6144, 2560),
    ("2.44",        2.44, 6144, 2518),
]

_LADDER_5K = [
    ("0.80 (4:5)",  0.80, 2304, 2880),
    ("1.25 (5:4)",  1.25, 3600, 2880),
    ("1.33 (4:3)",  1.33, 3840, 2880),
    ("1.66 (5:3)",  1.66, 4800, 2880),
    ("1.78 (16:9)", 1.78, 5120, 2880),
    ("1.85",        1.85, 5120, 2768),
    ("1.90",        1.90, 5120, 2700),
    ("2.00",        2.00, 5120, 2560),
    ("2.35",        2.35, 5120, 2178),
    ("2.37",        2.37, 5120, 2160),
    ("2.39",        2.39, 5120, 2142),
    ("2.40",        2.40, 5120, 2136),
    ("2.44",        2.44, 5120, 2098),
]

_LADDER_4K = [
    ("0.80 (4:5)",  0.80, 1842, 2304),
    ("1.25 (5:4)",  1.25, 2880, 2304),
    ("1.33 (4:3)",  1.33, 3072, 2304),
    ("1.66 (5:3)",  1.66, 3840, 2304),
    ("1.78 (16:9)", 1.78, 4096, 2304),
    ("1.85",        1.85, 4096, 2214),
    ("1.90",        1.90, 4096, 2160),
    ("2.00",        2.00, 4096, 2048),
    ("2.35",        2.35, 4096, 1742),
    ("2.37",        2.37, 4096, 1728),
    ("2.39",        2.39, 4096, 1716),
    ("2.40",        2.40, 4096, 1706),
    ("2.44",        2.44, 4096, 1678),
]

_LADDER_3K = [
    ("0.80 (4:5)",  0.80, 1382, 1728),
    ("1.25 (5:4)",  1.25, 2160, 1728),
    ("1.33 (4:3)",  1.33, 2304, 1728),
    ("1.66 (5:3)",  1.66, 2880, 1728),
    ("1.78 (16:9)", 1.78, 3072, 1728),
    ("1.85",        1.85, 3072, 1660),
    ("1.90",        1.90, 3072, 1620),
    ("2.00",        2.00, 3072, 1536),
    ("2.35",        2.35, 3072, 1306),
    ("2.37",        2.37, 3072, 1296),
    ("2.39",        2.39, 3072, 1284),
    ("2.40",        2.40, 3072, 1280),
    ("2.44",        2.44, 3072, 1259),
]

_LADDER_2K = [
    ("0.80 (4:5)",  0.80,  922, 1152),
    ("1.25 (5:4)",  1.25, 1440, 1152),
    ("1.33 (4:3)",  1.33, 1536, 1152),
    ("1.66 (5:3)",  1.66, 1920, 1152),
    ("1.78 (16:9)", 1.78, 2048, 1152),
    ("1.85",        1.85, 2048, 1106),
    ("1.90",        1.90, 2048, 1078),
    ("2.00",        2.00, 2048, 1024),
    ("2.35",        2.35, 2048,  870),
    ("2.37",        2.37, 2048,  864),
    ("2.39",        2.39, 2048,  858),
    ("2.40",        2.40, 2048,  852),
    ("2.44",        2.44, 2048,  838),
]

DIGITAL_CINEMA: List[Target] = (
    DCP_STANDARDS
    + _k_tier_ladder("8K", "8k", _LADDER_8K, channel="theatrical")
    + _k_tier_ladder("6K", "6k", _LADDER_6K, channel="theatrical")
    + _k_tier_ladder("5K", "5k", _LADDER_5K, channel="theatrical")
    + _k_tier_ladder("4K", "4k", _LADDER_4K, channel="theatrical")
    + _k_tier_ladder("3K", "3k", _LADDER_3K, channel="theatrical")
    + _k_tier_ladder("2K", "2k", _LADDER_2K, channel="theatrical")
)


# ─────────────────────────────────────────────────────────────────────────
# UHD / BROADCAST
# 8K UHD, 4K UHD, 3K UHD, 1080p, 720p — same 13-row aspect ladder as the
# K-tiers, but width-locked to broadcast standards.
# ─────────────────────────────────────────────────────────────────────────

def _uhd_ladder(tier_label: str, tier_slug: str, rows: list) -> List[Target]:
    out = []
    for aspect_label, aspect_ratio, w, h in rows:
        slug_aspect = aspect_label.split(" ")[0].replace(".", "_")
        out.append(_t(
            id_slug=f"uhd_{tier_slug}_{slug_aspect}",
            label=f"{tier_label} · {aspect_label}",
            category="uhd_broadcast",
            subcategory=tier_slug,
            width=w,
            height=h,
            aspect_label=aspect_label,
            aspect_ratio=aspect_ratio,
        ))
    return out


_LADDER_8K_UHD = [
    ("0.80 (4:5)",  0.80, 2765, 3456),
    ("1.25 (5:4)",  1.25, 5400, 3456),
    ("1.33 (4:3)",  1.33, 5760, 3456),
    ("1.66 (5:3)",  1.66, 7200, 3456),
    ("1.78 (16:9)", 1.78, 7680, 4320),
    ("1.85",        1.85, 7680, 4150),
    ("1.90",        1.90, 7680, 4042),
    ("2.00",        2.00, 7680, 3840),
    ("2.35",        2.35, 7680, 3268),
    ("2.37",        2.37, 7680, 3240),
    ("2.39",        2.39, 7680, 3214),
    ("2.40",        2.40, 7680, 3200),
    ("2.44",        2.44, 7680, 3148),
]

_LADDER_4K_UHD = [
    ("0.80 (4:5)",  0.80, 1728, 2160),
    ("1.25 (5:4)",  1.25, 2700, 2160),
    ("1.33 (4:3)",  1.33, 2880, 2160),
    ("1.66 (5:3)",  1.66, 3600, 2160),
    ("1.78 (16:9)", 1.78, 3840, 2160),
    ("1.85",        1.85, 3840, 2076),
    ("1.90",        1.90, 3840, 2020),
    ("2.00",        2.00, 3840, 1920),
    ("2.35",        2.35, 3840, 1634),
    ("2.37",        2.37, 3840, 1620),
    ("2.39",        2.39, 3840, 1606),
    ("2.40",        2.40, 3840, 1600),
    ("2.44",        2.44, 3840, 1574),
]

_LADDER_3K_UHD = [
    ("0.80 (4:5)",  0.80, 1296, 1620),
    ("1.25 (5:4)",  1.25, 2024, 1620),
    ("1.33 (4:3)",  1.33, 2160, 1620),
    ("1.66 (5:3)",  1.66, 2700, 1620),
    ("1.78 (16:9)", 1.78, 2880, 1620),
    ("1.85",        1.85, 2880, 1556),
    ("1.90",        1.90, 2880, 1516),
    ("2.00",        2.00, 2880, 1440),
    ("2.35",        2.35, 2880, 1226),
    ("2.37",        2.37, 2880, 1216),
    ("2.39",        2.39, 2880, 1204),
    ("2.40",        2.40, 2880, 1200),
    ("2.44",        2.44, 2880, 1180),
]

_LADDER_1080P = [
    ("0.80 (4:5)",  0.80,  864, 1080),
    ("1.25 (5:4)",  1.25, 1350, 1080),
    ("1.33 (4:3)",  1.33, 1440, 1080),
    ("1.66 (5:3)",  1.66, 1800, 1080),
    ("1.78 (16:9)", 1.78, 1920, 1080),
    ("1.85",        1.85, 1920, 1038),
    ("1.90",        1.90, 1920, 1010),
    ("2.00",        2.00, 1920,  960),
    ("2.35",        2.35, 1920,  816),
    ("2.37",        2.37, 1920,  810),
    ("2.39",        2.39, 1920,  802),
    ("2.40",        2.40, 1920,  800),
    ("2.44",        2.44, 1920,  786),
]

_LADDER_720P = [
    ("0.80 (4:5)",  0.80,  576, 720),
    ("1.25 (5:4)",  1.25,  900, 720),
    ("1.33 (4:3)",  1.33,  960, 720),
    ("1.66 (5:3)",  1.66, 1200, 720),
    ("1.78 (16:9)", 1.78, 1280, 720),
    ("1.85",        1.85, 1280, 692),
    ("1.90",        1.90, 1280, 674),
    ("2.00",        2.00, 1280, 640),
    ("2.35",        2.35, 1280, 544),
    ("2.37",        2.37, 1280, 540),
    ("2.39",        2.39, 1280, 536),
    ("2.40",        2.40, 1280, 532),
    ("2.44",        2.44, 1280, 524),
]

UHD_BROADCAST: List[Target] = (
    _uhd_ladder("8K UHD",  "8k_uhd",  _LADDER_8K_UHD)
    + _uhd_ladder("4K UHD",  "4k_uhd",  _LADDER_4K_UHD)
    + _uhd_ladder("3K UHD",  "3k_uhd",  _LADDER_3K_UHD)
    + _uhd_ladder("1080p",   "1080p",   _LADDER_1080P)
    + _uhd_ladder("720p",    "720p",    _LADDER_720P)
)


# ─────────────────────────────────────────────────────────────────────────
# SOCIAL — by platform (Instagram, Facebook, TikTok, X, Threads, Pinterest, YouTube)
# Each entry is a (sub_label, width, height, aspect_label) tuple. Aspect
# ratios are auto-computed.
# ─────────────────────────────────────────────────────────────────────────

def _social(platform_slug: str, platform_label: str, rows: list) -> List[Target]:
    out = []
    for row in rows:
        # Rows are 5-tuples (sub_slug, sub_label, w, h, aspect_label) by
        # default. A 6th, optional element sets `channel` — only for the
        # vertical-video/motion-context sub-targets where channels.yaml's
        # "social" rule (a top/bottom platform-UI-chrome occlusion model)
        # actually applies. Do NOT set this on static formats (profile
        # pictures, banners, cover photos, landscape/square posts) — the
        # rule models captions/buttons on a 9:16 video frame, which is
        # semantically meaningless there and would nudge layers away from
        # a "caption bar" that doesn't exist. See #476 audit finding +
        # docs/knowledge/2026-09-05-programmatic-safe-zones-research.md.
        sub_slug, sub_label, w, h, aspect_label = row[:5]
        channel = row[5] if len(row) > 5 else None
        out.append(_t(
            id_slug=f"{platform_slug}_{sub_slug}",
            label=f"{platform_label} · {sub_label}",
            category="social",
            subcategory=platform_slug,
            width=w,
            height=h,
            aspect_label=aspect_label,
            channel=channel,
        ))
    return out


_INSTAGRAM = [
    ("profile",   "Profile Picture",       320,  320,  "1:1"),
    ("portrait",  "Portrait Post (4:5)",  1080, 1350,  "4:5"),
    ("square",    "Square Post (1:1)",    1080, 1080,  "1:1"),
    ("tall",      "Tall Post (3:4)",      1080, 1440,  "3:4"),
    ("landscape", "Landscape Post",       1080,  566,  "1.91:1"),
    ("story",     "Stories & Reels",      1080, 1920,  "9:16", "social"),
    ("reel_thumb","Reels Thumbnail",      1080, 1920,  "9:16", "social"),
]

_FACEBOOK = [
    ("profile",    "Profile Picture",      320,  320, "1:1"),
    ("cover",      "Cover Photo",          851,  315, "2.7:1"),
    ("group",      "Group Cover",         1640,  856, "1.92:1"),
    ("event",      "Event Cover",         1920, 1005, "1.91:1"),
    ("vertical",   "Vertical Post",       1080, 1350, "4:5"),
    ("square",     "Square Post",         1080, 1080, "1:1"),
    ("landscape",  "Landscape Post",      1200,  630, "1.91:1"),
    ("story",      "Stories & Reels",     1080, 1920, "9:16", "social"),
]

_TIKTOK = [
    ("profile",   "Profile Picture",       200,  200, "1:1"),
    ("video",     "Standard Video",       1080, 1920, "9:16", "social"),
    ("carousel",  "Carousel Image",       1080, 1920, "9:16", "social"),
    ("thumb",     "Video Thumbnail",      1080, 1920, "9:16", "social"),
]

_X_TWITTER = [
    ("profile",   "Profile Picture",       400,  400, "1:1"),
    ("header",    "Header Image",         1500,  500, "3:1"),
    ("standard",  "Standard In-Feed",     1600,  900, "16:9"),
    ("square",    "Square In-Feed",       1080, 1080, "1:1"),
    ("portrait",  "Portrait In-Feed",     1080, 1350, "4:5"),
    ("link",      "Link/Card Image",      1200,  628, "1.91:1"),
]

_THREADS = [
    ("profile",   "Profile Photo",         320,  320, "1:1"),
    ("post",      "Standard Post",        1080, 1350, "4:5"),
    ("link",      "Link Preview",         1200,  600, "2:1"),
    ("video",     "Video Post",           1080, 1920, "9:16", "social"),
]

_PINTEREST = [
    ("profile",    "Profile Picture",      165,  165, "1:1"),
    ("cover",      "Cover Photo",          800,  450, "16:9"),
    ("pin",        "Standard Pin",        1000, 1500, "2:3"),
    ("square_pin", "Square Pin",          1000, 1000, "1:1"),
    ("idea",       "Idea Pin",            1080, 1920, "9:16", "social"),
    ("board",      "Board Cover",          600,  600, "1:1"),
    ("collection", "Collection Pin",      1000, 1500, "2:3"),
]

_YOUTUBE = [
    ("profile",   "Channel Profile",       800,  800, "1:1"),
    ("banner",    "Channel Banner",       2560, 1440, "16:9"),
    ("thumb",     "Video Thumbnail",      1280,  720, "16:9"),
    ("shorts",    "YouTube Shorts",       1080, 1920, "9:16", "social"),
    ("community", "Community Post",       1200, 1200, "1:1"),
    ("playlist",  "Playlist Cover",       1280, 1280, "1:1"),
]

SOCIAL: List[Target] = (
    _social("instagram", "Instagram", _INSTAGRAM)
    + _social("facebook",  "Facebook",  _FACEBOOK)
    + _social("tiktok",    "TikTok",    _TIKTOK)
    + _social("x_twitter", "X (Twitter)", _X_TWITTER)
    + _social("threads",   "Threads",   _THREADS)
    + _social("pinterest", "Pinterest", _PINTEREST)
    + _social("youtube",   "YouTube",   _YOUTUBE)
)


# ─────────────────────────────────────────────────────────────────────────
# OUT-OF-HOME / DOOH
# Loaded dynamically from config/ooh_specs.json (78 production specs)
# ─────────────────────────────────────────────────────────────────────────

def _load_ooh_targets() -> List[Target]:
    specs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "config", "ooh_specs.json"))
    if not os.path.exists(specs_path):
        return []
    try:
        with open(specs_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = []
        for s in data.get("production_specs", []):
            spec_id = s.get("spec_id", "").lower().replace("-", "_")
            width = s["width_px"]
            height = s["height_px"]
            aspect_ratio = round(float(width) / float(height), 4)
            out.append(Target.make(
                id=f"builtin:ooh_{spec_id}",
                label=f"{s.get('placement_id_type', s.get('spec_id'))} ({width}x{height})",
                category="custom_signage",
                subcategory=s.get("category", "dooh"),
                width=width,
                height=height,
                aspect_label=s.get("aspect_label", f"{aspect_ratio}:1"),
                aspect_ratio=aspect_ratio,
                source="builtin",
                channel="ooh",
                fps=s.get("fps"),
                duration=s.get("duration_sec"),
                metadata={
                    "market_id": s.get("market_id"),
                    "video_codec": s.get("video_codec"),
                    "still": s.get("still"),
                    "bitrate": s.get("bitrate"),
                    "audio": s.get("audio"),
                    "delivery_notes": s.get("delivery_notes"),
                    "multi_panel": s.get("multi_panel"),
                    "safe_zones": s.get("safe_zones"),
                }
            ))
        return out
    except Exception:
        return []


OOH: List[Target] = _load_ooh_targets()


# ─────────────────────────────────────────────────────────────────────────
# Public API — flat list and lookup helpers
# ─────────────────────────────────────────────────────────────────────────

BUILTIN_TARGETS: List[Target] = DIGITAL_CINEMA + UHD_BROADCAST + SOCIAL + OOH

_BY_ID = {t.id: t for t in BUILTIN_TARGETS}


# Display order for drawer tabs / sub-headers inside each category. The modal
# uses this when grouping rows so the order is stable across runs.
SUBCATEGORY_ORDER = {
    "digital_cinema": ["dcp", "k_tier_8k", "k_tier_6k", "k_tier_5k", "k_tier_4k", "k_tier_3k", "k_tier_2k"],
    "uhd_broadcast":  ["8k_uhd", "4k_uhd", "3k_uhd", "1080p", "720p"],
    "social":         ["instagram", "facebook", "tiktok", "x_twitter", "threads", "pinterest", "youtube"],
    "custom_signage": ["stadium_ribbons", "urban_spectaculars", "transit_triptychs", "portrait_towers", "horizontal_billboards", "dooh", "user"],
}


SUBCATEGORY_LABELS = {
    "dcp":         "DCP Standards",
    "k_tier_8k":   "8K",
    "k_tier_6k":   "6K",
    "k_tier_5k":   "5K",
    "k_tier_4k":   "4K",
    "k_tier_3k":   "3K",
    "k_tier_2k":   "2K",
    "8k_uhd":      "8K UHD",
    "4k_uhd":      "4K UHD",
    "3k_uhd":      "3K UHD",
    "1080p":       "1080p",
    "720p":        "720p",
    "instagram":   "Instagram",
    "facebook":    "Facebook",
    "tiktok":      "TikTok",
    "x_twitter":   "X (Twitter)",
    "threads":     "Threads",
    "pinterest":   "Pinterest",
    "youtube":     "YouTube",
    "stadium_ribbons": "Stadium Ribbons",
    "urban_spectaculars": "Urban Spectaculars",
    "transit_triptychs": "Transit Triptychs",
    "portrait_towers": "Portrait Towers",
    "horizontal_billboards": "Horizontal Billboards",
    "dooh":        "Digital Out-of-Home",
    "user":        "Custom Sizes",
}

CATEGORY_LABELS = {
    "digital_cinema": "Digital Cinema",
    "uhd_broadcast":  "UHD / Broadcast",
    "social":         "Social",
    "custom_signage": "Custom / Signage",
}
