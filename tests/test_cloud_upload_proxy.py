import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

import download_proxy as dp
import main


class UploadSettingsTests(unittest.TestCase):
    def test_legacy_config_preserves_download_and_does_not_enable_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "download-proxy.json"
            config.write_text('{"enabled":true,"protocol":"socks5","host":"localhost","port":7890}', encoding="utf-8")
            settings = dp.load_settings(config)
            self.assertTrue(settings.enabled)
            self.assertFalse(settings.upload_enabled)
            self.assertEqual(settings.proxy_url(), "socks5://localhost:7890")
            self.assertEqual(dp.upload_client_options(settings), {})

    def test_independent_switches_for_all_protocols(self):
        before = dict(os.environ)
        for protocol in ("http", "https", "socks5"):
            for download in (False, True):
                for upload in (False, True):
                    with self.subTest(protocol=protocol, download=download, upload=upload):
                        settings = dp.DownloadProxySettings(enabled=download, upload_enabled=upload, protocol=protocol, port=7890)
                        expected = {"proxy": f"{protocol}://127.0.0.1:7890", "trust_env": False}
                        self.assertEqual(dp.client_options(settings), expected if download else {})
                        self.assertEqual(dp.upload_client_options(settings), expected if upload else {})
                        self.assertEqual(settings.enabled, download)
        self.assertEqual(dict(os.environ), before)

    def test_local_upload_target_bypasses_external_proxy(self):
        settings = dp.DownloadProxySettings(upload_enabled=True)
        for host in ("localhost", "127.0.0.1", "192.168.2.2", "[::1]", "studio.local"):
            self.assertEqual(dp.upload_client_options(settings, f"http://{host}/upload"), {"trust_env": False})


class CloudUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.file = self.root / "synthetic.png"
        self.file.write_bytes(b"synthetic-upload-test-not-user-content")
        self.patch_data = patch.object(main, "DATA_DIR", self.folder.name)
        self.patch_data.start()
        self.addCleanup(self.patch_data.stop)
        self.settings = dp.DownloadProxySettings(upload_enabled=True, protocol="socks5", port=7890)
        dp.save_settings(self.root / "download-proxy.json", self.settings)
        self.factory = AsyncMock()
        self.client = self.factory.return_value.__aenter__.return_value
        self.client.post.return_value = httpx.Response(200, text="https://files.example.test/media.png\n")

    async def test_both_services_use_upload_proxy_and_preserve_multipart(self):
        for fn, service, field in ((main.upload_video_to_litterbox, "litterbox", "fileToUpload"),
                                   (main.upload_video_to_temp_sh, "temp.sh", "file")):
            with self.subTest(service=service), patch.object(main.httpx, "AsyncClient", return_value=self.factory.return_value) as constructor:
                result = await fn(str(self.file), "/assets/synthetic.png")
                options = constructor.call_args.kwargs
                self.assertEqual(options["proxy"], "socks5://127.0.0.1:7890")
                self.assertFalse(options["trust_env"])
                self.assertNotIn("headers", options)
                self.assertNotEqual(options.get("verify"), False)
                args = self.client.post.call_args.kwargs
                self.assertEqual(list(args["files"]), [field])
                self.assertEqual(args["files"][field][2], "image/png")
                self.assertTrue(args["files"][field][1].closed)
                if service == "litterbox":
                    self.assertEqual(args["data"]["reqtype"], "fileupload")
                self.assertEqual(result["url"], "https://files.example.test/media.png")
                self.assertEqual(result["source"], "/assets/synthetic.png")
                self.assertEqual(result["service"], service)

    async def test_disabled_upload_does_not_inherit_download_proxy(self):
        dp.save_settings(self.root / "download-proxy.json", dp.DownloadProxySettings(enabled=True))
        with patch.object(main.httpx, "AsyncClient", return_value=self.factory.return_value) as constructor:
            await main.upload_video_to_litterbox(str(self.file), "/assets/synthetic.png")
        self.assertNotIn("proxy", constructor.call_args.kwargs)
        self.assertNotIn("trust_env", constructor.call_args.kwargs)

    async def test_http_errors_are_actionable_and_never_echo_response(self):
        cases = {400: "格式", 401: "拒绝", 403: "拒绝", 404: "地址", 407: "认证",
                 413: "大小限制", 429: "频繁", 500: "异常", 502: "异常", 503: "异常"}
        for code, expected in cases.items():
            with self.subTest(code=code), patch.object(main.httpx, "AsyncClient", return_value=self.factory.return_value):
                self.client.post.return_value = httpx.Response(code, text="<!doctype html><html>PRIVATE_TOKEN signed-url</html>")
                with self.assertRaises(main.HTTPException) as caught:
                    await main.upload_video_to_litterbox(str(self.file), "/assets/synthetic.png")
            detail = caught.exception.detail
            self.assertEqual(caught.exception.status_code, code)
            self.assertIn(f"HTTP {code}", detail)
            self.assertIn(expected, detail)
            self.assertIn("已使用自定义素材上传代理", detail)
            self.assertNotIn("PRIVATE_TOKEN", detail)
            self.assertNotIn("<html", detail)

    async def test_disabled_proxy_error_explains_where_to_enable(self):
        dp.save_settings(self.root / "download-proxy.json", dp.DownloadProxySettings())
        self.client.post.return_value = httpx.Response(403, text="blocked")
        with patch.object(main.httpx, "AsyncClient", return_value=self.factory.return_value):
            with self.assertRaises(main.HTTPException) as caught:
                await main.upload_video_to_temp_sh(str(self.file), "/assets/synthetic.png")
        self.assertIn("未使用自定义素材上传代理", caught.exception.detail)
        self.assertIn("更多设置", caught.exception.detail)

    async def test_transport_failures_are_sanitized_and_not_retried_direct(self):
        for error, expected in ((httpx.ProxyError, "代理连接"), (httpx.ConnectError, "安全连接"),
                                (httpx.ConnectTimeout, "超时"), (httpx.WriteTimeout, "超时"),
                                (httpx.ReadTimeout, "超时"), (httpx.RemoteProtocolError, "传输异常")):
            self.client.post.reset_mock()
            self.client.post.side_effect = error("PRIVATE signed-url?key=PRIVATE")
            with self.subTest(error=error), patch.object(main.httpx, "AsyncClient", return_value=self.factory.return_value) as constructor:
                with self.assertRaises(main.HTTPException) as caught:
                    await main.upload_video_to_temp_sh(str(self.file), "/assets/synthetic.png")
                constructor.assert_called_once()
                self.client.post.assert_awaited_once()
            self.assertIn(expected, caught.exception.detail)
            self.assertIn("未切换直连", caught.exception.detail)
            self.assertNotIn("PRIVATE", caught.exception.detail)

    async def test_missing_socks_dependency_is_actionable(self):
        with patch.object(main.httpx, "AsyncClient", side_effect=ImportError("PRIVATE module path")):
            with self.assertRaises(main.HTTPException) as caught:
                await main.upload_video_to_litterbox(str(self.file), "/assets/synthetic.png")
        self.assertIn("SOCKS5", caught.exception.detail)
        self.assertNotIn("PRIVATE", caught.exception.detail)

    async def test_invalid_success_bodies_are_rejected_without_leaking_html_or_urls(self):
        for body in ("", "<!doctype html><p>PRIVATE</p>", '{"error":"PRIVATE"}',
                     "https://", "https://files.example:bad/PRIVATE", "https://user:PRIVATE@files.example/test",
                     "https://files.example/test\nPRIVATE", "https://files.example/<html>PRIVATE"):
            self.client.post.return_value = httpx.Response(200, text=body)
            with self.subTest(body=body), patch.object(main.httpx, "AsyncClient", return_value=self.factory.return_value):
                with self.assertRaises(main.HTTPException) as caught:
                    await main.upload_video_to_litterbox(str(self.file), "/assets/synthetic.png")
            self.assertIn("未返回有效素材直链", caught.exception.detail)
            self.assertNotIn("PRIVATE", caught.exception.detail)

    async def test_bad_config_fails_closed_before_network(self):
        (self.root / "download-proxy.json").write_text("invalid", encoding="utf-8")
        with patch.object(main.httpx, "AsyncClient") as constructor:
            with self.assertRaises(main.HTTPException) as caught:
                await main.upload_video_to_litterbox(str(self.file), "/assets/synthetic.png")
            constructor.assert_not_called()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("配置无法读取", caught.exception.detail)

    async def test_auto_fallback_preserves_proxy_and_errors_without_html(self):
        self.client.post.side_effect = [httpx.Response(403, text="<html>PRIVATE</html>"),
                                      httpx.Response(500, text="<html>PRIVATE</html>")]
        with patch.object(main, "local_media_path_for_cloud_upload", return_value=str(self.file)), patch.object(
            main.httpx, "AsyncClient", return_value=self.factory.return_value
        ) as constructor:
            with self.assertRaises(main.HTTPException) as caught:
                await main.upload_local_video_to_cloud("/assets/synthetic.png", "auto")
        self.assertEqual(constructor.call_count, 2)
        for call in constructor.call_args_list:
            self.assertEqual(call.kwargs["proxy"], self.settings.proxy_url())
            self.assertFalse(call.kwargs["trust_env"])
        self.assertIn("Litterbox", caught.exception.detail)
        self.assertIn("HTTP 403", caught.exception.detail)
        self.assertIn("Temp.sh", caught.exception.detail)
        self.assertIn("HTTP 500", caught.exception.detail)
        self.assertNotIn("PRIVATE", caught.exception.detail)

    async def test_explicit_service_does_not_fall_back_to_undisclosed_host(self):
        self.client.post.return_value = httpx.Response(403, text="blocked")
        with patch.object(main, "local_media_path_for_cloud_upload", return_value=str(self.file)), patch.object(
            main.httpx, "AsyncClient", return_value=self.factory.return_value
        ) as constructor, patch.object(main, "upload_video_to_temp_sh", new_callable=AsyncMock) as fallback:
            with self.assertRaises(main.HTTPException):
                await main.upload_local_video_to_cloud("/assets/synthetic.png", "litterbox")
        constructor.assert_called_once()
        fallback.assert_not_awaited()

    async def test_external_link_is_not_reuploaded(self):
        with patch.object(main.httpx, "AsyncClient") as constructor:
            result = await main.upload_local_video_to_cloud("https://files.example/test.mp4")
        self.assertEqual(result["service"], "existing")
        constructor.assert_not_called()

    async def test_real_http_and_socks_proxy_receive_synthetic_upload(self):
        for protocol in ("http", "socks5"):
            seen = []
            async def proxy(reader, writer):
                try:
                    if protocol == "socks5":
                        self.assertEqual(await reader.readexactly(3), b"\x05\x01\x00")
                        writer.write(b"\x05\x00")
                        await writer.drain()
                        self.assertEqual(await reader.readexactly(4), b"\x05\x01\x00\x03")
                        size = (await reader.readexactly(1))[0]
                        self.assertEqual(await reader.readexactly(size), b"upload.example.test")
                        self.assertEqual(await reader.readexactly(2), b"\x00P")
                        writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                        await writer.drain()
                    headers = await reader.readuntil(b"\r\n\r\n")
                    size = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                                if line.lower().startswith(b"content-length:"))
                    seen.append((headers, await reader.readexactly(size)))
                    response = b"https://files.example.test/test.png"
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(response)).encode() +
                                 b"\r\nConnection: close\r\n\r\n" + response)
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
            server = await asyncio.start_server(proxy, "127.0.0.1", 0)
            try:
                settings = dp.DownloadProxySettings(upload_enabled=True, protocol=protocol,
                                                    port=server.sockets[0].getsockname()[1])
                dp.save_settings(self.root / "download-proxy.json", settings)
                with patch.dict(os.environ, {"TEMP_SH_UPLOAD_URL": "http://upload.example.test/upload",
                                             "HTTP_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1"}):
                    result = await asyncio.wait_for(main.upload_video_to_temp_sh(str(self.file), "/assets/synthetic.png"), timeout=5)
                self.assertEqual(result["url"], "https://files.example.test/test.png")
                self.assertEqual(len(seen), 1)
                self.assertIn(b"multipart/form-data", seen[0][0])
                self.assertIn(b"synthetic-upload-test-not-user-content", seen[0][1])
                self.assertNotIn(b"Authorization:", seen[0][0])
            finally:
                server.close()
                await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
