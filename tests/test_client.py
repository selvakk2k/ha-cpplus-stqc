"""Regression test suite for CP PLUS STQC client using mock Digest NVR."""

import asyncio
import hashlib
import re
import pytest
from aiohttp import web

from custom_components.cpplus.client import CPPlusClient, TYPE_NVR
try:
    from custom_components.cpplus.client import CPPlusAuthError
except ImportError:
    # If not yet defined on unmodified code
    CPPlusAuthError = None


class MockDigestNVR:
    """Mock CP PLUS NVR server with Digest authentication."""

    def __init__(self, username: str = "admin", password: str = "correct_password") -> None:
        self.username = username
        self.password = password
        self.realm = "Login to CP PLUS"
        self.nonce = "testnonce123456"
        self.qop = "auth"
        self.opaque = "testopaque"
        self.request_counts: dict[str, int] = {}
        self.return_500_on_retry = False
        self.tripwire_reply = "OK\r\n"

    def _check_digest(self, request: web.Request) -> bool:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Digest "):
            return False

        fields = {}
        for part in re.findall(r'(\w+)=(?:"([^"]+)"|([^\s,]+))', auth_header[7:]):
            key = part[0]
            val = part[1] if part[1] else part[2]
            fields[key] = val

        if fields.get("username") != self.username:
            return False

        method = request.method
        uri = fields.get("uri", request.raw_path)

        ha1 = hashlib.md5(f"{self.username}:{self.realm}:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        nonce = fields.get("nonce", "")
        nc = fields.get("nc", "")
        cnonce = fields.get("cnonce", "")
        qop = fields.get("qop", "")

        expected = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
        return fields.get("response") == expected

    async def handle_request(self, request: web.Request) -> web.Response:
        path = request.raw_path
        self.request_counts[path] = self.request_counts.get(path, 0) + 1

        # Check authentication
        if not self._check_digest(request):
            challenge = (
                f'Digest realm="{self.realm}", qop="{self.qop}", nonce="{self.nonce}", opaque="{self.opaque}"'
            )
            return web.Response(status=401, headers={"WWW-Authenticate": challenge})

        if self.return_500_on_retry:
            return web.Response(status=500, text="<html>500 Internal Server Error</html>")

        if "action=getDeviceType" in path:
            return web.Response(text="type=CP-UNR-4K4322-V4\r\n")

        if "action=getSystemInfo" in path:
            return web.Response(text="serialNumber=REAL_NVR_SERIAL_12345\r\nappVersion=1.00.14.00.R\r\n")

        if "CrossLineDetection" in path:
            return web.Response(text=self.tripwire_reply)

        if "action=attach" in path:
            # Multipart stream simulation
            resp = web.StreamResponse(
                status=200,
                headers={"Content-Type": "multipart/x-mixed-replace;boundary=myboundary"},
            )
            await resp.prepare(request)
            await resp.write(b"--myboundary\r\nContent-Type: text/plain\r\n\r\nCode=VideoMotion;action=Start;index=0\r\n\r\n")
            await asyncio.sleep(0.05)
            await resp.write_eof()
            return resp

        return web.Response(text="OK\r\n")


@pytest.fixture
async def mock_nvr(aiohttp_server):
    nvr = MockDigestNVR()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", nvr.handle_request)
    server = await aiohttp_server(app)
    nvr.host = server.host
    nvr.port = server.port
    return nvr


@pytest.mark.asyncio
async def test_bug2_config_flow_rejects_wrong_password(mock_nvr):
    """Bug #2: Calling async_get_device_info with wrong password must raise auth error and NOT return fallback serial."""
    client = CPPlusClient(
        host=mock_nvr.host,
        port=mock_nvr.port,
        username="admin",
        password="WRONG_PASSWORD",
        use_ssl=False,
    )
    try:
        # We expect this to fail with authentication error
        if CPPlusAuthError is not None:
            with pytest.raises(CPPlusAuthError):
                await client.async_get_device_info()
        else:
            info = await client.async_get_device_info()
            # On buggy code: info["serial"] falls back to host IP string like "127_0_0_1"
            assert info.get("serial") != mock_nvr.host.replace(".", "_"), "Bug #2 present: wrong password returned fallback serial!"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bug3_ok_check_does_not_match_error_invalid_token(mock_nvr):
    """Bug #3: Response containing 'Error / Invalid Token' must not evaluate as True."""
    client = CPPlusClient(
        host=mock_nvr.host,
        port=mock_nvr.port,
        username="admin",
        password="correct_password",
        device_type=TYPE_NVR,
        use_ssl=False,
    )
    try:
        # Simulate server error containing 'token' (which contains 'ok')
        mock_nvr.tripwire_reply = "Error / Invalid Token\r\n"
        result = await client.async_set_tripwire(channel_idx=1, enable=True)
        assert result is False, "Bug #3 present: 'Error / Invalid Token' incorrectly evaluated to True!"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bug4_digest_retry_rejects_500_server_error(mock_nvr):
    """Bug #4: When Digest retry receives HTTP 500, it must raise ConnectionError, not return HTML body."""
    client = CPPlusClient(
        host=mock_nvr.host,
        port=mock_nvr.port,
        username="admin",
        password="correct_password",
        device_type=TYPE_NVR,
        use_ssl=False,
    )
    try:
        mock_nvr.return_500_on_retry = True
        with pytest.raises(ConnectionError):
            await client.async_nvr_request("/cgi-bin/test.cgi")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bug1_event_listener_halts_on_consecutive_401(mock_nvr):
    """Bug #1: Event listener must stop after consecutive 401s and not hammer the server."""
    client = CPPlusClient(
        host=mock_nvr.host,
        port=mock_nvr.port,
        username="admin",
        password="WRONG_PASSWORD",
        device_type=TYPE_NVR,
        use_ssl=False,
    )
    try:
        task = asyncio.create_task(client.async_start_event_listener(lambda ch, code, act: None))
        # Wait up to 0.5s for the listener to process
        await asyncio.sleep(0.3)
        client._stopped = True
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except Exception:
            pass

        # In buggy code: mock receives hundreds/thousands of requests in 0.3s
        attach_requests = sum(v for k, v in mock_nvr.request_counts.items() if "eventManager" in k)
        assert attach_requests <= 3, f"Bug #1 present: event listener hammered server with {attach_requests} requests!"
    finally:
        await client.close()
