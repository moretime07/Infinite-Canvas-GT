import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def provider(provider_id, **overrides):
    item = {
        "id": provider_id,
        "name": provider_id,
        "base_url": "https://example.test/v1",
        "enabled": True,
        "primary": False,
        "image_models": [],
        "chat_models": ["chat-model"],
        "video_models": [],
    }
    item.update(overrides)
    return item


class PrimaryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_sets_exactly_one_primary_without_key_payload(self):
        stored = [provider("old", primary=True), provider("next")]
        saved = []
        with patch.object(main, "load_api_providers", return_value=stored), patch.object(
            main, "provider_env_key_value", side_effect=lambda pid: "secret" if pid == "next" else "old-secret"
        ), patch.object(main, "save_api_providers", side_effect=lambda items: saved.extend(items)):
            result = await main.set_primary_api_provider("next")

        self.assertEqual([item["id"] for item in saved if item["primary"]], ["next"])
        self.assertEqual([item["id"] for item in stored if item["primary"]], ["old"])
        self.assertNotIn("api_key", str(result))

    async def test_rejects_unknown_disabled_unkeyed_and_modelless_candidates(self):
        cases = [
            ("missing", [provider("known")], "不存在"),
            ("disabled", [provider("disabled", enabled=False)], "停用"),
            ("unkeyed", [provider("unkeyed")], "密钥"),
            ("modelless", [provider("modelless", chat_models=[])], "模型"),
        ]
        for target, stored, message in cases:
            with self.subTest(target=target), patch.object(main, "load_api_providers", return_value=stored), patch.object(
                main, "provider_env_key_value", return_value="" if target == "unkeyed" else "secret"
            ), patch.object(main, "save_api_providers") as save:
                with self.assertRaises(HTTPException) as raised:
                    await main.set_primary_api_provider(target)
                self.assertIn(message, raised.exception.detail)
                save.assert_not_called()

    def test_runninghub_accepts_either_existing_runnable_key(self):
        item = provider("runninghub")
        with patch.object(main, "provider_env_key_value", return_value=""), patch.object(
            main, "runninghub_wallet_key_value", return_value="wallet-key"
        ):
            self.assertTrue(main.provider_has_primary_credential(item))


if __name__ == "__main__":
    unittest.main()
