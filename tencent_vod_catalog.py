"""Curated VOD models with adapters for the canvas's ordinary image/video nodes.

Sources (2026-10-27): Tencent VOD CreateAigcImageTask (266/126240),
CreateAigcVideoTask (266/126239), data structures (266/31773), and the
user-supplied WAND VOD guide. This is NOT an account entitlement list.
Specialized 3D, scene, avatar and lip-sync APIs are deliberately not advertised.
"""

RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"]
CATALOG = {}


def add(kind, family, versions, *, refs=None, resolutions=(), ratios=(), durations=(), audio=False):
    for version in versions:
        model_id = f"vod-{family.lower()}-{kind}-{version.lower()}"
        CATALOG[model_id] = {
            "id": model_id, "kind": kind, "model_name": family, "model_version": version,
            "label": f"{family} {version}", "max_references": refs,
            "resolutions": list(resolutions), "aspect_ratios": list(ratios),
            "durations": list(durations), "audio_generation": audio,
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

IMAGE_MODELS = [key for key, value in CATALOG.items() if value["kind"] == "image"]
VIDEO_MODELS = [key for key, value in CATALOG.items() if value["kind"] == "video"]
