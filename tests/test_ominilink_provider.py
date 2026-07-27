import unittest
import main


class OminiLinkProviderTests(unittest.TestCase):
    def test_exact_api_hosts_are_recognized(self):
        for url in (
            "https://api.aig-ai.com/v1",
            "https://vg-api.aig-ai.com/v1",
            "https://api.ominilink.ai/v1",
            "https://vg-api.ominilink.ai/v1",
        ):
            with self.subTest(url=url):
                self.assertTrue(main.is_ominilink_api_url(url))

    def test_portal_and_lookalike_hosts_are_rejected(self):
        for url in (
            "https://portal.ominilink.ai/",
            "https://aig-ai.com.evil.test/v1",
            "https://notominilink.ai/v1",
        ):
            with self.subTest(url=url):
                self.assertFalse(main.is_ominilink_api_url(url))

    def test_video_host_migrates_to_dual_urls(self):
        provider = main.normalize_provider({
            "id": "orange",
            "name": "姗欏煙",
            "base_url": "https://vg-api.aig-ai.com/v1",
        })
        self.assertEqual(provider["base_url"], "https://api.aig-ai.com/v1")
        self.assertEqual(provider["video_base_url"], "https://vg-api.aig-ai.com/v1")

    def test_explicit_video_url_wins(self):
        provider = main.normalize_provider({
            "id": "orange",
            "base_url": "https://api.aig-ai.com/v1",
            "video_base_url": "https://video-proxy.example.test/v1",
        })
        self.assertEqual(provider["video_base_url"], "https://video-proxy.example.test/v1")
