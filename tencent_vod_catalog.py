"""Curated VOD models with adapters for the canvas's ordinary image/video nodes.

Sources (2026-10-27): Tencent VOD CreateAigcImageTask (266/126240),
CreateAigcVideoTask (266/126239), data structures (266/31773), and the
user-supplied WAND VOD guide. This is NOT an account entitlement list.
Specialized 3D, scene, avatar and lip-sync APIs are deliberately not advertised.
"""

RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"]
CATALOG = {}


def add(kind, family, versions, *, refs=None, resolutions=(), ratios=(), durations=(), audio=False,
        reference_limits=None, benchmark="", pricing_note=""):
    for version in versions:
        model_id = f"vod-{family.lower()}-{kind}-{version.lower()}"
        CATALOG[model_id] = {
            "id": model_id, "kind": kind, "model_name": family, "model_version": version,
            "label": f"{family} {version}", "max_references": refs,
            "resolutions": list(resolutions), "aspect_ratios": list(ratios),
            "durations": list(durations), "audio_generation": audio,
            "reference_limits": reference_limits, "benchmark": benchmark, "pricing_note": pricing_note,
        }


CATALOG["wand-vega-image-lite"] = {
    "id": "wand-vega-image-lite", "kind": "image", "model_name": "wand-vega-image",
    "model_version": "1.0-lite", "label": "WAND Vega Image 1.0 Lite",
    "max_references": 20, "resolutions": ["1K", "2K", "4K"], "aspect_ratios": RATIOS,
}
add("image", "OG", ["image2_low", "image2_medium", "image2_high"], resolutions=["1K", "2K", "4K"], ratios=RATIOS + ["9:21"])
add("image", "GG", ["2.5"], refs=3, resolutions=["1K", "2K", "4K"], ratios=RATIOS + ["4:5", "5:4"])
add("image", "GG", ["3.0"], refs=14, resolutions=["1K", "2K", "4K"], ratios=RATIOS + ["4:5", "5:4"])
add("image", "GG", ["3.1"], refs=14, resolutions=["720P", "1K", "2K", "4K"], ratios=RATIOS + ["4:5", "5:4", "1:4", "1:8", "4:1", "8:1"])
add("image", "Hunyuan", ["3.0"], refs=3, resolutions=["1K"], ratios=RATIOS)
add("image", "Vidu", ["q2"], refs=7, resolutions=["1080p", "2K", "4K"], ratios=RATIOS)
add("image", "Kling", ["2.1"], refs=4, resolutions=["1k", "2k"], ratios=RATIOS)
add("image", "Kling", ["3.0"], refs=1, resolutions=["1k", "2k"], ratios=RATIOS)
add("image", "Kling", ["3.0-Omni", "O1"], refs=10, resolutions=["1k", "2k", "4k"], ratios=RATIOS)

add("video", "Kling", ["1.6", "2.0", "2.1", "2.5", "2.6", "O1", "3.0", "3.0-Omni", "3.0-turbo"],
    resolutions=["720P", "1080P"], ratios=["16:9", "9:16", "1:1"], durations=range(3, 16), audio=True)
add("video", "Hailuo", ["02", "2.3", "2.3-fast", "H3-Max"], resolutions=["768P", "1080P"], durations=[6, 10])
add("video", "Hailuo", ["H3"], resolutions=["768P", "1080P", "2K", "4K"], ratios=["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], durations=range(4, 16))
add("video", "Vidu", ["q2"], resolutions=["720P", "1080P"], ratios=["16:9", "9:16", "1:1", "4:3", "3:4"], durations=range(1, 11), audio=True)
add("video", "Vidu", ["q2-pro", "q2-turbo", "q3", "q3-pro", "q3-turbo"], resolutions=["720P", "1080P"], ratios=["16:9", "9:16", "1:1"], durations=range(1, 11), audio=True)
add("video", "GV", ["3.1", "3.1-fast", "3.1-lite", "omni"], resolutions=["720P", "1080P"], ratios=["16:9", "9:16"], durations=[8], audio=True)
add("video", "OS", ["2.0"], resolutions=["720P"], ratios=["16:9", "9:16"], durations=[4, 8, 12], audio=True)
add("video", "PixVerse", ["v5.6", "v6", "c1"], resolutions=["540p", "720p", "1080p", "2k", "4k"], ratios=RATIOS, durations=range(1, 16))
# Hunyuan 1.5 uses upstream defaults for undocumented output resolution options.
add("video", "Hunyuan", ["1.5"])

WAND_VEGA_LABELS = {
    "1.0-pro": "WAND Vega Video Pro 1.0",
    "1.0-pro-fast": "WAND Vega Video Pro 1.0 Fast",
    "1.0-pro-mini": "WAND Vega Video Pro 1.0 Mini",
    "1.5-pro": "WAND Vega Video Pro 1.5",
}
WAND_VEGA_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"]
WAND_VEGA_PRICING = "价格对标 SD2 / SD2.5：480P 约为 90%，720P 及以上约为 80%，用量可进一步优惠"
for _version, _label in WAND_VEGA_LABELS.items():
    _is_15 = _version.startswith("1.5")
    _limits = ({"image": 30, "video": 10, "audio": 10, "total": 50} if _is_15
               else {"image": 9, "video": 3, "audio": 3, "total": 12})
    _id = f"vod-wand-vega-video-{_version}"
    CATALOG[_id] = {
        "id": _id, "kind": "video", "model_name": "wand-vega-video", "model_version": _version,
        "label": _label, "max_references": _limits["total"],
        "resolutions": ["720P", "1080P", "2K", "4K"], "aspect_ratios": WAND_VEGA_RATIOS,
        "durations": list(range(4, 31 if _is_15 else 16)), "audio_generation": True,
        "reference_limits": _limits, "benchmark": "效果对标 SD2 / SD2.5", "pricing_note": WAND_VEGA_PRICING,
    }

# Wan 3.0 is a separate VOD family. The VOD guide does not publish hard
# reference-count or aspect-ratio limits, so those are intentionally not guessed.
WAN_VIDEO_MODELS = ["vod-wan-video-3.0"]
CATALOG["vod-wan-video-3.0"] = {
    "id": "vod-wan-video-3.0", "kind": "video", "model_name": "Wan", "model_version": "3.0",
    "label": "Wan 3.0 Video", "max_references": None,
    "resolutions": ["480P", "720P", "1080P"], "aspect_ratios": [],
    "durations": list(range(2, 31)), "audio_generation": False,
    "reference_limits": None, "benchmark": "", "pricing_note": "",
}

# Earlier entries used gateway-facing names instead of VOD catalog IDs.
# Keep them resolvable so existing saved canvases do not require a rebuild.
VIDEO_MODEL_ALIASES = {
    "wand-vega-video-1.0-lite": "vod-wand-vega-video-1.0-pro-mini",
    "wandvega-video-1.0-lite": "vod-wand-vega-video-1.0-pro-mini",
    "wand-vega-video-1.5-lite": "vod-wand-vega-video-1.5-pro",
    "wandvega-video-1.5-lite": "vod-wand-vega-video-1.5-pro",
    "wand-vega-video-1.0-pro": "vod-wand-vega-video-1.0-pro",
    "wand-vega-video-1.0-pro-fast": "vod-wand-vega-video-1.0-pro-fast",
    "wand-vega-video-1.0-pro-mini": "vod-wand-vega-video-1.0-pro-mini",
    "wand-vega-video-1.5-pro": "vod-wand-vega-video-1.5-pro",
    "Wan3.0": "vod-wan-video-3.0",
    "wan-3.0": "vod-wan-video-3.0",
    "wan3.0-video": "vod-wan-video-3.0",
}
VIDEO_MODEL_ALIAS_INDEX = {
    "".join(ch for ch in key.casefold() if ch not in "- _"): value
    for key, value in VIDEO_MODEL_ALIASES.items()
}


def canonical_model_id(model, kind=None):
    """Return a catalog ID for a saved model name, without guessing unknown models."""
    requested = str(model or "").strip()
    normalized = "".join(ch for ch in requested.casefold() if ch not in "- _")
    if normalized in VIDEO_MODEL_ALIAS_INDEX:
        return VIDEO_MODEL_ALIAS_INDEX[normalized]
    if requested in CATALOG and (kind is None or CATALOG[requested]["kind"] == kind):
        return requested
    casefolded = {key.casefold(): key for key in CATALOG}
    match = casefolded.get(requested.casefold())
    return match if match and (kind is None or CATALOG[match]["kind"] == kind) else None

IMAGE_MODELS = [key for key, value in CATALOG.items() if value["kind"] == "image"]
VIDEO_MODELS = [key for key, value in CATALOG.items() if value["kind"] == "video"]
WAND_VIDEO_MODELS = [key for key, value in CATALOG.items() if value["model_name"] == "wand-vega-video"]
