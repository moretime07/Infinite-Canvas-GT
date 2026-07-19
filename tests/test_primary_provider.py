import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic.warnings import PydanticDeprecatedSince20

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

    async def test_switch_sets_exactly_one_primary_with_duplicate_stored_ids(self):
        stored = [provider("duplicate", primary=True), provider("duplicate"), provider("other")]
        saved = []
        with patch.object(main, "load_api_providers", return_value=stored), patch.object(
            main, "provider_env_key_value", return_value="secret"
        ), patch.object(main, "save_api_providers", side_effect=lambda items: saved.extend(items)):
            await main.set_primary_api_provider("duplicate")

        self.assertEqual(sum(bool(item["primary"]) for item in saved), 1)

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

    def test_runninghub_accepts_standard_key_only(self):
        item = provider("runninghub")
        with patch.object(main, "provider_env_key_value", return_value="standard-key"), patch.object(
            main, "runninghub_wallet_key_value", return_value=""
        ):
            self.assertTrue(main.provider_has_primary_credential(item))

    def test_runninghub_accepts_wallet_key_only(self):
        item = provider("runninghub")
        with patch.object(main, "provider_env_key_value", return_value=""), patch.object(
            main, "runninghub_wallet_key_value", return_value="wallet-key"
        ):
            self.assertTrue(main.provider_has_primary_credential(item))

    def test_runninghub_rejects_when_neither_key_exists(self):
        item = provider("runninghub")
        with patch.object(main, "provider_env_key_value", return_value=""), patch.object(
            main, "runninghub_wallet_key_value", return_value=""
        ):
            self.assertFalse(main.provider_has_primary_credential(item))

    def test_transition_rejects_removing_or_disabling_current_primary(self):
        current = [provider("current", primary=True), provider("other")]
        for proposed in (
            [provider("other")],
            [provider("current", enabled=False), provider("other")],
            [provider("current", primary=False), provider("other")],
        ):
            with self.subTest(proposed=proposed), self.assertRaises(HTTPException) as raised:
                main.validate_primary_transition(current, proposed)
            self.assertIn("先设置另一个默认供应商", raised.exception.detail)

    def test_transition_accepts_explicit_eligible_replacement(self):
        current = [provider("current", primary=True), provider("other")]
        proposed = [provider("current", enabled=False), provider("other", primary=True)]
        with patch.object(main, "provider_env_key_value", return_value="secret"):
            main.validate_primary_transition(current, proposed)

    def test_transition_uses_submitted_credential_for_replacement(self):
        current = [provider("current", primary=True), provider("other")]
        proposed = [provider("current", enabled=False), provider("other", primary=True)]
        with patch.object(main, "provider_env_key_value", return_value=""):
            main.validate_primary_transition(current, proposed, {"other": "new-secret"})

    def test_transition_rejects_clearing_current_primary_credential(self):
        current = [provider("current", primary=True), provider("other")]
        proposed = [provider("current", primary=True), provider("other")]
        with patch.object(main, "provider_env_key_value", return_value="stored-secret"), self.assertRaises(
            HTTPException
        ) as raised:
            main.validate_primary_transition(current, proposed, {"current": ""})
        self.assertIn("先设置另一个默认供应商", raised.exception.detail)

    def test_transition_rejects_current_primary_becoming_modelless(self):
        current = [provider("current", primary=True), provider("other")]
        for proposed_models in ([], None):
            proposed = [provider("current", primary=True, chat_models=proposed_models), provider("other")]
            with self.subTest(proposed_models=proposed_models):
                with patch.object(main, "provider_env_key_value", return_value="stored-secret"), self.assertRaises(
                    HTTPException
                ) as raised:
                    main.validate_primary_transition(current, proposed)
                self.assertIn("先设置另一个默认供应商", raised.exception.detail)

    def test_transition_allows_unrelated_edit_to_eligible_current_primary(self):
        current = [provider("current", primary=True), provider("other")]
        proposed = [provider("current", primary=True, name="Renamed"), provider("other")]
        with patch.object(main, "provider_env_key_value", return_value="stored-secret"):
            main.validate_primary_transition(current, proposed)

    async def test_full_save_keeps_submitted_primary_flag(self):
        payload = [
            main.ApiProviderPayload(id="one", name="One", enabled=True, primary=True, chat_models=["chat"]),
            main.ApiProviderPayload(id="two", name="Two", enabled=True, primary=False, chat_models=["chat"]),
        ]
        saved = []
        with patch.object(main, "load_api_providers", return_value=[provider("one", primary=True), provider("two")]), patch.object(
            main, "provider_env_key_value", return_value="secret"
        ), patch.object(main, "save_api_providers", side_effect=lambda items: saved.extend(items)), patch.object(
            main, "update_env_values"
        ), patch.object(main, "reload_env_globals"):
            await main.save_providers(payload)
        self.assertEqual([item["id"] for item in saved if item["primary"]], ["one"])

    async def test_full_save_rejects_clearing_current_primary_key_before_writes(self):
        payload = [
            main.ApiProviderPayload(
                id="one", name="One", enabled=True, primary=True, chat_models=["chat"], clear_key=True
            ),
            main.ApiProviderPayload(id="two", name="Two", enabled=True, primary=False, chat_models=["chat"]),
        ]
        with patch.object(main, "load_api_providers", return_value=[provider("one", primary=True), provider("two")]), patch.object(
            main, "provider_env_key_value", return_value="stored-secret"
        ), patch.object(main, "save_api_providers") as save, patch.object(main, "update_env_values") as update:
            with self.assertRaises(HTTPException):
                await main.save_providers(payload)
        save.assert_not_called()
        update.assert_not_called()

    async def test_full_save_accepts_replacement_with_newly_submitted_credential(self):
        payload = [
            main.ApiProviderPayload(id="one", name="One", enabled=False, primary=False, chat_models=["chat"]),
            main.ApiProviderPayload(
                id="two", name="Two", enabled=True, primary=True, chat_models=["chat"], api_key="new-secret"
            ),
        ]
        events = []
        with patch.object(main, "load_api_providers", return_value=[provider("one", primary=True), provider("two")]), patch.object(
            main, "provider_env_key_value", return_value=""
        ), patch.object(main, "save_api_providers", side_effect=lambda items: events.append(("save", items))), patch.object(
            main, "update_env_values", side_effect=lambda values: events.append(("env", values))
        ), patch.object(main, "reload_env_globals", side_effect=lambda: events.append(("reload", None))):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                await main.save_providers(payload)

        self.assertEqual([name for name, _ in events], ["save", "env", "reload"])
        self.assertEqual([item["id"] for item in events[0][1] if item["primary"]], ["two"])
        self.assertEqual(events[1][1][main.provider_key_env("two")], "new-secret")
        self.assertFalse([warning for warning in caught if issubclass(warning.category, PydanticDeprecatedSince20)])

    async def test_full_save_accepts_replacement_while_clearing_old_primary_credential(self):
        payload = [
            main.ApiProviderPayload(
                id="one", name="One", enabled=True, primary=False, chat_models=["chat"], clear_key=True
            ),
            main.ApiProviderPayload(id="two", name="Two", enabled=True, primary=True, chat_models=["chat"]),
        ]
        saved = []
        env_updates = []
        with patch.object(main, "load_api_providers", return_value=[provider("one", primary=True), provider("two")]), patch.object(
            main, "provider_env_key_value", side_effect=lambda provider_id: "stored-two" if provider_id == "two" else "stored-one"
        ), patch.object(main, "save_api_providers", side_effect=lambda items: saved.extend(items)), patch.object(
            main, "update_env_values", side_effect=lambda values: env_updates.append(values)
        ), patch.object(main, "reload_env_globals"):
            await main.save_providers(payload)

        self.assertEqual([item["id"] for item in saved if item["primary"]], ["two"])
        self.assertEqual(env_updates[0][main.provider_key_env("one")], "")

    async def test_full_save_validates_modelless_primary_before_any_write(self):
        payload = [
            main.ApiProviderPayload(id="one", name="One", enabled=True, primary=True, chat_models=[]),
            main.ApiProviderPayload(id="two", name="Two", enabled=True, primary=False, chat_models=["chat"]),
        ]
        with patch.object(main, "load_api_providers", return_value=[provider("one", primary=True), provider("two")]), patch.object(
            main, "provider_env_key_value", return_value="stored-secret"
        ), patch.object(main, "save_api_providers") as save, patch.object(
            main, "update_env_values"
        ) as update, patch.object(main, "reload_env_globals") as reload_globals:
            with self.assertRaises(HTTPException):
                await main.save_providers(payload)

        save.assert_not_called()
        update.assert_not_called()
        reload_globals.assert_not_called()


if __name__ == "__main__":
    unittest.main()
