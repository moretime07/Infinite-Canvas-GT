"""Host-local proxy preferences for generated-video transfers only.

Never changes environment variables, system proxy settings or generation clients.
The caller stores this file under the ignored runtime data directory.
"""
import ipaddress
import json
import os
import re
import tempfile
from contextvars import ContextVar
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
import httpx


_pinned_tls_target = ContextVar("download_proxy_pinned_tls_target", default=None)


class _SNIStream:
    def __init__(self, stream, proxy_tls_hostname=None):
        self.stream = stream
        self.proxy_tls_hostname = proxy_tls_hostname

    def __getattr__(self, name):
        return getattr(self.stream, name)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        target = _pinned_tls_target.get()
        if self.proxy_tls_hostname:
            # httpcore also forwards the origin SNI extension to HTTPS proxies.
            # The outer TLS layer must verify the PROXY, not the video CDN.
            server_hostname = self.proxy_tls_hostname
        elif target and server_hostname == target[0]:
            server_hostname = target[1]
        stream = await self.stream.start_tls(ssl_context=ssl_context, server_hostname=server_hostname, timeout=timeout)
        return _SNIStream(stream)


class _SNIBackend:
    def __init__(self, backend, proxy_tls_hostname=None):
        self.backend = backend
        self.proxy_tls_hostname = proxy_tls_hostname

    def __getattr__(self, name):
        return getattr(self.backend, name)

    async def connect_tcp(self, *args, **kwargs):
        return _SNIStream(await self.backend.connect_tcp(*args, **kwargs), self.proxy_tls_hostname)


class PinnedProxyTransport(httpx.AsyncHTTPTransport):
    """Preserve Host/TLS names while CONNECT still targets the validated IP.

    httpcore 1.x HTTP CONNECT ignores the sni_hostname extension (unlike its
    direct and SOCKS paths). Adapt only the TLS server name at the network layer;
    certificate verification remains enabled. ContextVar isolates parallel URLs.
    No keep-alive / HTTP2 reuse across distinct TLS authorities on the same IP.
    The narrowly scoped httpcore backend hook is covered by wire-level tests.
    """
    def __init__(self, proxy, *, verify=True):
        super().__init__(proxy=proxy, trust_env=False, verify=verify, http2=False,
                         limits=httpx.Limits(max_connections=4, max_keepalive_connections=0))
        parsed = urlsplit(proxy)
        self._pool._network_backend = _SNIBackend(self._pool._network_backend,
                                                parsed.hostname if parsed.scheme == "https" else None)

    async def handle_async_request(self, request):
        hostname = request.extensions.get("sni_hostname")
        if isinstance(hostname, bytes):
            hostname = hostname.decode("ascii")
        target = None
        if hostname:
            ipaddress.ip_address(request.url.host)  # this adapter accepts only IP-pinned requests
            target = (request.url.host, hostname)
        token = _pinned_tls_target.set(target)
        try:
            return await super().handle_async_request(request)
        finally:
            _pinned_tls_target.reset(token)


class DownloadProxySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = False
    protocol: Literal["http", "https", "socks5"] = "http"
    host: str = Field(default="127.0.0.1", max_length=253)
    port: int = Field(default=10808, ge=1, le=65535, strict=True)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value):
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1]
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
        # A host only, not a URL: rejects credentials, paths, query and CR/LF.
        if not value or not re.fullmatch(r"[a-zA-Z0-9.-]+", value):
            raise ValueError("只填写代理 IP 或主机名，不要填写协议、端口、路径或账号密码")
        if any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
               for label in value.split(".")):
            raise ValueError("代理主机名格式不正确")
        return value.lower()

    def proxy_url(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.protocol}://{host}:{self.port}"


def load_settings(path):
    try:
        return DownloadProxySettings.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DownloadProxySettings()
    # An invalid saved configuration must not silently bypass the selected proxy.


def save_settings(path, settings):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".download-proxy-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings.model_dump(), handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def client_options(settings, url=None):
    if not settings.enabled:
        return {}  # Preserve the download adapter's pre-existing environment policy.
    if url:
        hostname = (urlsplit(url).hostname or "").lower()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            return {"trust_env": False}
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return {"trust_env": False}
        except ValueError:
            pass
    return {"proxy": settings.proxy_url(), "trust_env": False}
