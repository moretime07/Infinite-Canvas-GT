import asyncio
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

import download_proxy as dp
import main


class SettingsTests(unittest.TestCase):
    def test_validation_and_urls(self):
        self.assertFalse(dp.DownloadProxySettings().enabled)
        for protocol in ("http", "https", "socks5"):
            setting = dp.DownloadProxySettings(enabled=True, protocol=protocol, host="[::1]", port=10808)
            self.assertEqual(setting.proxy_url(), f"{protocol}://[::1]:10808")
        for changes in ({"port":0}, {"port":65536}, {"port":True}, {"port":"7890"},
                        {"enabled":"yes"}, {"protocol":"socks4"}, {"password":"secret"},
                        *({"host":host} for host in ("", "http://127.0.0.1", "user:secret@localhost", "a/b", "a\nb", "-bad.test", "a..b"))):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                dp.DownloadProxySettings(**changes)

    def test_runtime_persistence_and_invalid_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data" / "download-proxy.json"
            self.assertFalse(dp.load_settings(path).enabled)
            setting = dp.DownloadProxySettings(enabled=True, protocol="socks5", port=7890)
            dp.save_settings(path, setting)
            self.assertEqual(dp.load_settings(path), setting)
            self.assertEqual(len(list(path.parent.iterdir())), 1)
            path.write_text("invalid", encoding="utf-8")
            with self.assertRaises(ValueError):
                dp.load_settings(path)

    def test_proxy_scope_no_environment_mutation_and_private_hosts_direct(self):
        before = dict(os.environ)
        self.assertEqual(dp.client_options(dp.DownloadProxySettings()), {})
        setting = dp.DownloadProxySettings(enabled=True)
        self.assertEqual(dp.client_options(setting, "https://cdn.example/video"),
                         {"proxy":"http://127.0.0.1:10808", "trust_env":False})
        for host in ("localhost", "127.0.0.1", "192.168.2.2", "[::1]", "studio.local"):
            self.assertEqual(dp.client_options(setting, f"http://{host}/video"), {"trust_env":False})
        self.assertEqual(dict(os.environ), before)


HEADERS = {"Origin":"http://127.0.0.1:3000", "Sec-Fetch-Site":"same-origin",
           "X-Infinite-Canvas-Download-Proxy":"1"}


class SettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patcher = patch.object(main, "DATA_DIR", self.folder.name)
        self.patcher.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 50000)),
                                       base_url="http://127.0.0.1:3000")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patcher.stop()
        self.folder.cleanup()

    async def test_save_reload_and_csrf_rejection(self):
        url = "/api/settings/download-proxy"
        payload = dp.DownloadProxySettings(enabled=True, protocol="socks5", port=7890).model_dump()
        self.assertFalse((await self.client.get(url)).json()["settings"]["enabled"])
        for headers in ({}, {**HEADERS, "Origin":"https://evil.test"}, {**HEADERS, "Sec-Fetch-Site":"cross-site"}):
            self.assertEqual((await self.client.put(url, json=payload, headers=headers)).status_code, 403)
        self.assertFalse(list(Path(self.folder.name).iterdir()))
        saved = await self.client.put(url, json=payload, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual((await self.client.get(url)).json()["settings"], payload)
        bad = await self.client.put(url, json={**payload, "port":65536}, headers=HEADERS)
        self.assertEqual(bad.status_code, 422)
        self.assertEqual((await self.client.get(url)).json()["settings"], payload)

    async def test_lan_client_cannot_read_or_change_host_proxy(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("192.168.2.9", 50000)),
                                     base_url="http://127.0.0.1:3000") as remote:
            self.assertEqual((await remote.get("/api/settings/download-proxy")).json(), {"can_edit":False, "settings":None})
            response = await remote.put("/api/settings/download-proxy", json=dp.DownloadProxySettings().model_dump(), headers=HEADERS)
            self.assertEqual(response.status_code, 403)

    async def test_probe_is_fixed_no_credentials_and_does_not_save(self):
        factory = AsyncMock()
        http = factory.return_value.__aenter__.return_value
        http.head.return_value = httpx.Response(204)
        with patch.object(main.httpx, "AsyncClient", return_value=factory.return_value) as constructor:
            result = await self.client.post("/api/settings/download-proxy/test", headers=HEADERS,
                                            json=dp.DownloadProxySettings(enabled=True).model_dump())
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["ok"])
        self.assertEqual(constructor.call_args.kwargs["proxy"], "http://127.0.0.1:10808")
        http.head.assert_awaited_once_with("https://www.gstatic.com/generate_204")
        self.assertNotIn("headers", constructor.call_args.kwargs)
        self.assertFalse(list(Path(self.folder.name).iterdir()))

    async def test_probe_failure_is_sanitized(self):
        factory = AsyncMock()
        factory.return_value.__aenter__.return_value.head.side_effect = httpx.ConnectError("PRIVATE proxy details")
        with patch.object(main.httpx, "AsyncClient", return_value=factory.return_value):
            response = await self.client.post("/api/settings/download-proxy/test", headers=HEADERS,
                                              json=dp.DownloadProxySettings(enabled=True).model_dump())
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("PRIVATE", response.text)


class DownloadRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_socks5_client_uses_configured_port(self):
        seen = []
        async def proxy(reader, writer):
            try:
                self.assertEqual(await reader.readexactly(3), b"\x05\x01\x00")
                writer.write(b"\x05\x00")
                await writer.drain()
                self.assertEqual(await reader.readexactly(4), b"\x05\x01\x00\x03")
                size = (await reader.readexactly(1))[0]
                seen.append((await reader.readexactly(size)).decode())
                self.assertEqual(await reader.readexactly(2), b"\x00P")
                writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\ndata")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        try:
            settings = dp.DownloadProxySettings(enabled=True, protocol="socks5", port=server.sockets[0].getsockname()[1])
            async with httpx.AsyncClient(**dp.client_options(settings), timeout=3) as client:
                result = await client.get("http://cdn.example.test/video")
            self.assertEqual(result.content, b"data")
            self.assertEqual(seen, ["cdn.example.test"])
        finally:
            server.close()
            await server.wait_closed()

    async def test_generic_video_reaches_http_proxy_and_saves(self):
        seen = []
        async def proxy(reader, writer):
            try:
                seen.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\nContent-Length: 4\r\nConnection: close\r\n\r\ndata")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(main, "DATA_DIR", folder), patch.object(
                main, "output_path_for", side_effect=lambda name, category: str(Path(folder) / name)
            ):
                dp.save_settings(Path(folder) / "download-proxy.json", dp.DownloadProxySettings(enabled=True, port=port))
                result = await main.save_remote_video_to_output("http://cdn.example.test/movie.mp4")
                self.assertTrue(result.startswith(("/output/", "/assets/")))
                self.assertEqual(next(Path(folder).glob("*.mp4")).read_bytes(), b"data")
                self.assertIn(b"GET http://cdn.example.test/movie.mp4 HTTP/1.1", seen[0])
        finally:
            server.close()
            await server.wait_closed()

    async def test_proxy_failure_does_not_fall_back_to_signed_url(self):
        with patch.object(main, "video_download_client_options", return_value={"proxy":"http://127.0.0.1:1", "trust_env":False}), patch.object(
            main.httpx, "AsyncClient", side_effect=httpx.ConnectError("secret-signed-url")
        ):
            with self.assertRaises(main.HTTPException) as ctx:
                await main.save_remote_video_to_output("https://cdn.example/video?token=secret-signed-url")
        self.assertIn("代理", ctx.exception.detail)
        self.assertNotIn("secret-signed-url", ctx.exception.detail)

    async def test_protected_downloader_uses_proxy_transport(self):
        settings = dp.DownloadProxySettings(enabled=True)
        with tempfile.TemporaryDirectory() as folder, patch.object(main, "DATA_DIR", folder):
            dp.save_settings(Path(folder) / "download-proxy.json", settings)
            with patch.object(dp, "PinnedProxyTransport", side_effect=RuntimeError("proxy constructor reached")) as constructor:
                with self.assertRaises(main.HTTPException):
                    await main.save_ominilink_video_to_output("https://cdn.example/video")
            constructor.assert_called_once_with(settings.proxy_url())


class FakeStream:
    def __init__(self):
        self.responses = [b"HTTP/1.1 200 Connection established\r\n\r\n",
                          b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ndata"]
        self.writes = []
        self.tls = []

    async def read(self, max_bytes, timeout=None):
        return self.responses.pop(0)

    async def write(self, data, timeout=None):
        self.writes.append(data)

    async def aclose(self):
        pass

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls.append((server_hostname, ssl_context.check_hostname, ssl_context.verify_mode))
        return self

    def get_extra_info(self, name):
        return None


class PinnedTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_connect_preserves_pinned_ip_and_verified_original_tls_name(self):
        # Exercise real httpx/httpcore CONNECT serialization, with fake TCP/TLS I/O.
        for protocol in ("http", "https"):
            stream = FakeStream()
            backend = AsyncMock()
            backend.connect_tcp.return_value = stream
            transport = dp.PinnedProxyTransport(f"{protocol}://proxy.example:10808")
            transport._pool._network_backend = dp._SNIBackend(backend, "proxy.example" if protocol == "https" else None)
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.get("https://93.184.216.34/video", headers={"Host":"cdn.example.test"},
                                            extensions={"sni_hostname":"cdn.example.test"})
            self.assertEqual(response.content, b"data")
            self.assertIn(b"CONNECT 93.184.216.34:443 HTTP/1.1", stream.writes[0])
            self.assertEqual(stream.tls[-1], ("cdn.example.test", True, ssl.CERT_REQUIRED))
            if protocol == "https":
                self.assertEqual(stream.tls[0], ("proxy.example", True, ssl.CERT_REQUIRED))
            self.assertIsNone(dp._pinned_tls_target.get())


if __name__ == "__main__":
    unittest.main()
