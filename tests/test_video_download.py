import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
import video_download as vd

DATA = b"".join(i.to_bytes(4, "big") for i in range(4096))


class SlowStream(httpx.AsyncByteStream):
    def __init__(self, data, tracker, fail_after=None):
        self.data, self.tracker, self.fail_after = data, tracker, fail_after
        self.active = False

    async def __aiter__(self):
        self.active = True
        self.tracker["active"] += 1
        self.tracker["peak"] = max(self.tracker["peak"], self.tracker["active"])
        for offset in range(0, len(self.data), 1024):
            await asyncio.sleep(0.001)
            if self.fail_after is not None and offset >= self.fail_after:
                raise httpx.ReadError("simulated disconnect")
            yield self.data[offset:offset + 1024]

    async def aclose(self):
        if self.active:
            self.tracker["active"] -= 1
            self.active = False


class VideoDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def download(self, *, threshold=1024, data=DATA, fault="", etag='"version-a"', accept_ranges=True):
        requests, events = [], []
        tracker = {"active": 0, "peak": 0}
        interrupted = False

        async def handler(request):
            nonlocal interrupted
            requested_range = request.headers.get("Range", "")
            requests.append(dict(request.headers))
            first = len(requests) == 1
            headers = {"Content-Type": "video/mp4"}
            if etag:
                headers["ETag"] = etag
            if accept_ranges:
                headers["Accept-Ranges"] = "bytes"
            body = data
            status = 200
            fail_after = None
            if requested_range and fault != "ignore-range":
                start, end = map(int, requested_range[6:].split("-"))
                body = data[start:end + 1]
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                if fault == "wrong-range":
                    headers["Content-Range"] = f"bytes 0-{end}/{len(data)}"
                if fault == "changed-etag":
                    headers["ETag"] = '"version-b"'
                if fault == "overlong-range":
                    body += b"bad"
            if fault == "changed-etag" and not first and not requested_range:
                body = b"z" * len(data)
                headers["ETag"] = '"version-b"'
            headers["Content-Length"] = str(len(body))
            if fault == "disconnect-initial" and first:
                fail_after = 2048
            if fault == "disconnect-range" and requested_range and not interrupted:
                fail_after = 1024
                interrupted = True
            if fault == "short-range" and requested_range:
                body = body[:-10]
            return httpx.Response(status, headers=headers, stream=SlowStream(body, tracker, fail_after))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            def open_stream(extra):
                return client.stream("GET", "https://cdn.example.test/video.mp4", headers=extra)

            with tempfile.TemporaryDirectory() as folder, patch.object(vd, "PARALLEL_THRESHOLD", threshold):
                path = Path(folder) / "video.part"
                async with open_stream({}) as initial:
                    result = await vd.save_response(initial, open_stream, path, progress=lambda *args: events.append(args))
                saved = path.read_bytes()
        self.assertEqual(tracker["active"], 0, "all connections must be closed")
        return saved, result, requests, events, tracker

    async def test_parallel_byte_integrity_four_connections_and_exact_offsets(self):
        saved, result, requests, events, tracker = await self.download()
        self.assertEqual(saved, DATA)
        self.assertEqual(result, {"bytes": len(DATA), "mode": "parallel"})
        self.assertEqual(tracker["peak"], 4)
        self.assertEqual(len(requests), 4, "initial GET is reused, no HEAD / probe / merge download")
        self.assertEqual(sorted(r["range"] for r in requests[1:]), ["bytes=12288-16383", "bytes=4096-8191", "bytes=8192-12287"])
        self.assertTrue(all(r["if-range"] == '"version-a"' for r in requests[1:]))
        self.assertEqual(events[-1][:2], (len(DATA), len(DATA)))

    async def test_small_unknown_range_and_weak_validator_use_single_get(self):
        for kwargs in ({"threshold": len(DATA) + 1}, {"etag": ""}, {"etag": 'W/"weak"'}, {"accept_ranges": False}):
            with self.subTest(kwargs=kwargs):
                saved, result, requests, _, tracker = await self.download(**kwargs)
                self.assertEqual(saved, DATA)
                self.assertEqual(result["mode"], "single")
                self.assertEqual(len(requests), 1)
                self.assertEqual(tracker["peak"], 1)

    async def test_ignored_invalid_and_overlong_ranges_fall_back_without_corruption(self):
        for fault in ("ignore-range", "wrong-range", "overlong-range", "short-range"):
            with self.subTest(fault=fault):
                saved, result, requests, _, _ = await self.download(fault=fault)
                self.assertEqual(saved, DATA)
                self.assertEqual(result["mode"], "single")
                self.assertNotIn("range", requests[-1])

    async def test_changed_etag_discards_old_parts(self):
        saved, result, requests, _, _ = await self.download(fault="changed-etag")
        self.assertEqual(saved, b"z" * len(DATA))
        self.assertEqual(result["mode"], "single")

    async def test_interrupted_range_resumes_only_missing_bytes(self):
        saved, result, requests, _, _ = await self.download(fault="disconnect-range")
        self.assertEqual(saved, DATA)
        self.assertEqual(result["mode"], "parallel")
        self.assertEqual(len(requests), 5)
        self.assertTrue(any(r.get("range", "").startswith("bytes=5120-") for r in requests))

    async def test_interrupted_first_part_resumes_without_restarting_other_parts(self):
        saved, result, requests, _, _ = await self.download(fault="disconnect-initial")
        self.assertEqual(saved, DATA)
        self.assertEqual(result["mode"], "parallel")
        self.assertTrue(any(r.get("range") == "bytes=2048-4095" for r in requests))

    async def test_single_stream_resumes_and_does_not_double_count_bytes(self):
        saved, result, requests, events, _ = await self.download(fault="disconnect-initial", threshold=len(DATA) + 1)
        self.assertEqual(saved, DATA)
        self.assertEqual(result["mode"], "resuming")
        self.assertEqual(requests[-1]["range"], "bytes=2048-16383")
        self.assertEqual(events[-1][0], len(DATA))

    async def test_single_stream_without_validator_restarts_once(self):
        saved, result, requests, _, _ = await self.download(fault="disconnect-initial", etag="")
        self.assertEqual(saved, DATA)
        self.assertEqual(len(requests), 2)
        self.assertTrue(all("range" not in r for r in requests))

    async def test_html_oversized_and_unsolicited_partial_responses_are_rejected(self):
        for status, headers, max_bytes in (
            (200, {"Content-Type": "text/html"}, None),
            (200, {"Content-Length": "9000"}, 8000),
            (206, {"Content-Length": "10", "Content-Range": "bytes 0-9/100"}, None),
        ):
            response = httpx.Response(status, headers=headers, content=b"test", request=httpx.Request("GET", "https://example.test"))
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "part"
                with self.assertRaises(vd.InvalidVideoDownload):
                    await vd.save_response(response, None, path, max_bytes=max_bytes)
                self.assertFalse(path.exists())

    async def test_cancellation_drains_all_segment_writers(self):
        tracker = {"active": 0, "peak": 0}
        async def handler(request):
            headers = {"Content-Type": "video/mp4", "Accept-Ranges": "bytes", "ETag": '"a"'}
            start, end = 0, len(DATA) - 1
            status = 200
            if request.headers.get("range"):
                start, end = map(int, request.headers["range"][6:].split("-"))
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{len(DATA)}"
            headers["Content-Length"] = str(end - start + 1)
            return httpx.Response(status, headers=headers, stream=SlowStream(DATA[start:end + 1], tracker))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            def open_stream(extra):
                return client.stream("GET", "https://example.test/video.mp4", headers=extra)
            with tempfile.TemporaryDirectory() as folder, patch.object(vd, "PARALLEL_THRESHOLD", 1024):
                async with open_stream({}) as initial:
                    task = asyncio.create_task(vd.save_response(initial, open_stream, Path(folder) / "part"))
                    while tracker["peak"] < 4:
                        await asyncio.sleep(0)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertEqual(tracker["active"], 0)


if __name__ == "__main__":
    unittest.main()
