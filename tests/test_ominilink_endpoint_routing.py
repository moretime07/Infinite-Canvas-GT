import unittest
from unittest.mock import AsyncMock, patch

import main
from test_ominilink_video import FakeResponse, RecordingClient


CHAT = "https://api.aig-ai.com/v1"
VIDEO = "https://vg-api.aig-ai.com/v1"
TASKS = "https://vg-api.aig-ai.com/v3/bytedance/api/v3/contents/generations/tasks"
PREFIXES = ("/v3/bytedance", "/v3/bytedance/api/v3",
            "/v3/bytedance/api/v3/contents/generations/tasks")


class OminiLinkEndpointRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_task_address_in_general_field_does_not_poison_model_discovery(self):
        for prefix in PREFIXES:
            with self.subTest(prefix=prefix):
                provider = main.normalize_provider({
                    "id": "orange", "base_url": "https://vg-api.aig-ai.com" + prefix,
                    "protocol": "openai", "chat_models": ["chat-test"],
                })
                self.assertEqual(provider["base_url"], CHAT)
                self.assertEqual(provider["video_base_url"], VIDEO)
                self.assertEqual(main.ominilink_chat_api_url(provider), CHAT)
                self.assertEqual(provider["chat_models"], ["chat-test"])

    def test_explicit_bytedance_video_endpoint_only_affects_seedance(self):
        for prefix in PREFIXES:
            with self.subTest(prefix=prefix):
                provider = {"base_url": TASKS, "video_base_url": "https://vg-api.aig-ai.com" + prefix}
                self.assertEqual(main.ominilink_chat_api_url(provider), CHAT)
                self.assertEqual(main.ominilink_video_api_root(provider), VIDEO)
                self.assertEqual(main.ominilink_seedance_api_root(provider) + "/contents/generations/tasks", TASKS)

    def test_chat_host_alone_derives_video_host(self):
        provider = {"base_url": CHAT}
        self.assertEqual(main.ominilink_video_api_root(provider), VIDEO)
        self.assertEqual(main.ominilink_seedance_api_root(provider) + "/contents/generations/tasks", TASKS)

    def test_unrelated_hosts_and_explicit_proxy_are_not_rewritten(self):
        unrelated = "https://example.test/v3/bytedance/api/v3"
        self.assertEqual(main.normalize_ominilink_urls(unrelated), (unrelated, ""))
        self.assertEqual(main.normalize_ominilink_urls(CHAT, unrelated), (CHAT, unrelated))
        self.assertEqual(main.ominilink_video_api_root({"base_url": CHAT, "video_base_url": unrelated}), unrelated)

    async def test_unsaved_form_fetches_models_from_chat_endpoint(self):
        fetch_models = AsyncMock(return_value={"all": ["seedance-2.0"]})
        with patch.object(main, "api_key_from_payload", return_value="test-key"), \
                patch.object(main, "fetch_models_from_upstream", new=fetch_models):
            result = await main.fetch_upstream_models_from_payload(main.TestConnectionPayload(
                base_url=TASKS, video_base_url="https://vg-api.aig-ai.com/v3/bytedance",
                provider_id="orange", protocol="openai",
            ))
        fetch_models.assert_awaited_once_with(CHAT, "test-key", "openai", "openai")
        self.assertEqual(result["all"], ["seedance-2.0"])

    async def test_unavailable_task_route_does_not_retry_submission(self):
        client = RecordingClient([FakeResponse(404, text="<!doctype html><html>private trace</html>")])
        with patch.object(main, "provider_env_key_value", return_value="test-key"):
            with self.assertRaises(main.HTTPException) as raised:
                await main.generate_ominilink_video(main.CanvasVideoRequest(
                    prompt="test", model="seedance-2.0",
                ), {"id": "orange", "base_url": CHAT}, client=client)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertIn("海外 Seedance 通道是否开放", raised.exception.detail)
        self.assertNotIn("private trace", raised.exception.detail)
        self.assertNotIn("test-key", raised.exception.detail)
        self.assertEqual(len(client.calls), 1)

    async def test_complete_request_and_poll_routes_with_screenshot_configuration(self):
        provider = {"id": "orange", "base_url": TASKS,
                    "video_base_url": "https://vg-api.aig-ai.com/v3/bytedance"}
        for model in ("seedance-2.0", "dreamina-seedance-2-0-260128", "gemini-omni-flash-preview"):
            with self.subTest(model=model):
                seedance = model != "gemini-omni-flash-preview"
                result_media = ({"content": {"video_url": "https://cdn.example.test/result.mp4"}}
                                if seedance else {"steps": [{"type": "model_output", "content": [
                                    {"type": "video", "uri": "https://cdn.example.test/result.mp4"}]}]})
                client = RecordingClient(
                    [FakeResponse(200, {"id": "task-test", "status": "queued"})],
                    [FakeResponse(200, {"id": "task-test", "status": "succeeded", **result_media})],
                )
                with patch.object(main, "provider_env_key_value", return_value="test-key"), \
                        patch.object(main.asyncio, "sleep", new=AsyncMock()), \
                        patch.object(main, "save_ominilink_video_to_output", new=AsyncMock(return_value="/assets/output/test.mp4")):
                    result = await main.generate_ominilink_video(main.CanvasVideoRequest(
                        prompt="test", model=model, aspect_ratio="9:16", resolution="720p", duration=6,
                    ), provider, client=client)
                self.assertEqual(result["videos"], ["/assets/output/test.mp4"])
                self.assertEqual([entry[0] for entry in client.calls], ["POST", "GET"])
                self.assertEqual(client.calls[0][1], TASKS if seedance else VIDEO + "/" + model)
                self.assertEqual(client.calls[1][1], TASKS + "/task-test" if seedance else VIDEO + "/query/" + model + "/task-test")
                body = client.calls[0][2]["json"]
                if seedance:
                    self.assertEqual(body["ratio"], "9:16")
                    self.assertEqual(body["resolution"], "720p")
                else:
                    self.assertEqual(body["response_format"]["aspect_ratio"], "9:16")


if __name__ == "__main__":
    unittest.main()
