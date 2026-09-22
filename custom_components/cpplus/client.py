"""Client communication interface for CP PLUS STQC cameras and NVRs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import ssl
import urllib.parse
from datetime import datetime
from typing import Any, Callable
import aiohttp

from .const import (
    STREAM_PROFILE_DAHUA_CH0,
    STREAM_PROFILE_DAHUA_CH1,
    STREAM_PROFILE_LIVE,
    STREAM_PROFILE_ONVIF,
    STREAM_PROFILE_VIDEO_LIVE,
    TYPE_CAMERA,
    TYPE_NVR,
)

_LOGGER = logging.getLogger(__name__)


class CPPlusError(Exception):
    """Base exception for CP PLUS STQC client."""


class CPPlusAuthError(CPPlusError):
    """Authentication failed."""


class CPPlusConnectionError(CPPlusError):
    """Connection to device failed."""


class CPPlusBusyError(CPPlusError):
    """Device or session is busy (e.g. 486 Busy Here)."""


class AsyncDigestAuth:
    """Helper to perform HTTP Digest Authentication with aiohttp."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.realm: str | None = None
        self.nonce: str | None = None
        self.qop: str | None = None
        self.opaque: str | None = None
        self.nc = 0

    def parse_challenge(self, header: str) -> None:
        realm_m = re.search(r'realm="([^"]+)"', header)
        if realm_m:
            self.realm = realm_m.group(1)
        nonce_m = re.search(r'nonce="([^"]+)"', header)
        if nonce_m:
            new_nonce = nonce_m.group(1)
            if new_nonce != self.nonce:
                self.nonce = new_nonce
                self.nc = 0
        qop_m = re.search(r'qop="?([^",\s]+)"?', header)
        self.qop = qop_m.group(1) if qop_m else None
        opaque_m = re.search(r'opaque="([^"]*)"', header)
        self.opaque = opaque_m.group(1) if opaque_m else None

    def build_header(self, method: str, uri: str) -> str:
        self.nc += 1
        nc_str = f"{self.nc:08x}"
        cnonce = os.urandom(8).hex()
        ha1 = hashlib.md5(f"{self.username}:{self.realm}:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        if self.qop:
            resp = hashlib.md5(f"{ha1}:{self.nonce}:{nc_str}:{cnonce}:{self.qop}:{ha2}".encode()).hexdigest()
            header = (
                f'Digest username="{self.username}", realm="{self.realm}", '
                f'nonce="{self.nonce}", uri="{uri}", response="{resp}", '
                f'qop={self.qop}, nc={nc_str}, cnonce="{cnonce}"'
            )
        else:
            resp = hashlib.md5(f"{ha1}:{self.nonce}:{ha2}".encode()).hexdigest()
            header = (
                f'Digest username="{self.username}", realm="{self.realm}", '
                f'nonce="{self.nonce}", uri="{uri}", response="{resp}"'
            )
        if self.opaque:
            header += f', opaque="{self.opaque}"'
        return header


class CPPlusClient:
    """Async client for CP PLUS STQC cameras and NVRs."""

    def __init__(
        self,
        hass=None,
        host: str = "",
        port: int = 443,
        rtsp_port: int = 554,
        username: str = "admin",
        password: str = "",
        device_type: str = TYPE_CAMERA,
        session: aiohttp.ClientSession | None = None,
        use_ssl: bool = True,
        stream_profile: str | None = None,
        rtsp_over_tls: bool | None = None,
    ) -> None:
        """Initialize the CP PLUS STQC client."""
        self.hass = hass
        self.host = host
        self.port = port
        self.rtsp_port = rtsp_port
        self.username = username
        self.password = password
        self.device_type = device_type
        self.use_ssl = use_ssl
        self.rtsp_over_tls = rtsp_over_tls if rtsp_over_tls is not None else (use_ssl if device_type == TYPE_CAMERA else False)
        self.stream_profile = stream_profile or (
            STREAM_PROFILE_VIDEO_LIVE if device_type == TYPE_CAMERA else STREAM_PROFILE_DAHUA_CH1
        )
        self._scheme = "https" if use_ssl else "http"
        self._external_session = session is not None
        self._session = session
        self._session_id: str | None = None
        self._logged_in = False
        self._keep_alive_interval = 30
        self._req_id = 1
        self._lock = asyncio.Lock()
        self._device_info: dict[str, Any] = {}
        self._digest_auth: AsyncDigestAuth | None = None
        self._event_auth: AsyncDigestAuth | None = None
        self._event_session: aiohttp.ClientSession | None = None
        self._channels: list[dict[str, Any]] = []
        self._stopped = False

    def _get_ssl_context(self) -> ssl.SSLContext:
        """Return SSL context that accepts self-signed camera certificates."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            ssl_ctx = self._get_ssl_context() if self.use_ssl else False
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    def _next_id(self) -> int:
        """Return the next RPC request ID."""
        self._req_id += 1
        return self._req_id

    async def async_login(self) -> bool:
        """Perform the challenge-response login over /cpapi2_Login."""
        async with self._lock:
            if self._logged_in and self._session_id:
                return True

            session = await self._get_session()
            login_url = f"{self._scheme}://{self.host}:{self.port}/cpapi2_Login"

            client_type = "Web3.0"
            req1 = {
                "method": "user.signin",
                "params": {
                    "userName": self.username,
                    "password": "",
                    "clientType": client_type,
                },
                "id": self._next_id(),
            }
            body1 = json.dumps(req1)
            etag1 = hashlib.sha256(body1.encode()).hexdigest()
            headers1 = {
                "Content-Type": "application/json",
                "ETag": etag1,
                "User-Agent": "Mozilla/5.0",
            }

            try:
                async with session.post(login_url, data=body1, headers=headers1) as resp:
                    if resp.status != 200:
                        raise ConnectionError(f"HTTP error {resp.status} during firstLogin")
                    data1 = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.error("Connection error contacting CP PLUS camera %s: %s", self.host, err)
                raise ConnectionError(f"Cannot connect to {self.host}:{self.port}") from err

            # If Web3.0 slot is busy on camera, fall back to Mobile clientType (concurrent session)
            if data1.get("error", {}).get("code") == 486:
                _LOGGER.debug("Camera %s Web3.0 slot busy (486), falling back to Mobile clientType", self.host)
                client_type = "Mobile"
                req1["params"]["clientType"] = "Mobile"
                req1["id"] = self._next_id()
                body1 = json.dumps(req1)
                headers1["ETag"] = hashlib.sha256(body1.encode()).hexdigest()
                try:
                    async with session.post(login_url, data=body1, headers=headers1) as retry_resp:
                        data1 = await retry_resp.json(content_type=None)
                except Exception:
                    pass

            params = data1.get("params", {})
            realm = params.get("realm", "")
            random_val = params.get("random", "")
            challenge_session = data1.get("session", "")
            encryption = params.get("encryption", "Default")

            if not realm or not random_val:
                if data1.get("error", {}).get("code") == 486:
                    raise CPPlusBusyError(f"Camera {self.host} RPC session is currently busy (486 Busy Here)")
                raise ConnectionError(f"Unexpected challenge response from {self.host}: {data1}")

            # Step 2: Compute uppercase double-MD5 response matching camera specification:
            # inner = MD5(user + ":" + realm + ":" + password).upper()
            # outer = MD5(user + ":" + random + ":" + inner).upper()
            inner_md5 = hashlib.md5(
                f"{self.username}:{realm}:{self.password}".encode()
            ).hexdigest().upper()
            auth_hash = hashlib.md5(
                f"{self.username}:{random_val}:{inner_md5}".encode()
            ).hexdigest().upper()

            # Step 3: Send authenticated credentials (secondLogin)
            req2 = {
                "method": "user.signin",
                "params": {
                    "userName": self.username,
                    "password": auth_hash,
                    "clientType": client_type,
                    "authorityType": encryption,
                    "loginType": "Direct",
                },
                "id": self._next_id(),
                "session": challenge_session,
            }
            body2 = json.dumps(req2)
            etag2 = hashlib.sha256(body2.encode()).hexdigest()
            headers2 = {
                "Content-Type": "application/json",
                "ETag": etag2,
                "User-Agent": "Mozilla/5.0",
            }

            try:
                async with session.post(login_url, data=body2, headers=headers2) as resp:
                    if resp.status != 200:
                        raise ConnectionError(f"HTTP error {resp.status} during secondLogin")
                    data2 = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise ConnectionError(f"Failed to post secondLogin to {self.host}") from err

            if not data2.get("result"):
                error_msg = data2.get("error", {}).get("message", "Invalid credentials")
                _LOGGER.warning("Authentication failed for %s on %s: %s", self.username, self.host, error_msg)
                raise CPPlusAuthError(f"Authentication failed: {error_msg}")

            self._session_id = data2.get("session") or challenge_session
            self._logged_in = True
            self._keep_alive_interval = data2.get("params", {}).get("keepAliveInterval", 30)
            _LOGGER.info("Successfully authenticated with CP PLUS STQC camera at %s", self.host)
            return True

    async def async_call_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dispatch a JSON-RPC command to /cpapi2."""
        if not self._logged_in or not self._session_id:
            await self.async_login()

        session = await self._get_session()
        rpc_url = f"https://{self.host}:{self.port}/cpapi2"

        req = {
            "method": method,
            "params": params,
            "id": self._next_id(),
            "session": self._session_id,
        }
        body = json.dumps(req)
        etag = hashlib.sha256(body.encode()).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "ETag": etag,
            "User-Agent": "Mozilla/5.0",
        }

        try:
            async with session.post(rpc_url, data=body, headers=headers) as resp:
                if resp.status != 200:
                    raise ConnectionError(f"RPC HTTP error {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ConnectionError(f"RPC call {method} to {self.host} failed") from err

        # If session expired, attempt re-login once (268632064 = session timeout, 268632065 = session invalid)
        _LOGGER.debug("RPC %s response from %s: %s", method, self.host, data)
        if not data.get("result") and data.get("error", {}).get("code") in [268632064, 268632065]:
            _LOGGER.debug("Session expired on %s, attempting re-authentication", self.host)
            self._logged_in = False
            await self.async_login()
            req["session"] = self._session_id
            body = json.dumps(req)
            headers["ETag"] = hashlib.sha256(body.encode()).hexdigest()
            async with session.post(rpc_url, data=body, headers=headers) as retry_resp:
                return await retry_resp.json(content_type=None)

        return data

    async def async_detect_device_type(self) -> str:
        """Detect whether the target host is an NVR or a standalone camera."""
        session = await self._get_session()
        nvr_url = f"{self._scheme}://{self.host}:{self.port}/cgi-bin/magicBox.cgi?action=getDeviceType"
        try:
            async with session.get(nvr_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 401 and "Digest" in resp.headers.get("WWW-Authenticate", ""):
                    self.device_type = TYPE_NVR
                    self._digest_auth = AsyncDigestAuth(self.username, self.password)
                    self._digest_auth.parse_challenge(resp.headers["WWW-Authenticate"])
                    _LOGGER.info("Detected CP PLUS STQC NVR at %s", self.host)
                    return TYPE_NVR
        except Exception as err:
            _LOGGER.debug("NVR probe error on %s: %s", self.host, err)

        self.device_type = TYPE_CAMERA
        _LOGGER.info("Detected CP PLUS STQC standalone camera at %s", self.host)
        return TYPE_CAMERA

    async def async_ping(self) -> bool:
        """Lightweight check that the camera HTTP/HTTPS interface is responsive."""
        session = await self._get_session()
        url = f"{self._scheme}://{self.host}:{self.port}/"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CPPlusConnectionError(f"Cannot connect to camera at {self.host}:{self.port}") from err

    async def async_cgi_request(self, uri: str, method: str = "GET", data: Any = None) -> str:
        """Execute an authenticated HTTP Digest request against device CGI."""
        if not self._digest_auth:
            self._digest_auth = AsyncDigestAuth(self.username, self.password)
        session = await self._get_session()
        url = f"{self._scheme}://{self.host}:{self.port}{uri}"

        headers = {"User-Agent": "Mozilla/5.0"}
        if self._digest_auth.realm and self._digest_auth.nonce:
            headers["Authorization"] = self._digest_auth.build_header(method, uri)

        async with session.request(method, url, headers=headers, data=data) as resp:
            if resp.status == 401:
                auth_hdr = resp.headers.get("WWW-Authenticate", "")
                if "Digest" in auth_hdr:
                    self._digest_auth.parse_challenge(auth_hdr)
                    headers["Authorization"] = self._digest_auth.build_header(method, uri)
                    async with session.request(method, url, headers=headers, data=data) as retry_resp:
                        if retry_resp.status == 401:
                            raise CPPlusAuthError(f"Authentication failed on {self.host}")
                        if retry_resp.status != 200:
                            raise ConnectionError(f"HTTP error {retry_resp.status} on {uri}")
                        return await retry_resp.text()
                raise CPPlusAuthError(f"401 missing Digest challenge on {self.host}")
            elif resp.status == 200:
                return await resp.text()
            raise ConnectionError(f"HTTP error {resp.status} on {uri}")

    async def async_nvr_request(self, uri: str, method: str = "GET", data: Any = None) -> str:
        """Backward-compatible alias for async_cgi_request."""
        return await self.async_cgi_request(uri, method, data)

    async def async_cgi_request_bytes(self, uri: str, method: str = "GET", data: Any = None) -> bytes:
        """Execute an authenticated HTTP Digest request returning binary bytes."""
        if not self._digest_auth:
            self._digest_auth = AsyncDigestAuth(self.username, self.password)
        session = await self._get_session()
        url = f"{self._scheme}://{self.host}:{self.port}{uri}"

        headers = {"User-Agent": "Mozilla/5.0"}
        if self._digest_auth.realm and self._digest_auth.nonce:
            headers["Authorization"] = self._digest_auth.build_header(method, uri)

        async with session.request(method, url, headers=headers, data=data) as resp:
            if resp.status == 401:
                auth_hdr = resp.headers.get("WWW-Authenticate", "")
                if "Digest" in auth_hdr:
                    self._digest_auth.parse_challenge(auth_hdr)
                    headers["Authorization"] = self._digest_auth.build_header(method, uri)
                    async with session.request(method, url, headers=headers, data=data) as retry_resp:
                        if retry_resp.status == 401:
                            raise CPPlusAuthError(f"Authentication failed on {self.host}")
                        if retry_resp.status != 200:
                            raise ConnectionError(f"HTTP error {retry_resp.status} on {uri}")
                        return await retry_resp.read()
                raise CPPlusAuthError(f"401 missing Digest challenge on {self.host}")
            elif resp.status == 200:
                return await resp.read()
            raise ConnectionError(f"HTTP error {resp.status} on {uri}")

    async def async_nvr_request_bytes(self, uri: str, method: str = "GET", data: Any = None) -> bytes:
        """Backward-compatible alias for async_cgi_request_bytes."""
        return await self.async_cgi_request_bytes(uri, method, data)

    async def async_get_channels(self) -> list[dict[str, Any]]:
        """Query NVR or standalone camera for channels, camera names, and AI detection capabilities."""
        if self.device_type == TYPE_CAMERA:
            cgi_supported = False
            camera_name = self.host
            try:
                title_res = await self.async_cgi_request("/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle")
                cgi_supported = True
                for line in title_res.splitlines():
                    m = re.match(r"table\.ChannelTitle\[0\]\.Name=(.*)", line.strip())
                    if m and m.group(1).strip():
                        camera_name = m.group(1).strip()
            except Exception as err:
                _LOGGER.debug("Could not query ChannelTitle on camera %s: %s", self.host, err)

            smd_info: dict[str, bool] = {}
            try:
                smd_res = await self.async_cgi_request("/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect")
                cgi_supported = True
                for line in smd_res.splitlines():
                    m_en = re.match(r"table\.SmartMotionDetect\[0\]\.Enable=(true|false)", line.strip(), re.I)
                    if m_en:
                        smd_info["enable"] = m_en.group(1).lower() == "true"
                    m_hum = re.match(r"table\.SmartMotionDetect\[0\]\.(?:ObjectTypes\.)?Human(?:Detection)?=(true|false)", line.strip(), re.I)
                    if m_hum:
                        smd_info["human"] = m_hum.group(1).lower() == "true"
                    m_veh = re.match(r"table\.SmartMotionDetect\[0\]\.(?:ObjectTypes\.)?Vehicle(?:Detection)?=(true|false)", line.strip(), re.I)
                    if m_veh:
                        smd_info["vehicle"] = m_veh.group(1).lower() == "true"
            except Exception as err:
                _LOGGER.debug("Could not query SmartMotionDetect on camera %s: %s", self.host, err)

            tripwire_enabled = False
            has_tripwire = False
            try:
                trip_res = await self.async_cgi_request("/cgi-bin/configManager.cgi?action=getConfig&name=CrossLineDetection")
                cgi_supported = True
                for line in trip_res.splitlines():
                    m_trip = re.match(r"table\.CrossLineDetection\[0\]\.Enable=(true|false)", line.strip(), re.I)
                    if m_trip:
                        has_tripwire = True
                        tripwire_enabled = m_trip.group(1).lower() == "true"
            except Exception as err:
                _LOGGER.debug("Could not query CrossLineDetection on camera %s: %s", self.host, err)

            video_mode = 0
            try:
                vim_res = await self.async_cgi_request("/cgi-bin/configManager.cgi?action=getConfig&name=VideoInMode")
                cgi_supported = True
                for line in vim_res.splitlines():
                    m_vim = re.match(r"table\.VideoInMode\[0\]\.Mode=(\d+)", line.strip())
                    if m_vim:
                        video_mode = int(m_vim.group(1))
            except Exception as err:
                _LOGGER.debug("Could not query VideoInMode on camera %s: %s", self.host, err)

            lighting_mode = "Auto"
            try:
                light_res = await self.async_cgi_request("/cgi-bin/configManager.cgi?action=getConfig&name=Lighting")
                cgi_supported = True
                for line in light_res.splitlines():
                    m_light = re.match(r"table\.Lighting\[0\]\[0\]\.Mode=([a-zA-Z0-9]+)", line.strip())
                    if m_light:
                        lighting_mode = m_light.group(1)
            except Exception as err:
                _LOGGER.debug("Could not query Lighting on camera %s: %s", self.host, err)

            audio_enable = True
            try:
                enc_res = await self.async_cgi_request("/cgi-bin/configManager.cgi?action=getConfig&name=Encode")
                cgi_supported = True
                for line in enc_res.splitlines():
                    m_aud = re.match(
                        r"table\.Encode\[0\]\.MainFormat\[0\]\.(?:Audio\.Enable|AudioEnable)=(true|false)",
                        line.strip(),
                        re.I,
                    )
                    if m_aud:
                        audio_enable = m_aud.group(1).lower() == "true"
            except Exception as err:
                _LOGGER.debug("Could not query Encode on camera %s: %s", self.host, err)

            # JSON-RPC fallback for STQC standalone cameras where CGI endpoints are disabled
            if not cgi_supported:
                if not smd_info:
                    try:
                        res = await self.async_call_rpc("configManager.getConfig", {"name": "SmartMotionDetect"})
                        if res.get("result"):
                            table = res.get("params", {}).get("table", [])
                            item = table[0] if isinstance(table, list) and table else (table if isinstance(table, dict) else {})
                            if "Enable" in item:
                                smd_info["enable"] = bool(item["Enable"])
                            obj = item.get("ObjectTypes", item)
                            if "Human" in obj or "HumanDetection" in obj:
                                smd_info["human"] = bool(obj.get("Human", obj.get("HumanDetection")))
                            if "Vehicle" in obj or "VehicleDetection" in obj:
                                smd_info["vehicle"] = bool(obj.get("Vehicle", obj.get("VehicleDetection")))
                    except Exception as err:
                        _LOGGER.debug("RPC query for SmartMotionDetect failed on %s: %s", self.host, err)

                if not has_tripwire:
                    try:
                        res = await self.async_call_rpc("configManager.getConfig", {"name": "CrossLineDetection"})
                        if res.get("result"):
                            table = res.get("params", {}).get("table", [])
                            item = table[0] if isinstance(table, list) and table else (table if isinstance(table, dict) else {})
                            if "Enable" in item:
                                has_tripwire = True
                                tripwire_enabled = bool(item["Enable"])
                    except Exception as err:
                        _LOGGER.debug("RPC query for CrossLineDetection failed on %s: %s", self.host, err)

                if not has_tripwire:
                    try:
                        res = await self.async_call_rpc("configManager.getConfig", {"name": "VideoAnalyseRule"})
                        if res.get("result"):
                            table = res.get("params", {}).get("table", [])
                            item = table[0] if isinstance(table, list) and table else (table if isinstance(table, dict) else {})
                            rules = item.get("Rules", item.get("Rule", [])) if isinstance(item, dict) else []
                            if rules:
                                has_tripwire = any(
                                    r.get("RuleType") in ("CrossLine", "CrossRegion", "LineDetection")
                                    for r in rules if isinstance(r, dict)
                                )
                                tripwire_enabled = any(
                                    bool(r.get("Enable", True))
                                    for r in rules if isinstance(r, dict) and r.get("RuleType") in ("CrossLine", "CrossRegion", "LineDetection")
                                )
                    except Exception as err:
                        _LOGGER.debug("RPC query for VideoAnalyseRule failed on %s: %s", self.host, err)

                if video_mode == 0:
                    try:
                        res = await self.async_call_rpc("configManager.getConfig", {"name": "VideoInMode"})
                        if res.get("result"):
                            table = res.get("params", {}).get("table", [])
                            item = table[0] if isinstance(table, list) and table else (table if isinstance(table, dict) else {})
                            if "Mode" in item:
                                video_mode = int(item["Mode"])
                    except Exception as err:
                        _LOGGER.debug("RPC query for VideoInMode failed on %s: %s", self.host, err)

                if lighting_mode == "Auto":
                    try:
                        res = await self.async_call_rpc("configManager.getConfig", {"name": "Lighting"})
                        if res.get("result"):
                            table = res.get("params", {}).get("table", [])
                            item = table[0] if isinstance(table, list) and table else (table if isinstance(table, dict) else {})
                            if isinstance(item, list) and item:
                                item = item[0]
                            if isinstance(item, dict) and "Mode" in item:
                                lighting_mode = str(item["Mode"])
                    except Exception as err:
                        _LOGGER.debug("RPC query for Lighting failed on %s: %s", self.host, err)

                try:
                    res = await self.async_call_rpc("configManager.getConfig", {"name": "Encode"})
                    if res.get("result"):
                        table = res.get("params", {}).get("table", [])
                        item = table[0] if isinstance(table, list) and table else (table if isinstance(table, dict) else {})
                        fmt = item.get("MainFormat", [{}])[0] if isinstance(item.get("MainFormat"), list) else {}
                        if "AudioEnable" in fmt or "Audio" in fmt:
                            audio_enable = bool(fmt.get("AudioEnable", fmt.get("Audio", {}).get("Enable", True)))
                except Exception as err:
                    _LOGGER.debug("RPC query for Encode failed on %s: %s", self.host, err)

                if camera_name == self.host:
                    try:
                        res = await self.async_call_rpc("configManager.getConfig", {"name": "ChannelTitle"})
                        if res.get("result"):
                            table = res.get("params", {}).get("table", [])
                            item = table[0] if isinstance(table, list) and table else (table if isinstance(table, dict) else {})
                            if "Name" in item and item["Name"]:
                                camera_name = str(item["Name"])
                    except Exception as err:
                        _LOGGER.debug("RPC query for ChannelTitle failed on %s: %s", self.host, err)

            model = self._device_info.get("hardware") or "CP-UNC-TA21L3C-Q"
            serial_no = self._device_info.get("serial") or ""
            firmware_ver = self._device_info.get("firmware") or ""

            # Hardware capability profile: honor actual detected capabilities
            is_ai_cam = any(k in model.upper() for k in ("TA21", "AI", "SMD"))
            has_smd = smd_info.get("enable", bool(smd_info))
            has_tripwire = bool(has_tripwire)

            self._channels = [{
                "index": 0,
                "channel": 1,
                "name": camera_name,
                "model": model,
                "manufacturer": "CP PLUS",
                "is_native_cpplus": True,
                "serial": serial_no,
                "firmware": firmware_ver,
                "address": self.host,
                "http_port": "80",
                "https_port": str(self.port),
                "vendor": "CPPLUS",
                "has_smd": has_smd,
                "has_tripwire": has_tripwire,
                "smd_human": smd_info.get("human", True if (has_smd and is_ai_cam) else False),
                "smd_vehicle": smd_info.get("vehicle", True if (has_smd and is_ai_cam) else False),
                "tripwire": tripwire_enabled,
                "video_in_mode": video_mode,
                "lighting_mode": lighting_mode,
                "audio_enable": audio_enable,
            }]
            return self._channels

        title_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle")
        title_map: dict[int, str] = {}
        for line in title_res.splitlines():
            m = re.match(r"table\.ChannelTitle\[(\d+)\]\.Name=(.*)", line.strip())
            if m:
                title_map[int(m.group(1))] = m.group(2).strip()

        smd_map: dict[int, dict[str, bool]] = {}
        try:
            smd_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect")
            for line in smd_res.splitlines():
                m_en = re.match(r"table\.SmartMotionDetect\[(\d+)\]\.Enable=(true|false)", line.strip(), re.I)
                if m_en:
                    idx = int(m_en.group(1))
                    smd_map.setdefault(idx, {})["enable"] = m_en.group(2).lower() == "true"
                m_hum = re.match(r"table\.SmartMotionDetect\[(\d+)\]\.ObjectTypes\.Human=(true|false)", line.strip(), re.I)
                if m_hum:
                    idx = int(m_hum.group(1))
                    smd_map.setdefault(idx, {})["human"] = m_hum.group(2).lower() == "true"
                m_veh = re.match(r"table\.SmartMotionDetect\[(\d+)\]\.ObjectTypes\.Vehicle=(true|false)", line.strip(), re.I)
                if m_veh:
                    idx = int(m_veh.group(1))
                    smd_map.setdefault(idx, {})["vehicle"] = m_veh.group(2).lower() == "true"
        except Exception as err:
            _LOGGER.debug("Could not query SmartMotionDetect: %s", err)

        tripwire_map: dict[int, bool] = {}
        try:
            trip_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=CrossLineDetection")
            for line in trip_res.splitlines():
                m_trip = re.match(r"table\.CrossLineDetection\[(\d+)\]\.Enable=(true|false)", line.strip(), re.I)
                if m_trip:
                    tripwire_map[int(m_trip.group(1))] = m_trip.group(2).lower() == "true"
        except Exception as err:
            _LOGGER.debug("Could not query CrossLineDetection: %s", err)

        remote_devices: dict[int, dict[str, str]] = {}
        try:
            rd_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=RemoteDevice")
            for line in rd_res.splitlines():
                m_rd = re.match(r"table\.RemoteDevice(?:\.uuid:System_CONFIG_NETCAMERA_INFO_)?(?:\[)?(\d+)(?:\])?\.(.*?)=(.*)", line.strip())
                if m_rd:
                    idx = int(m_rd.group(1))
                    prop = m_rd.group(2)
                    val = m_rd.group(3).strip()
                    remote_devices.setdefault(idx, {})[prop] = val
        except CPPlusAuthError:
            raise
        except Exception as err:
            _LOGGER.warning("Could not query RemoteDevice on %s: %s", self.host, err)
            raise CPPlusConnectionError(f"Failed to query RemoteDevice on {self.host}: {err}") from err

        video_mode_map: dict[int, int] = {}
        try:
            vim_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=VideoInMode")
            for line in vim_res.splitlines():
                m_vim = re.match(r"table\.VideoInMode\[(\d+)\]\.Mode=(\d+)", line.strip())
                if m_vim:
                    video_mode_map[int(m_vim.group(1))] = int(m_vim.group(2))
        except Exception as err:
            _LOGGER.debug("Could not query VideoInMode: %s", err)

        lighting_map: dict[int, str] = {}
        try:
            light_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=Lighting")
            for line in light_res.splitlines():
                m_light = re.match(r"table\.Lighting\[(\d+)\]\[0\]\.Mode=([a-zA-Z0-9]+)", line.strip())
                if m_light:
                    lighting_map[int(m_light.group(1))] = m_light.group(2)
        except Exception as err:
            _LOGGER.debug("Could not query Lighting: %s", err)

        audio_map: dict[int, bool] = {}
        try:
            enc_res = await self.async_nvr_request("/cgi-bin/configManager.cgi?action=getConfig&name=Encode")
            for line in enc_res.splitlines():
                m_aud = re.match(
                    r"table\.Encode\[(\d+)\]\.MainFormat\[0\]\.(?:Audio\.Enable|AudioEnable)=(true|false)",
                    line.strip(),
                    re.I,
                )
                if m_aud:
                    audio_map[int(m_aud.group(1))] = m_aud.group(2).lower() == "true"
        except Exception as err:
            _LOGGER.debug("Could not query Encode audio: %s", err)

        channels: list[dict[str, Any]] = []
        for idx, name in sorted(title_map.items()):
            rd_info = remote_devices.get(idx, {})
            addr = rd_info.get("Address", "")
            is_configured = bool(addr and addr != "0.0.0.0" and rd_info.get("Enable", "true").lower() != "false")
            is_custom_name = not bool(re.match(r"^(?:Channel|CAM|D)\s*\d+$", name, re.I))

            # Exclude unconfigured phantom slots (no configured IP address and default factory title)
            if not name or not (is_configured or is_custom_name):
                continue

            smd_info = smd_map.get(idx, {})
            raw_model = rd_info.get("DeviceType")
            serial_no = rd_info.get("SerialNo")
            firmware_ver = rd_info.get("Version")
            http_port = rd_info.get("HttpPort")
            https_port = rd_info.get("HttpsPort")
            vendor = rd_info.get("Vendor")
            protocol = rd_info.get("Protocol")

            # Determine human-friendly and accurate model string & manufacturer
            if raw_model and raw_model != "Default":
                model = raw_model
            elif vendor == "CPPLUS" or protocol == "CPPLUS":
                model = "CP PLUS Camera"
            else:
                model = "Camera"

            # Brand and Native CP PLUS classification
            if model.startswith("CP-") or vendor == "CPPLUS" or protocol == "CPPLUS":
                manufacturer = "CP PLUS"
                is_native_cpplus = True
            elif model.startswith("VTO"):
                manufacturer = "Dahua"
                is_native_cpplus = False
            elif model.startswith("IPC_GK"):
                manufacturer = "Xiongmai"
                is_native_cpplus = False
            elif vendor:
                manufacturer = vendor
                is_native_cpplus = False
            else:
                manufacturer = "Generic ONVIF"
                is_native_cpplus = False

            channels.append({
                "index": idx,
                "channel": idx + 1,
                "name": name,
                "model": model,
                "manufacturer": manufacturer,
                "is_native_cpplus": is_native_cpplus,
                "serial": serial_no,
                "firmware": firmware_ver,
                "address": addr,
                "http_port": http_port,
                "https_port": https_port,
                "vendor": vendor,
                "has_smd": idx in smd_map,
                "has_tripwire": idx in tripwire_map,
                "smd_human": smd_info.get("human", False) and smd_info.get("enable", False),
                "smd_vehicle": smd_info.get("vehicle", False) and smd_info.get("enable", False),
                "tripwire": tripwire_map.get(idx, False),
                "video_in_mode": video_mode_map.get(idx, 0),
                "lighting_mode": lighting_map.get(idx, "Auto"),
                "audio_enable": audio_map.get(idx, True),
            })

        self._channels = channels
        return channels

    async def async_start_event_listener(
        self,
        callback: Callable[[int, str, str], None],
        on_auth_failed: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        """Connect to eventManager.cgi and dispatch real-time events to callback."""
        if self._event_auth is None:
            self._event_auth = AsyncDigestAuth(self.username, self.password)

        if self._event_session is None or self._event_session.closed:
            ssl_ctx = self._get_ssl_context() if self.use_ssl else False
            self._event_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl_ctx),
                timeout=aiohttp.ClientTimeout(total=None, sock_read=90),
            )
        session = self._event_session
        uri = "/cgi-bin/eventManager.cgi?action=attach&codes=[All]"
        url = f"{self._scheme}://{self.host}:{self.port}{uri}"

        consecutive_401 = 0
        reconnect_delay = 2.0

        while not self._stopped:
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                if self._event_auth.realm and self._event_auth.nonce:
                    headers["Authorization"] = self._event_auth.build_header("GET", uri)

                async with session.get(url, headers=headers) as resp:
                    if resp.status == 401:
                        consecutive_401 += 1
                        if consecutive_401 >= 2:
                            _LOGGER.error(
                                "Event stream authentication failed with consecutive 401s on %s. Halting stream.",
                                self.host,
                            )
                            if on_auth_failed:
                                try:
                                    on_auth_failed()
                                except Exception:
                                    pass
                            raise CPPlusAuthError(f"Authentication failed on event stream {self.host}")

                        auth_hdr = resp.headers.get("WWW-Authenticate", "")
                        if "Digest" in auth_hdr:
                            self._event_auth.parse_challenge(auth_hdr)
                            await asyncio.sleep(0.1)
                            continue
                        raise CPPlusAuthError(f"Event stream 401 missing Digest challenge on {self.host}")

                    if resp.status == 404:
                        _LOGGER.info(
                            "Event stream endpoint not supported on %s (HTTP 404). Halting event stream listener.",
                            self.host,
                        )
                        return

                    if resp.status != 200:
                        _LOGGER.warning("Event stream HTTP %s on %s, reconnecting...", resp.status, self.host)
                        if on_disconnect:
                            try:
                                on_disconnect()
                            except Exception:
                                pass
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, 60.0)
                        continue

                    # Reset consecutive 401 counter and backoff upon successful connection
                    consecutive_401 = 0
                    reconnect_delay = 2.0

                    buffer = ""
                    async for chunk in resp.content.iter_chunked(1024):
                        if self._stopped:
                            break
                        buffer += chunk.decode(errors="ignore")
                        if len(buffer) > 65536:
                            buffer = buffer[-32768:]

                        while "\n\n" in buffer or "\r\n\r\n" in buffer:
                            parts = re.split(r"\r?\n\r?\n", buffer, maxsplit=1)
                            event_block = parts[0]
                            buffer = parts[1] if len(parts) > 1 else ""

                            code_m = re.search(r"Code=([a-zA-Z0-9]+)", event_block)
                            action_m = re.search(r"action=([a-zA-Z0-9]+)", event_block)
                            index_m = re.search(r"index=(\d+)", event_block)

                            if code_m and action_m and index_m:
                                code = code_m.group(1)
                                action = action_m.group(1)
                                ch_idx = int(index_m.group(1))
                                try:
                                    callback(ch_idx, code, action)
                                except Exception as cb_err:
                                    _LOGGER.warning("Error in event callback for channel %d: %s", ch_idx, cb_err)

                    # Stream closed or broke
                    if on_disconnect:
                        try:
                            on_disconnect()
                        except Exception:
                            pass
            except CPPlusAuthError:
                if on_disconnect:
                    try:
                        on_disconnect()
                    except Exception:
                        pass
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.debug("Event stream reconnecting (%s)", err)
                if on_disconnect:
                    try:
                        on_disconnect()
                    except Exception:
                        pass
                await asyncio.sleep(3)
            except Exception as err:
                _LOGGER.error("Unexpected error in event listener: %s", err)
                if on_disconnect:
                    try:
                        on_disconnect()
                    except Exception:
                        pass
                await asyncio.sleep(5)

    async def async_get_device_info(self, fallback_serial: str | None = None) -> dict[str, Any]:
        """Fetch device model, machine name, and serial number."""
        if not self.device_type:
            await self.async_detect_device_type()

        if self.device_type == TYPE_NVR:
            model = "CP PLUS STQC NVR"
            serial = ""
            firmware = "Unknown"

            try:
                dev_res = await self.async_cgi_request("/cgi-bin/magicBox.cgi?action=getDeviceType")
                for line in dev_res.splitlines():
                    if line.startswith("type="):
                        model = line.split("=", 1)[1].strip()
            except CPPlusAuthError:
                raise
            except Exception as err:
                _LOGGER.debug("NVR getDeviceType error on %s: %s", self.host, err)

            try:
                sys_res = await self.async_cgi_request("/cgi-bin/magicBox.cgi?action=getSystemInfo")
                for line in sys_res.splitlines():
                    if line.startswith("serialNumber="):
                        serial = line.split("=", 1)[1].strip()
                    elif line.startswith("appVersion="):
                        firmware = line.split("=", 1)[1].strip()
            except CPPlusAuthError:
                raise
            except Exception as err:
                _LOGGER.debug("NVR getSystemInfo error on %s: %s", self.host, err)
                raise CPPlusConnectionError(f"Failed to communicate with NVR at {self.host}: {err}") from err

            if not serial:
                raise CPPlusError(f"Failed to retrieve valid serial number from NVR at {self.host}")

            if firmware == "Unknown":
                try:
                    ver_res = await self.async_cgi_request("/cgi-bin/magicBox.cgi?action=getSoftwareVersion")
                    for line in ver_res.splitlines():
                        if line.startswith("version="):
                            firmware = line.split("=", 1)[1].strip()
                except CPPlusAuthError:
                    raise
                except Exception as err:
                    _LOGGER.debug("NVR getSoftwareVersion error on %s: %s", self.host, err)

            self._device_info = {
                "serial": serial,
                "hardware": model,
                "firmware": firmware,
                "device_type": TYPE_NVR,
            }
            return self._device_info

        # Standalone Camera flow
        model = "CP PLUS STQC IPC"
        serial = ""
        firmware = "Unknown"

        # 1. Query standard CGI endpoints via HTTP Digest
        try:
            dev_res = await self.async_cgi_request("/cgi-bin/magicBox.cgi?action=getDeviceType")
            for line in dev_res.splitlines():
                if line.startswith("type="):
                    model = line.split("=", 1)[1].strip()
        except CPPlusAuthError:
            raise
        except Exception as err:
            _LOGGER.debug("Camera getDeviceType error on %s: %s", self.host, err)

        try:
            sys_res = await self.async_cgi_request("/cgi-bin/magicBox.cgi?action=getSystemInfo")
            for line in sys_res.splitlines():
                if line.startswith("serialNumber="):
                    serial = line.split("=", 1)[1].strip()
                elif line.startswith("appVersion="):
                    firmware = line.split("=", 1)[1].strip()
        except CPPlusAuthError:
            raise
        except Exception as err:
            _LOGGER.debug("Camera getSystemInfo error on %s: %s", self.host, err)

        if firmware == "Unknown":
            try:
                ver_res = await self.async_cgi_request("/cgi-bin/magicBox.cgi?action=getSoftwareVersion")
                for line in ver_res.splitlines():
                    if line.startswith("version="):
                        firmware = line.split("=", 1)[1].strip()
            except CPPlusAuthError:
                raise
            except Exception as err:
                _LOGGER.debug("Camera getSoftwareVersion error on %s: %s", self.host, err)

        # 2. If serial number, model, or firmware is missing/default, fallback to JSON-RPC /cpapi2
        if not serial or firmware == "Unknown" or model == "CP PLUS STQC IPC":
            if not self._logged_in:
                try:
                    await self.async_login()
                except Exception as err:
                    _LOGGER.debug("RPC login fallback failed on %s: %s", self.host, err)

            if self._logged_in:
                # Query device model via RPC
                if model == "CP PLUS STQC IPC":
                    try:
                        res = await self.async_call_rpc("magicBox.getDeviceType")
                        if res.get("result"):
                            p = res.get("params", {})
                            if "type" in p and p["type"]:
                                model = p["type"]
                    except Exception:
                        pass

                # Query serial number
                if not serial:
                    try:
                        res = await self.async_call_rpc("magicBox.getSerialNo")
                        if res.get("result") and "serial" in res.get("params", {}):
                            serial = res["params"]["serial"]
                    except Exception:
                        pass

                # Query system info (contains model, serial, and software version on STQC)
                try:
                    res = await self.async_call_rpc("magicBox.getSystemInfo")
                    if res.get("result"):
                        p = res.get("params", {})
                        info = p.get("info", p)
                        if not serial:
                            serial = info.get("serialNumber") or info.get("serial") or ""
                        if model == "CP PLUS STQC IPC":
                            model = info.get("deviceType") or info.get("type") or model
                        if firmware == "Unknown":
                            v = info.get("appVersion") or info.get("softwareVersion") or info.get("version") or info.get("Version")
                            b = info.get("buildDate") or info.get("build") or info.get("buildTime") or info.get("BuildDate")
                            if v and v != "Unknown":
                                firmware = f"{v} (Build: {b})" if b else str(v)
                except Exception:
                    pass

                if not serial:
                    try:
                        res = await self.async_call_rpc("configManager.getConfig", {"name": "General"})
                        if res.get("result"):
                            table = res.get("params", {}).get("table", {})
                            machine_name = table.get("MachineName")
                            if machine_name:
                                serial = machine_name
                    except Exception:
                        pass

                # Query software version via dedicated version endpoints
                if firmware == "Unknown":
                    for method in ("magicBox.getSoftwareVersion", "system.getVersion", "magicBox.getSystemInfo"):
                        try:
                            res = await self.async_call_rpc(method)
                            if res.get("result"):
                                p = res.get("params", {})
                                info_dict = p.get("info", p) if isinstance(p.get("info"), dict) else p
                                v = None
                                b = None
                                if isinstance(info_dict, dict):
                                    for k, val in info_dict.items():
                                        k_lower = k.lower()
                                        if k_lower in ("version", "softwareversion", "appversion", "sysversion", "firmwareversion") and val and val != "Unknown":
                                            v = str(val)
                                        elif k_lower in ("builddate", "build", "buildtime") and val:
                                            b = str(val)
                                if v:
                                    firmware = f"{v} (Build: {b})" if b else str(v)
                                    break
                        except Exception:
                            pass

                if firmware == "Unknown":
                    for cfg_name in ("SoftwareVersion", "Version", "General"):
                        try:
                            res = await self.async_call_rpc("configManager.getConfig", {"name": cfg_name})
                            if res.get("result"):
                                table = res.get("params", {}).get("table", {})
                                if isinstance(table, dict):
                                    v = table.get("Version") or table.get("SoftwareVersion") or table.get("version")
                                    b = table.get("BuildDate") or table.get("Build") or table.get("buildDate")
                                    if v and v != "Unknown":
                                        firmware = f"{v} (Build: {b})" if b else str(v)
                                        break
                        except Exception:
                            pass

        # Cleanly format firmware string if raw dict or dict string was returned
        if isinstance(firmware, dict):
            ver = firmware.get("Version") or firmware.get("version") or firmware.get("softwareVersion") or "Unknown"
            bdate = firmware.get("BuildDate") or firmware.get("buildDate") or firmware.get("build")
            firmware = f"{ver} (Build: {bdate})" if bdate else str(ver)
        elif isinstance(firmware, str) and ("Version" in firmware or "softwareVersion" in firmware) and "{" in firmware:
            try:
                import ast
                parsed = ast.literal_eval(firmware)
                if isinstance(parsed, dict):
                    ver = parsed.get("Version") or parsed.get("version") or parsed.get("softwareVersion") or "Unknown"
                    bdate = parsed.get("BuildDate") or parsed.get("buildDate") or parsed.get("build")
                    firmware = f"{ver} (Build: {bdate})" if bdate else str(ver)
            except Exception:
                pass

        if not serial and fallback_serial and not fallback_serial.replace(".", "").isdigit():
            serial = fallback_serial

        if not serial:
            raise CPPlusError(f"Failed to retrieve serial number from camera at {self.host}")

        if model == "CP PLUS STQC IPC":
            model = "CP-UNC-TA21L3C-Q"

        self._device_info = {
            "serial": serial,
            "hardware": model,
            "firmware": firmware,
            "device_type": TYPE_CAMERA,
        }
        return self._device_info

    @staticmethod
    def _is_ok_response(res: str) -> bool:
        """Check if CGI response indicates success without substring false positives."""
        if not res:
            return False
        return res.strip().upper().startswith("OK")

    async def async_reboot(self) -> bool:
        """Send reboot command to CP PLUS camera or NVR."""
        if self.device_type == TYPE_NVR:
            try:
                res = await self.async_cgi_request("/cgi-bin/magicBox.cgi?action=reboot")
                return self._is_ok_response(res) or res.strip().lower() == "success"
            except Exception as err:
                _LOGGER.error("Failed to reboot NVR at %s: %s", self.host, err)
                return False

        try:
            res = await self.async_cgi_request("/cgi-bin/magicBox.cgi?action=reboot")
            if self._is_ok_response(res) or res.strip().lower() == "success":
                return True
        except Exception:
            pass

        try:
            res = await self.async_call_rpc("magicBox.reboot")
            return bool(res.get("result"))
        except Exception as err:
            _LOGGER.error("Failed to reboot CP PLUS camera at %s: %s", self.host, err)
            return False

    def get_stream_url(
        self,
        channel: int = 1,
        subtype: int = 0,
        stream_profile: str | None = None,
        use_tls: bool | None = None,
        host_override: str | None = None,
        port_override: int | None = None,
        username_override: str | None = None,
        password_override: str | None = None,
    ) -> str:
        """Return the RTSP / RTSPS stream URL for the requested channel and stream type."""
        # Subtype 0 = Main Stream (HD), 1 = Sub Stream (SD)
        # Use safe RFC 3986 sub-delims so FFmpeg Digest Auth does not receive corrupted %XX password
        raw_user = username_override if username_override is not None else self.username
        raw_pass = password_override if password_override is not None else self.password
        username = urllib.parse.quote(raw_user, safe="!$&'()*+,-._~")
        password = urllib.parse.quote(raw_pass, safe="!$&'()*+,-._~")

        target_host = host_override or self.host
        target_port = port_override or self.rtsp_port

        profile = stream_profile or getattr(self, "stream_profile", None)
        if not profile:
            profile = STREAM_PROFILE_VIDEO_LIVE if self.device_type == TYPE_CAMERA else STREAM_PROFILE_DAHUA_CH1

        is_tls = use_tls if use_tls is not None else getattr(self, "rtsp_over_tls", False)
        scheme = "rtsps" if is_tls else "rtsp"

        if profile == STREAM_PROFILE_VIDEO_LIVE:
            return (
                f"{scheme}://{username}:{password}@{target_host}:{target_port}"
                f"/video/live?channel={channel}&subtype={subtype}"
            )
        elif profile == STREAM_PROFILE_ONVIF:
            onvif_path = f"onvif{subtype + 1}"
            return f"{scheme}://{username}:{password}@{target_host}:{target_port}/{onvif_path}"
        elif profile == STREAM_PROFILE_LIVE:
            return f"{scheme}://{username}:{password}@{target_host}:{target_port}/live"
        elif profile == STREAM_PROFILE_DAHUA_CH0:
            return (
                f"{scheme}://{username}:{password}@{target_host}:{target_port}"
                f"/cam/realmonitor?channel=0&subtype={subtype}"
            )
        else:
            return (
                f"{scheme}://{username}:{password}@{target_host}:{target_port}"
                f"/cam/realmonitor?channel={channel}&subtype={subtype}"
            )

    async def async_get_snapshot(
        self,
        channel: int = 1,
        host_override: str | None = None,
        port_override: int | None = None,
        username_override: str | None = None,
        password_override: str | None = None,
    ) -> bytes | None:
        """Fetch a snapshot JPEG image from camera or NVR using Digest authentication."""
        if host_override:
            scheme = "https" if (port_override == 443) else "http"
            port = port_override or 80
            user = username_override or self.username
            pwd = password_override or self.password
            digest = AsyncDigestAuth(user, pwd)
            session = await self._get_session()

            urls_to_try = [
                f"/cgi-bin/snapshot.cgi?channel=1",
                f"/cgi-bin/snapshot.cgi?channel=0",
                f"/cgi-bin/snapshot.cgi",
            ]
            for uri in urls_to_try:
                try:
                    url = f"{scheme}://{host_override}:{port}{uri}"
                    headers = {"User-Agent": "Mozilla/5.0"}
                    if digest.realm and digest.nonce:
                        headers["Authorization"] = digest.build_header("GET", uri)
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        if resp.status == 401:
                            auth_hdr = resp.headers.get("WWW-Authenticate", "")
                            if "Digest" in auth_hdr:
                                digest.parse_challenge(auth_hdr)
                                headers["Authorization"] = digest.build_header("GET", uri)
                                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as retry_resp:
                                    if retry_resp.status == 200:
                                        data = await retry_resp.read()
                                        if data and len(data) > 100:
                                            return data
                        elif resp.status == 200:
                            data = await resp.read()
                            if data and len(data) > 100:
                                return data
                except Exception as err:
                    _LOGGER.debug("Direct snapshot failed for uri %s on %s: %s", uri, host_override, err)
            return None

        urls_to_try = [
            f"/cgi-bin/snapshot.cgi?channel={channel}",
            f"/cgi-bin/snapshot.cgi",
            f"/cgi-bin/snapshot.cgi?channel=0",
            f"/onvif/snapshot",
        ] if self.device_type == TYPE_CAMERA else [
            f"/cgi-bin/snapshot.cgi?channel={channel}",
            f"/cgi-bin/snapshot.cgi",
        ]

        for uri in urls_to_try:
            try:
                data = await self.async_cgi_request_bytes(uri)
                if data and len(data) > 100:
                    return data
            except Exception as err:
                _LOGGER.debug("Snapshot failed for uri %s on %s: %s", uri, self.host, err)
        return None

    async def async_set_video_in_mode(self, channel_idx: int, mode: int) -> bool:
        """Set VideoInMode (Day/Night) for a channel: 0=Color, 1=Auto, 2=Black & White."""
        uri = f"/cgi-bin/configManager.cgi?action=setConfig&VideoInMode[{channel_idx}].Mode={mode}"
        try:
            res = await self.async_cgi_request(uri)
            if self._is_ok_response(res):
                return True
        except Exception:
            pass

        if self.device_type == TYPE_CAMERA:
            try:
                res_rpc = await self.async_call_rpc("configManager.setConfig", {"name": "VideoInMode", "table": [{"Mode": mode}]})
                return bool(res_rpc.get("result"))
            except Exception as err:
                _LOGGER.error("Failed to set VideoInMode via RPC on camera %s: %s", self.host, err)
        return False

    async def async_set_lighting_mode(self, channel_idx: int, mode: str) -> bool:
        """Set Lighting mode for a channel: Auto, Manual, Off."""
        uri = f"/cgi-bin/configManager.cgi?action=setConfig&Lighting[{channel_idx}][0].Mode={mode}"
        try:
            res = await self.async_cgi_request(uri)
            if self._is_ok_response(res):
                return True
        except Exception:
            pass

        if self.device_type == TYPE_CAMERA:
            try:
                res_rpc = await self.async_call_rpc("configManager.setConfig", {"name": "Lighting", "table": [[{"Mode": mode}]]})
                return bool(res_rpc.get("result"))
            except Exception as err:
                _LOGGER.error("Failed to set Lighting mode via RPC on camera %s: %s", self.host, err)
        return False

    async def async_set_smd_human(self, channel_idx: int, enable: bool) -> bool:
        """Enable or disable SmartMotionDetect Human recognition on a channel."""
        en_str = "true" if enable else "false"
        uri = (
            f"/cgi-bin/configManager.cgi?action=setConfig"
            f"&SmartMotionDetect[{channel_idx}].Enable=true"
            f"&SmartMotionDetect[{channel_idx}].ObjectTypes.Human={en_str}"
        )
        try:
            res = await self.async_cgi_request(uri)
            if self._is_ok_response(res):
                return True
        except Exception:
            pass

        if self.device_type == TYPE_CAMERA:
            try:
                res_rpc = await self.async_call_rpc(
                    "configManager.setConfig",
                    {"name": "SmartMotionDetect", "table": [{"Enable": True, "ObjectTypes": {"Human": enable}}]},
                )
                return bool(res_rpc.get("result"))
            except Exception as err:
                _LOGGER.error("Failed to set SMD Human via RPC on camera %s: %s", self.host, err)
        return False

    async def async_set_smd_vehicle(self, channel_idx: int, enable: bool) -> bool:
        """Enable or disable SmartMotionDetect Vehicle recognition on a channel."""
        en_str = "true" if enable else "false"
        uri = (
            f"/cgi-bin/configManager.cgi?action=setConfig"
            f"&SmartMotionDetect[{channel_idx}].Enable=true"
            f"&SmartMotionDetect[{channel_idx}].ObjectTypes.Vehicle={en_str}"
        )
        try:
            res = await self.async_cgi_request(uri)
            if self._is_ok_response(res):
                return True
        except Exception:
            pass

        if self.device_type == TYPE_CAMERA:
            try:
                res_rpc = await self.async_call_rpc(
                    "configManager.setConfig",
                    {"name": "SmartMotionDetect", "table": [{"Enable": True, "ObjectTypes": {"Vehicle": enable}}]},
                )
                return bool(res_rpc.get("result"))
            except Exception as err:
                _LOGGER.error("Failed to set SMD Vehicle via RPC on camera %s: %s", self.host, err)
        return False

    async def async_set_tripwire(self, channel_idx: int, enable: bool) -> bool:
        """Enable or disable CrossLineDetection (Tripwire) on a channel."""
        en_str = "true" if enable else "false"
        uri = f"/cgi-bin/configManager.cgi?action=setConfig&CrossLineDetection[{channel_idx}].Enable={en_str}"
        try:
            res = await self.async_cgi_request(uri)
            if self._is_ok_response(res):
                return True
        except Exception:
            pass

        if self.device_type == TYPE_CAMERA:
            try:
                res_rpc = await self.async_call_rpc(
                    "configManager.setConfig",
                    {"name": "CrossLineDetection", "table": [{"Enable": enable}]},
                )
                return bool(res_rpc.get("result"))
            except Exception as err:
                _LOGGER.error("Failed to set CrossLineDetection via RPC on camera %s: %s", self.host, err)
        return False

    async def async_set_audio_enable(self, channel_idx: int, enable: bool) -> bool:
        """Enable or disable audio transmission on channel RTSP stream."""
        val = "true" if enable else "false"
        uri = (
            f"/cgi-bin/configManager.cgi?action=setConfig"
            f"&table.Encode[{channel_idx}].MainFormat[0].AudioEnable={val}"
            f"&table.Encode[{channel_idx}].ExtraFormat[0].AudioEnable={val}"
        )
        try:
            res = await self.async_cgi_request(uri)
            if self._is_ok_response(res):
                return True
            # Fallback without 'table.' prefix if firmware prefers Encode[x]
            fallback_uri = (
                f"/cgi-bin/configManager.cgi?action=setConfig"
                f"&Encode[{channel_idx}].MainFormat[0].AudioEnable={val}"
                f"&Encode[{channel_idx}].ExtraFormat[0].AudioEnable={val}"
            )
            res2 = await self.async_cgi_request(fallback_uri)
            if self._is_ok_response(res2):
                return True
        except Exception:
            pass

        if self.device_type == TYPE_CAMERA:
            try:
                res_rpc = await self.async_call_rpc(
                    "configManager.setConfig",
                    {"name": "Encode", "table": [{"MainFormat": [{"AudioEnable": enable}]}]},
                )
                return bool(res_rpc.get("result"))
            except Exception as err:
                _LOGGER.error("Failed to set audio enable via RPC on camera %s: %s", self.host, err)
        return False

    async def async_ptz_control(
        self,
        channel: int,
        code: str,
        arg1: int = 0,
        arg2: int = 5,
        arg3: int = 0,
        stop: bool = False,
    ) -> bool:
        """Send PTZ movement or zoom command to camera channel."""
        if self.device_type != TYPE_NVR:
            return False
        action = "stop" if stop else "start"
        uri = (
            f"/cgi-bin/ptz.cgi?action={action}"
            f"&channel={channel}&code={code}&arg1={arg1}&arg2={arg2}&arg3={arg3}"
        )
        try:
            res = await self.async_nvr_request(uri)
            return self._is_ok_response(res)
        except Exception as err:
            _LOGGER.error("PTZ control failed on channel %d (%s): %s", channel, code, err)
            return False

    async def async_ptz_preset(self, channel: int, preset: int) -> bool:
        """Command PTZ to move to configured preset number."""
        if self.device_type != TYPE_NVR:
            return False
        uri = f"/cgi-bin/ptz.cgi?action=start&channel={channel}&code=GotoPreset&arg1=0&arg2={preset}&arg3=0"
        try:
            res = await self.async_nvr_request(uri)
            return self._is_ok_response(res)
        except Exception as err:
            _LOGGER.error("PTZ preset %d failed on channel %d: %s", preset, channel, err)
            return False

    async def async_close(self) -> None:
        """Close background connections and sessions."""
        self._stopped = True
        if self._logged_in and self.device_type == TYPE_CAMERA:
            try:
                await self.async_call_rpc("global.logout")
            except Exception:
                pass
            try:
                session = await self._get_session()
                logout_url = f"{self._scheme}://{self.host}:{self.port}/cpapi2_Login"
                req = {
                    "method": "user.signout",
                    "params": {},
                    "id": self._next_id(),
                    "session": self._session_id,
                }
                body = json.dumps(req)
                headers = {
                    "Content-Type": "application/json",
                    "ETag": hashlib.sha256(body.encode()).hexdigest(),
                    "User-Agent": "Mozilla/5.0",
                }
                async with session.post(logout_url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=2)):
                    pass
            except Exception:
                pass
            self._logged_in = False
            self._session_id = None

        if self._event_session and not self._event_session.closed:
            await self._event_session.close()
        if not self._external_session and self._session and not self._session.closed:
            await self._session.close()

    async def close(self) -> None:
        """Alias for async_close."""
        await self.async_close()




