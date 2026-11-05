import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import main
import tencent_vod as vod


CONFIG = {"secret_id": "test-catalog-id", "secret_key": "test-catalog-secret"}


class TencentVodCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = patch.object(vod, "VERIFICATION_FILE", Path(self.temp.name) / "verification.json")
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def image(self, model, size="1K", refs=None):
        return vod.image_request("test", size, model, refs or [], 123, lambda r: "data:image/png;base64,aW1hZ2U=")

    def video(self, model="vod-kling-video-3.0", **kwargs):
        values = {"model": model, "prompt": "test", "duration": 8, "aspect_ratio": "16:9", **kwargs}
        return vod.video_request(values, 123, lambda r: "data:video/mp4;base64,dmlkZW8=")

    def test_catalog_only_advertises_models_with_matching_adapters(self):
        self.assertGreater(len(vod.IMAGE_MODELS), 10)
        self.assertGreater(len(vod.VIDEO_MODELS), 20)
        for model in vod.IMAGE_MODELS:
            with self.subTest(model=model):
                body = self.image(model)
                self.assertEqual(body["ModelName"], vod.CATALOG[model]["model_name"])
                self.assertEqual(body["ModelVersion"], vod.CATALOG[model]["model_version"])
        for model in vod.VIDEO_MODELS:
            spec = vod.CATALOG[model]
            with self.subTest(model=model):
                body = self.video(model, duration=spec["durations"][0] if spec["durations"] else 5)
                self.assertEqual(body["ModelName"], spec["model_name"])
                self.assertEqual(body["ModelVersion"], spec["model_version"])

    def test_wand_vega_catalog_maps_all_versions_and_capabilities(self):
        expected = {
            "vod-wand-vega-video-1.0-pro": ("1.0-pro", 15, {"image": 9, "video": 3, "audio": 3, "total": 12}),
            "vod-wand-vega-video-1.0-pro-fast": ("1.0-pro-fast", 15, {"image": 9, "video": 3, "audio": 3, "total": 12}),
            "vod-wand-vega-video-1.0-pro-mini": ("1.0-pro-mini", 15, {"image": 9, "video": 3, "audio": 3, "total": 12}),
            "vod-wand-vega-video-1.5-pro": ("1.5-pro", 30, {"image": 30, "video": 10, "audio": 10, "total": 50}),
        }
        for model, (version, max_duration, limits) in expected.items():
            with self.subTest(model=model):
                spec = vod.CATALOG[model]
                self.assertEqual(spec["model_name"], "wand-vega-video")
                self.assertEqual(spec["model_version"], version)
                self.assertEqual(max(spec["durations"]), max_duration)
                self.assertEqual(spec["reference_limits"], limits)
                self.assertIn("SD2", spec["benchmark"])
                self.assertIn("80%", spec["pricing_note"])
                body = self.video(model, duration=max_duration, resolution="720p")
                self.assertEqual(body["ModelName"], "wand-vega-video")
                self.assertEqual(body["ModelVersion"], version)

    def test_wand_vega_duration_and_resolution_are_validated(self):
        with self.assertRaises(HTTPException):
            self.video("vod-wand-vega-video-1.0-pro", duration=16, resolution="720p")
        self.assertEqual(self.video("vod-wand-vega-video-1.5-pro", duration=30, resolution="720p")["OutputConfig"]["Duration"], 30)
        for model in ("vod-wand-vega-video-1.0-pro", "vod-wand-vega-video-1.0-pro-fast",
                      "vod-wand-vega-video-1.0-pro-mini", "vod-wand-vega-video-1.5-pro"):
            with self.subTest(model=model), self.assertRaises(HTTPException):
                self.video(model, duration=4, resolution="480p")

    def test_saved_wand_vega_aliases_resolve_to_catalog_models(self):
        cases = {
            "WAND-Vega-Video-1.0-Lite": "vod-wand-vega-video-1.0-pro-mini",
            "wand-vega-video-1.0-lite": "vod-wand-vega-video-1.0-pro-mini",
            "WAND-Vega-Video-1.5-Lite": "vod-wand-vega-video-1.5-pro",
            "wand-vega-video-1.5-lite": "vod-wand-vega-video-1.5-pro",
        }
        for legacy, canonical in cases.items():
            with self.subTest(model=legacy):
                self.assertEqual(vod.canonical_model_id(legacy, "video"), canonical)
                body = self.video(legacy, duration=4, resolution="720p")
                self.assertEqual(body["ModelName"], "wand-vega-video")
                self.assertEqual(body["ModelVersion"], vod.CATALOG[canonical]["model_version"])

    def test_wan_catalog_maps_aliases_duration_and_enhancement(self):
        self.assertEqual(vod.WAN_VIDEO_MODELS, ["vod-wan-video-3.0"])
        # Case-only aliases collapse to one key in case-insensitive JSON clients.
        for alias in ("Wan3.0", "WAN3.0", "wan-3.0", "wan3.0-video"):
            with self.subTest(alias=alias):
                self.assertEqual(vod.canonical_model_id(alias, "video"), "vod-wan-video-3.0")
        spec = vod.CATALOG["vod-wan-video-3.0"]
        self.assertEqual((spec["model_name"], spec["model_version"]), ("Wan", "3.0"))
        self.assertEqual(spec["resolutions"], ["480P", "720P", "1080P"])
        self.assertEqual(spec["durations"], list(range(2, 31)))
        self.assertIsNone(spec["reference_limits"])
        for duration in (2, 30):
            body = self.video("Wan3.0", duration=duration, resolution="720p")
            self.assertEqual((body["ModelName"], body["ModelVersion"]), ("Wan", "3.0"))
            self.assertEqual(body["OutputConfig"]["Duration"], duration)
        for duration in (1, 31):
            with self.subTest(duration=duration), self.assertRaises(HTTPException):
                self.video("Wan3.0", duration=duration, resolution="720p")
        for resolution in ("480p", "720p", "1080p"):
            with self.subTest(resolution=resolution):
                body = self.video("Wan3.0", duration=5, resolution=resolution)
                self.assertEqual(body["OutputConfig"]["Resolution"], resolution.upper())
        for resolution in ("2K", "4K"):
            with self.subTest(resolution=resolution), self.assertRaises(HTTPException):
                self.video("Wan3.0", duration=5, resolution=resolution)
        enhanced = self.video("Wan3.0", duration=5, resolution="1080P", enable_upsample=True)
        self.assertEqual(enhanced["OutputConfig"]["EnhanceSwitch"], "Enabled")
        with self.assertRaises(HTTPException):
            self.video("Wan3.0", duration=5, resolution="480P", enable_upsample=True)
        with self.assertRaises(HTTPException):
            self.video("Wan3.0", duration=5, resolution="720P", generate_audio=True)
        self.assertNotIn("ExtInfo", self.video("Wan3.0", enhance_prompt=True))

    def test_vod_normalization_keeps_wan_with_all_vega_models(self):
        provider = main.normalize_provider({"id": "legacy-vod", "protocol": vod.PROTOCOL,
                                            "base_url": vod.DEFAULT_BASE,
                                            "video_models": ["WAND-Vega-Video-1.0-Lite", "Wan3.0"]})
        self.assertEqual(provider["video_models"], [*vod.WAND_VIDEO_MODELS, "vod-wan-video-3.0"])
        self.assertEqual(provider["video_models"].count("vod-wan-video-3.0"), 1)

    def test_wand_vega_reference_limits_and_audio_dependency_are_explicit(self):
        image = {"url": "https://example.com/reference.png", "role": "reference_image"}
        video = "https://example.com/reference.mp4"
        audio = "https://example.com/reference.mp3"
        with self.assertRaises(HTTPException):
            self.video("vod-wand-vega-video-1.0-pro", duration=4, images=[image] * 10)
        with self.assertRaises(HTTPException):
            self.video("vod-wand-vega-video-1.0-pro", duration=4, videos=[video] * 4)
        with self.assertRaises(HTTPException):
            self.video("vod-wand-vega-video-1.0-pro", duration=4, audios=[audio] * 4)
        with self.assertRaises(HTTPException):
            self.video("vod-wand-vega-video-1.0-pro", duration=4, audios=[audio])
        with self.assertRaises(HTTPException):
            self.video("vod-wand-vega-video-1.0-pro", duration=4, images=[image] * 9,
                       videos=[video] * 2, audios=[audio] * 2)
        body = self.video("vod-wand-vega-video-1.5-pro", duration=30, images=[image] * 30,
                          videos=[video] * 10, audios=[audio] * 10, multimodal=True)
        self.assertEqual(len(body["FileInfos"]), 50)

    def test_wand_vega_extensions_use_documented_ext_info_shape(self):
        body = self.video("vod-wand-vega-video-1.0-pro", duration=8, resolution="1080p",
                          enhance_prompt=True, return_last_frame=True)
        extensions = json.loads(body["ExtInfo"])
        self.assertEqual(json.loads(extensions["AdditionalParameters"]), {"return_last_frame": True})
        self.assertEqual(json.loads(extensions["WandVegaParameters"]), {"EnhancePromptMode": "enable"})
        self.assertNotIn("EnhanceSwitch", body["OutputConfig"])
        self.assertEqual(self.video("vod-wand-vega-video-1.0-pro", duration=8, aspect_ratio="adaptive")["OutputConfig"]["AspectRatio"], "adaptive")
        with self.assertRaises(HTTPException):
            self.video(return_last_frame=True)

    def test_image_resolution_and_ratio_map_without_wand_parameters(self):
        gg = self.image("vod-gg-image-3.1", "2048x1152")
        self.assertEqual(gg["OutputConfig"]["AspectRatio"], "16:9")
        self.assertEqual(gg["OutputConfig"]["Resolution"], "2K")
        self.assertNotIn("ExtInfo", gg)
        self.assertEqual(self.image("vod-kling-image-o1")["OutputConfig"]["Resolution"], "1k")
        self.assertEqual(self.image("vod-vidu-image-q2")["OutputConfig"]["Resolution"], "1080p")
        self.assertEqual(self.image("vod-gg-image-3.1", "3840x1648")["OutputConfig"]["Resolution"], "4K")
        self.assertEqual(self.image("vod-gg-image-3.1", "2048x880")["OutputConfig"]["Resolution"], "2K")

    def test_hunyuan_size_uses_documented_extension(self):
        body = self.image("vod-hunyuan-image-3.0")
        self.assertEqual(json.loads(json.loads(body["ExtInfo"])["AdditionalParameters"])["size"], "1024x1024")
        self.assertEqual(body["OutputConfig"], {"StorageMode": "Temporary"})
        with self.assertRaises(HTTPException):
            self.image("vod-hunyuan-image-3.0", "2048x2048")

    def test_image_limits_are_per_model_and_do_not_drop_references(self):
        refs = [{"url": "/assets/input/reference.png"}] * 2
        with self.assertRaises(HTTPException):
            self.image("vod-kling-image-3.0", refs=refs)
        self.assertEqual(len(self.image("vod-gg-image-3.1", refs=refs)["FileInfos"]), 2)
        with self.assertRaises(HTTPException):
            self.image("vod-kling-image-2.1", "4K")
        with self.assertRaises(HTTPException):
            self.image("vod-gg-image-3.0", "3000x1000")

    def test_image_fields_follow_selected_model(self):
        fields = main.build_image_param_fields("api", {"protocol": vod.PROTOCOL}, "vod-kling-image-3.0")
        self.assertEqual(fields[2]["max"], 1)
        self.assertEqual([x["value"] for x in fields[0]["resolutions"]], ["1k", "2k"])

    def test_video_maps_settings_and_first_last_frames(self):
        body = self.video(images=[{"url": "https://example.com/first.png", "role": "first_frame"},
                                  {"url": "https://example.com/last.png", "role": "last_frame"}],
                          resolution="1080p", generate_audio=True, enhance_prompt=True, seed=0, watermark=True)
        self.assertEqual([f["Usage"] for f in body["FileInfos"]], ["FirstFrame", "LastFrame"])
        self.assertEqual(body["FileInfos"][0]["Category"], "Image")
        self.assertEqual(body["OutputConfig"]["Resolution"], "1080P")
        self.assertEqual(body["OutputConfig"]["AudioGeneration"], "Enabled")
        self.assertEqual(body["OutputConfig"]["LogoAdd"], "Enabled")
        self.assertEqual(body["EnhancePrompt"], "Enabled")
        self.assertEqual(body["Seed"], 0)

    def test_video_preserves_multiple_references_and_categories(self):
        body = self.video(images=[{"url": "https://example.com/a.png"}, {"url": "https://example.com/b.png"}],
                          videos=["/assets/input/a.mp4"], audios=["https://example.com/a.mp3"], multimodal=True)
        self.assertEqual([f["Category"] for f in body["FileInfos"]], ["Image", "Image", "Video", "Audio"])
        self.assertTrue(all(f["Usage"] == "Reference" for f in body["FileInfos"]))
        self.assertEqual(body["FileInfos"][2]["Base64"], "dmlkZW8=")

    def test_video_validation_rejects_mismatched_not_silently_changed_settings(self):
        cases = [dict(model="vod-gv-video-3.1", duration=5), dict(resolution="480p"), dict(aspect_ratio="9:21"),
                 dict(camerafixed=True), dict(model="vod-hailuo-video-02", duration=6, generate_audio=True),
                 dict(images=[{"url": "https://example.com/last.png", "role": "last_frame"}]),
                 dict(images=[{"url": "https://example.com/a.png", "role": "first_frame"}], videos=["https://example.com/v.mp4"])]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(HTTPException):
                self.video(**case)

    def test_wrong_kind_unknown_model_or_missing_app_fails_before_submission(self):
        for model in (vod.MODEL_ID, "vod-invented-video", "kling-video-v3"):
            with self.assertRaises(HTTPException):
                self.video(model)
        with self.assertRaises(HTTPException):
            vod.video_request({"model": "vod-kling-video-3.0"}, 0, lambda r: r)

    def test_discovery_reports_both_categories_without_claiming_generation_permission(self):
        with patch.object(vod, "call", AsyncMock(return_value={})) as remote:
            data = asyncio.run(vod.discover(CONFIG, 123))
        self.assertEqual(remote.await_count, 1)
        self.assertEqual(remote.call_args.args[1], "DescribeAigcQuotas")
        self.assertEqual(data["total"], len(vod.CATALOG))
        self.assertEqual(data["video_models"], vod.VIDEO_MODELS)
        self.assertTrue(all(meta["verification"] == "unverified" for meta in data["model_metadata"].values()))

    def test_verification_is_private_scoped_evidence_not_universal_permission(self):
        body = self.video()
        vod.record_success(CONFIG, body, "video")
        model = "vod-kling-video-3.0"
        self.assertEqual(vod.model_metadata(CONFIG, 123)[model]["verification"], "verified")
        self.assertEqual(vod.model_metadata(CONFIG, 456)[model]["verification"], "unverified")
        self.assertEqual(vod.model_metadata({**CONFIG, "secret_key": "different"}, 123)[model]["verification"], "unverified")
        stored = vod.VERIFICATION_FILE.read_text()
        for value in CONFIG.values():
            self.assertNotIn(value, stored)

    def test_video_polls_download_result_and_records_only_success(self):
        actions = []
        def handler(request):
            action = request.headers["x-tc-action"]
            actions.append(action)
            self.assertEqual(json.loads(request.content)["SubAppId"], 123)
            data = {"TaskId": "test-video-task"} if action == "CreateAigcVideoTask" else {
                "Status": "FINISH", "AigcVideoTask": {"Output": {"FileInfos": [{"FileUrl": "https://example.com/output.mp4"}]}}}
            return httpx.Response(200, json={"Response": data})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await vod.generate(CONFIG, self.video(), kind="video", client=client, interval=0)
        _, result = asyncio.run(run())
        self.assertEqual(result["task_id"], "test-video-task")
        self.assertEqual(actions, ["CreateAigcVideoTask", "DescribeTaskDetail"])
        self.assertEqual(vod.model_metadata(CONFIG, 123)["vod-kling-video-3.0"]["verification"], "verified")

    def test_failed_or_empty_video_is_never_marked_verified(self):
        for task in ({"ErrCodeExt": "FailedOperation.NoPermission", "Message": "not enabled"}, {"Output": {"FileInfos": []}}):
            async def run():
                with patch.object(vod, "call", AsyncMock(side_effect=[{"TaskId": "task-1"}, {"Status": "FINISH", "AigcVideoTask": task}])):
                    return await vod.generate(CONFIG, self.video(), kind="video", interval=0)
            with self.assertRaises(HTTPException):
                asyncio.run(run())
            self.assertFalse(vod.VERIFICATION_FILE.exists())

    def test_timeout_keeps_task_id_without_resubmitting(self):
        with patch.object(vod, "call", AsyncMock(return_value={"TaskId": "still-running"})) as remote:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(vod.generate(CONFIG, self.video(), kind="video", timeout=0))
        self.assertEqual(raised.exception.upstream_task_id, "still-running")
        self.assertEqual(remote.await_count, 1)

    def test_canvas_video_dispatches_vod_without_bearer_key_and_saves_local_result(self):
        provider = {"id": "test-vod", "protocol": vod.PROTOCOL, "tencent_sub_app_id": 123}
        payload = main.CanvasVideoRequest(provider_id="test-vod", model="vod-kling-video-3.0", prompt="test")
        result = ({"type": "url", "value": "https://example.com/a.mp4"}, {"task_id": "video-task", "data": [{"url": "https://example.com/a.mp4"}]})
        with patch.object(main, "get_api_provider", return_value=provider), \
                patch.object(main, "tencent_vod_config", return_value=CONFIG), \
                patch.object(vod, "generate", AsyncMock(return_value=result)) as generate, \
                patch.object(main, "save_ominilink_video_to_output", AsyncMock(return_value="/assets/output/vod.mp4")):
            output = asyncio.run(main.canvas_video(payload))
        self.assertEqual(output["videos"], ["/assets/output/vod.mp4"])
        self.assertEqual(output["task_id"], "video-task")
        self.assertEqual(generate.call_args.kwargs["kind"], "video")


if __name__ == "__main__":
    unittest.main()
