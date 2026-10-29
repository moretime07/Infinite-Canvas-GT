"""Bounded video transfers; no generation requests, credentials, or URL policy here.

The caller supplies a stream opener which applies its original authentication and
redirect/SSRF policy on EVERY request. Partial responses are combined only with a
strong ETag and exact Content-Range validation (RFC 9110 sections 13.1.5, 14).
"""
import asyncio
import re

import httpx

PARALLEL_THRESHOLD = 8 * 1024 * 1024
PARALLEL_MAX_BYTES = 512 * 1024 * 1024  # larger files still stream; do not preallocate an unbounded header value
PARALLEL_CONNECTIONS = 4
READ_TIMEOUT = 45.0


class InvalidVideoDownload(ValueError):
    pass


class RangeUnavailable(InvalidVideoDownload):
    pass


class IncompleteVideoDownload(httpx.TransportError):
    pass


def header(response, name):
    # Also accepts lightweight test adapters, not only httpx.Headers.
    return next((str(value).strip() for key, value in response.headers.items() if key.lower() == name.lower()), "")


def content_length(response):
    value = header(response, "content-length")
    return int(value) if value.isdigit() else None


def strong_etag(response):
    value = header(response, "etag")
    return value if re.fullmatch(r'"[^"\r\n]+"', value) else ""


def validate_video(response, max_bytes):
    response.raise_for_status()
    mime = header(response, "content-type").lower()
    if "text/" in mime or "json" in mime or "xml" in mime:
        raise InvalidVideoDownload("服务器未返回视频内容")
    size = content_length(response)
    if max_bytes and size is not None and size > max_bytes:
        raise InvalidVideoDownload("视频超过允许的下载大小")


async def close_response(response):
    close = getattr(response, "aclose", None)
    if close is not None:
        await close()


async def save_response(response, open_stream, path, *, progress=None, max_bytes=None):
    """Save an already opened GET. Small/non-range files incur no probe request.

    Four disjoint ranges write directly to one private .part file: no in-memory
    whole-video buffer and no second merge copy. The initial GET supplies the
    first range, then closes early. Cancellation drains all writers before the
    caller removes the part file. Only the caller publishes the completed file.
    """
    validate_video(response, max_bytes)
    if response.status_code != 200:
        raise InvalidVideoDownload("视频下载响应不完整")
    total = content_length(response)
    identity = header(response, "content-encoding").lower() in ("", "identity")
    if not identity:
        total = None  # aiter_bytes yields decoded bytes, not encoded Content-Length.
    etag = strong_etag(response)
    ranges = header(response, "accept-ranges").lower() == "bytes" and bool(etag) and identity
    received = 0
    mode = "single"

    def notify():
        if progress:
            progress(received, total, mode)

    def add(count):
        nonlocal received
        received += count
        if max_bytes and received > max_bytes:
            raise InvalidVideoDownload("视频超过允许的下载大小")
        notify()

    async def read_range(stream, start, end, *, initial=False):
        position = start
        with open(path, "r+b", buffering=0) as handle:
            handle.seek(start)
            try:
                async for chunk in stream.aiter_bytes():
                    if not chunk:
                        continue
                    remaining = end + 1 - position
                    if len(chunk) > remaining and not initial:
                        raise RangeUnavailable("分段长度不匹配")
                    chunk = chunk[:remaining]
                    handle.write(chunk)
                    position += len(chunk)
                    add(len(chunk))
                    if position == end + 1:
                        if initial:
                            break  # stop the original full GET after its first part
                        # Consume to EOF to reject an overlong range body.
                if position != end + 1:
                    raise IncompleteVideoDownload("视频下载提前中断")
            except httpx.TransportError as exc:
                exc.resume_position = position
                raise

    async def fetch_range(start, end):
        position = start
        for attempt in range(3):
            if position > end:
                return
            try:
                async with open_stream({"Range": f"bytes={position}-{end}", "If-Range": etag}) as part:
                    if part.status_code != 206:
                        raise RangeUnavailable("服务器不支持可靠的分段下载")
                    validate_video(part, max_bytes)
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", header(part, "content-range"))
                    if (not match or tuple(map(int, match.groups())) != (position, end, total)
                            or strong_etag(part) != etag
                            or header(part, "content-encoding").lower() not in ("", "identity")
                            or content_length(part) not in (None, end - position + 1)):
                        raise RangeUnavailable("视频分段校验失败")
                    await read_range(part, position, end)
                    return
            except httpx.TransportError as exc:
                position = getattr(exc, "resume_position", position)
                if attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))

    async def first_part(end):
        try:
            await read_range(response, 0, end, initial=True)
        except httpx.TransportError as exc:
            await close_response(response)
            await fetch_range(getattr(exc, "resume_position", 0), end)
        finally:
            await close_response(response)

    async def single(stream):
        nonlocal received, total, etag, ranges, mode
        validate_video(stream, max_bytes)
        if stream.status_code != 200:
            raise InvalidVideoDownload("普通下载未返回完整视频")
        total = content_length(stream) if header(stream, "content-encoding").lower() in ("", "identity") else None
        etag = strong_etag(stream)
        ranges = header(stream, "accept-ranges").lower() == "bytes" and bool(etag)
        received = 0
        mode = "single"
        notify()
        with open(path, "wb") as handle:
            async for chunk in stream.aiter_bytes():
                if total is not None and received + len(chunk) > total:
                    raise InvalidVideoDownload("视频长度不匹配")
                handle.write(chunk)
                add(len(chunk))
        if received <= 0 or (total is not None and received != total):
            raise IncompleteVideoDownload("视频下载提前中断")

    if ranges and total is not None and PARALLEL_THRESHOLD <= total <= PARALLEL_MAX_BYTES:
        mode = "parallel"
        notify()
        with open(path, "xb") as handle:
            handle.truncate(total)
        size = (total + PARALLEL_CONNECTIONS - 1) // PARALLEL_CONNECTIONS
        workers = [asyncio.create_task(first_part(min(size, total) - 1))]
        workers += [asyncio.create_task(fetch_range(start, min(start + size, total) - 1))
                    for start in range(size, total, size)]
        try:
            await asyncio.gather(*workers)
            return {"bytes": received, "mode": mode}
        except BaseException as exc:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            if not isinstance(exc, (RangeUnavailable, httpx.HTTPError)):
                raise
            # A server that ignores Range or changes its ETag must never be spliced.
            # Restart as one full response, only after every range writer stopped.
        async with open_stream({}) as full:
            await single(full)
        return {"bytes": received, "mode": mode}

    try:
        await single(response)
    except httpx.TransportError:
        await close_response(response)
        if ranges and total is not None and 0 < received < total:
            mode = "resuming"
            notify()
            try:
                await fetch_range(received, total - 1)
                return {"bytes": received, "mode": mode}
            except RangeUnavailable:
                pass
        # One ordinary GET retry, never a generation POST and never an endless loop.
        async with open_stream({}) as full:
            await single(full)
    return {"bytes": received, "mode": mode}
